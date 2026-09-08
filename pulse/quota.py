"""Read account quota through Codex's authenticated, read-only App Server RPCs."""
from __future__ import annotations

import copy
import json
import math
import os
import selectors
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import __version__

REFRESH_SECONDS = 60
MIN_REFRESH_SECONDS = 15
ERRORS = {
    "cli_missing": "未找到 Codex CLI，请安装 Codex 或通过 --codex-cli 指定路径。",
    "login_required": "账号登录不可用，请先在 Codex 中登录，再刷新额度。",
    "auth_unsupported": "当前 CLI 登录方式不提供 ChatGPT 订阅额度。",
    "cli_unsupported": "当前 Codex CLI 不支持额度接口，请更新 Codex。",
    "timeout": "官方额度查询超时，将自动重试。",
    "rate_limited": "官方额度查询过于频繁，稍后自动重试。",
    "unavailable": "官方接口暂未提供可用的额度数据。",
    "fetch_failed": "官方额度暂时读取失败，将自动重试。",
    "disabled": "账号额度连接已关闭（--no-quota）。",
}


class QuotaError(Exception):
    def __init__(self, code):
        self.code = code if code in ERRORS else "fetch_failed"
        super().__init__(ERRORS[self.code])


def discover_codex(override=None):
    """Resolve the CLI even with launchd's minimal PATH. Never invoke a shell."""
    candidates = [override] if override else [
        shutil.which("codex"),
        "/Applications/ChatGPT.app/Contents/Resources/codex",
        "/Applications/Codex.app/Contents/Resources/codex",
        Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex",
        Path.home() / "Applications/Codex.app/Contents/Resources/codex",
        Path.home() / ".local/bin/codex",
        "/opt/homebrew/bin/codex", "/usr/local/bin/codex",
    ]
    for value in candidates:
        if not value:
            continue
        path = Path(value).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
    raise QuotaError("cli_missing")


def number(value, minimum=None):
    if type(value) not in (int, float):
        return None
    try:
        if not math.isfinite(value):
            return None
    except OverflowError:
        return None
    return value if minimum is None or value >= minimum else None


def short_text(value):
    return value[:100] if isinstance(value, str) else None


def normalize_quota(response):
    """Whitelist display fields; account IDs, email and reset-credit IDs stay out."""
    if not isinstance(response, dict):
        raise QuotaError("unavailable")
    limits = response.get("rateLimitsByLimitId")
    if not isinstance(limits, dict) or not limits:
        legacy = response.get("rateLimits")
        limits = {legacy.get("limitId") or "codex": legacy} if isinstance(legacy, dict) else {}
    buckets = []
    for key, raw in sorted(limits.items(), key=lambda item: (item[0] != "codex", str(item[0]))):
        if not isinstance(raw, dict):
            continue
        windows = []
        for kind in ("primary", "secondary"):
            window = raw.get(kind)
            if not isinstance(window, dict):
                continue
            used = number(window.get("usedPercent"))
            if used is not None:
                used = min(100, max(0, used))
            resets_at = number(window.get("resetsAt"), 0)
            if resets_at is not None and resets_at >= 253402300800:
                resets_at = None
            windows.append({"kind": kind, "usedPercent": used,
                            "remainingPercent": None if used is None else round(100 - used, 2),
                            "windowDurationMins": number(window.get("windowDurationMins"), 1),
                            "resetsAt": resets_at})
        credits = raw.get("credits")
        if isinstance(credits, dict):
            balance = credits.get("balance")
            # A credit amount is not a currency price or a subscription percentage.
            if isinstance(balance, str):
                try:
                    valid_balance = number(float(balance), 0)
                except ValueError:
                    valid_balance = None
                balance = balance[:40] if valid_balance is not None and len(balance) <= 40 else None
            else:
                balance = number(balance, 0)
            credits = {"balance": balance,
                       "unlimited": credits.get("unlimited") if type(credits.get("unlimited")) is bool else None}
        else:
            credits = None
        buckets.append({"id": short_text(key), "name": short_text(raw.get("limitName")) or ("Codex" if key == "codex" else short_text(key)),
                        "planType": short_text(raw.get("planType")), "windows": windows, "credits": credits,
                        "spendControlReached": raw.get("spendControlReached") is True,
                        "limitReached": bool(raw.get("rateLimitReachedType"))})
    reset = response.get("rateLimitResetCredits")
    count = number(reset.get("availableCount"), 0) if isinstance(reset, dict) else None
    if count is not None and count != int(count):
        count = None
    if not buckets and count is None:
        raise QuotaError("unavailable")
    return {"buckets": buckets, "resetCreditsAvailable": count}


def rpc_error(error):
    """Never pass upstream errors, which may contain account details, to HTTP/logs."""
    if not isinstance(error, dict):
        return QuotaError("fetch_failed")
    message = str(error.get("message", "")).lower()
    if error.get("code") == -32601:
        return QuotaError("cli_unsupported")
    if any(word in message for word in ("unauthorized", "not logged", "401", "unauthenticated", "authentication required")):
        return QuotaError("login_required")
    if "429" in message or "too many requests" in message:
        return QuotaError("rate_limited")
    return QuotaError("fetch_failed")


def read_quota(codex_home, cli=None, timeout=25):
    """A short-lived CLI reloads the current login each time; no model turns start."""
    executable = discover_codex(cli)
    env = os.environ.copy()
    env["CODEX_HOME"] = str(Path(codex_home).expanduser().resolve())
    try:
        process = subprocess.Popen([executable, "app-server", "--listen", "stdio://"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   cwd=str(Path.home()), env=env, start_new_session=True)
    except OSError:
        raise QuotaError("cli_missing") from None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    pending = b""
    received = 0
    deadline = time.monotonic() + timeout

    def send(method, ident=None, params=None):
        message = {"method": method}
        if ident is not None:
            message["id"] = ident
        if params is not None:
            message["params"] = params
        process.stdin.write((json.dumps(message) + "\n").encode())
        process.stdin.flush()

    def receive(ident):
        nonlocal pending, received
        while time.monotonic() < deadline:
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                try:
                    message = json.loads(line)
                except ValueError:
                    raise QuotaError("fetch_failed") from None
                if not isinstance(message, dict):
                    raise QuotaError("fetch_failed")
                if message.get("id") != ident:
                    continue
                if "error" in message:
                    raise rpc_error(message["error"])
                result = message.get("result")
                if not isinstance(result, dict):
                    raise QuotaError("fetch_failed")
                return result
            if not selector.select(max(0, deadline - time.monotonic())):
                break
            chunk = os.read(process.stdout.fileno(), 65536)
            received += len(chunk)
            if not chunk or received > 1024 * 1024:
                raise QuotaError("fetch_failed")
            pending += chunk
        raise QuotaError("timeout")

    try:
        send("initialize", 0, {"clientInfo": {"name": "codex_pulse", "title": "Codex Pulse", "version": __version__}})
        receive(0)
        send("initialized", params={})
        send("account/read", 1, {"refreshToken": False})
        account = receive(1).get("account")
        if not account:
            raise QuotaError("login_required")
        if not isinstance(account, dict) or account.get("type") not in ("chatgpt", "chatgptAuthTokens", "agentIdentity", "personalAccessToken"):
            raise QuotaError("auth_unsupported")
        send("account/rateLimits/read", 2)
        return normalize_quota(receive(2))
    except (OSError, ValueError):
        raise QuotaError("fetch_failed") from None
    finally:
        selector.close()
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        process.stdin.close()
        process.stdout.close()


class QuotaMonitor:
    """Independent, bounded polling; snapshots and page refreshes never perform RPC."""
    def __init__(self, codex_home, cli=None, enabled=True, fetch=None,
                 refresh_seconds=REFRESH_SECONDS, min_refresh_seconds=MIN_REFRESH_SECONDS):
        self.enabled = enabled
        self.fetch = fetch or (lambda: read_quota(codex_home, cli))
        self.refresh_seconds = refresh_seconds
        self.min_refresh_seconds = min_refresh_seconds
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.worker = None
        self.value = None
        self.error = None if enabled else "disabled"
        self.updated_at = None
        self.attempt_at = None
        self.attempt_clock = None
        self.refreshing = False
        self.pending = enabled

    def start(self):
        if self.enabled and self.worker is None:
            self.worker = threading.Thread(target=self.run, daemon=True, name="account-quota")
            self.worker.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.worker:
            self.worker.join(timeout=30)

    def request_refresh(self):
        with self.lock:
            if not self.enabled or self.pending or self.refreshing:
                return False
            if self.attempt_clock is not None and time.monotonic() - self.attempt_clock < self.min_refresh_seconds:
                return False
            self.pending = True
            self.wake.set()
            return True

    def run(self):
        next_refresh = 0
        while not self.stop.is_set():
            self.wake.wait(max(0, next_refresh - time.monotonic()))
            self.wake.clear()
            if self.stop.is_set():
                return
            with self.lock:
                if not self.pending and time.monotonic() < next_refresh:
                    continue
                self.pending = False
                self.refreshing = True
                self.attempt_at = time.time()
                self.attempt_clock = time.monotonic()
            try:
                value = self.fetch()
                error = None
            except QuotaError as exc:
                value, error = None, exc.code
            except Exception:
                value, error = None, "fetch_failed"
            with self.lock:
                # Clear the previous account's values on failure (including logout).
                self.value, self.error = value, error
                if value is not None:
                    self.updated_at = time.time()
                self.refreshing = False
            next_refresh = time.monotonic() + self.refresh_seconds

    def snapshot(self):
        now = time.time()
        with self.lock:
            value = copy.deepcopy(self.value) if self.value else {"buckets": [], "resetCreditsAvailable": None}
            status = "ready" if self.value else "unavailable" if self.error else "loading"
            reason = ERRORS.get(self.error, "正在连接 Codex 账号…" if status == "loading" else "")
            if self.value and self.updated_at is not None and now - self.updated_at > self.refresh_seconds * 2:
                status, reason = "stale", "额度数据已过期，显示上次查询值，正在等待更新。"
            for bucket in value["buckets"]:
                for window in bucket["windows"]:
                    window["resetDue"] = window["resetsAt"] is not None and window["resetsAt"] <= now
                    if window["resetDue"] and status == "ready":
                        status, reason = "stale", "部分窗口已到重置时间，等待官方额度更新。"
            retry = max(0, math.ceil(self.min_refresh_seconds - (time.monotonic() - self.attempt_clock))) if self.attempt_clock is not None else 0
            return {**value, "available": self.value is not None, "status": status, "reason": reason,
                    "errorCode": self.error, "updatedAt": self.updated_at, "lastAttemptAt": self.attempt_at,
                    "refreshing": self.refreshing or self.pending, "enabled": self.enabled,
                    "refreshSeconds": self.refresh_seconds, "retryAfterSeconds": retry,
                    "source": "Codex account/rateLimits/read", "scope": "account"}
