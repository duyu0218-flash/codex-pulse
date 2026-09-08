"""Loopback-only HTTP service; no control endpoints for Codex."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from . import __version__
from .collector import Collector, atomic_json
from .quota import QuotaMonitor

STATIC = Path(__file__).parent / "static"
DEFAULTS = {"refreshSeconds": 5, "staleSeconds": 300, "theme": "system"}


def validate_settings(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        raise ValueError("设置字段无效")
    if type(value["refreshSeconds"]) is not int or not 2 <= value["refreshSeconds"] <= 60:
        raise ValueError("刷新间隔需为 2–60 秒的整数")
    if type(value["staleSeconds"]) is not int or not 30 <= value["staleSeconds"] <= 1800:
        raise ValueError("状态确认窗口需为 30–1800 秒的整数")
    if value["theme"] not in ("system", "light", "dark"):
        raise ValueError("主题无效")
    return value


def report(snapshot):
    m, date = snapshot["metrics"], snapshot["date"]
    lines = ["# Codex Pulse 日报 · " + date, "",
             f"统计时区：{snapshot['timezone']}；范围：此设备可读的用户任务与子代理日志。",
             f"项目 {m['projects']} 个；主任务 {m['mainTasks']} 个；子代理 {m['childTasks']} 个。",
             f"当日运行时长 {m['wallTime']} 秒（并行重叠只计一次）；回合并行累计 {m['duration']} 秒。",
             f"记录不完整的回合 {m['incompleteTurns']} 个；缺少结束记录时只计至最后执行活动。",
             f"Token {m['usage']['total_tokens']:,}（输入 {m['usage']['input_tokens']:,}，输出 {m['usage']['output_tokens']:,}）。", "",
             "| 项目 | 运行秒数 | 并行累计秒数 | 不完整回合 | Token | 当前计划步骤 |", "|---|---:|---:|---:|---:|---|"]
    for p in snapshot["projects"]:
        if not p["activeToday"]:
            continue
        name = p["name"].replace("|", "\\|").replace("\n", " ")
        pr = p["progress"]
        step = f"{pr['completed']}/{pr['total']}" if pr["known"] else "进度未知或当前无运行任务"
        lines.append(f"| {name} | {p['wallTime']} | {p['duration']} | {p['incompleteTurns']} | {p['usage']['total_tokens']:,} | {step} |")
    lines += ["", "说明：进度表示当前计划步骤比例；回合结束不等于项目验收。",
              "缓存输入是输入子集，推理输出是输出子集；不重复相加。",
              "时长来自回合日志，含模型、工具与等待时间，不等于 CPU 或模型生成时间。",
              "不同项目可能并行，项目运行时长不可直接相加。设置变更不续计时，复制的历史回合不重复计时。",
              "状态来自日志观测，静默或断开后显示未确认；未确认区间的耗时只计至最后执行活动。",
              f"旧格式响应 {snapshot['source']['legacyResponses']} 条；解析/重置提示 {snapshot['source']['warnings']} 条。",
              "内部审批与记忆任务排除；当前账户额度请查看实时看板，不计入历史日报。"]
    return "\n".join(lines)


class PulseServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, collector, quota=None):
        self.collector = collector
        self.quota = quota or QuotaMonitor(collector.home, enabled=False)
        self.settings_lock = threading.Lock()
        self.settings_path = collector.data_dir / "settings.json"
        try:
            self.settings = validate_settings(json.loads(self.settings_path.read_text()))
        except (OSError, ValueError, TypeError, KeyError):
            self.settings = dict(DEFAULTS)
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "CodexPulse/" + __version__

    def log_message(self, fmt, *args):
        # Do not log URL parameters, project titles or task data.
        return

    def send(self, status, body, mime="application/json; charset=utf-8", extra=None):
        if not isinstance(body, bytes):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self' http://127.0.0.1:* http://localhost:*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def json(self, status, data):
        self.send(status, json.dumps(data, ensure_ascii=False))

    def allowed(self, write=False):
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in allowed:
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + host for host in allowed}:
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        if write and self.headers.get("X-Pulse-Request") != "1":
            return False
        return True

    def snapshot(self):
        date = parse_qs(urlsplit(self.path).query).get("date", [None])[0]
        if not date:
            date = datetime.now(self.server.collector.zone).date().isoformat()
        data = self.server.collector.snapshot(date, self.server.settings["staleSeconds"])
        data["settings"] = self.server.settings.copy()
        data["version"] = __version__
        data["quota"] = self.server.quota.snapshot()
        return data

    def do_GET(self):
        if not self.allowed():
            return self.json(403, {"error": "仅允许本机同源访问"})
        path = urlsplit(self.path).path
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8"),
                  "/favicon.svg": ("favicon.svg", "image/svg+xml")}
        try:
            if path in assets:
                file, mime = assets[path]
                return self.send(200, (STATIC / file).read_bytes(), mime)
            if path == "/api/health":
                return self.json(200, {"version": __version__, **self.server.collector.status})
            if path == "/api/settings":
                return self.json(200, self.server.settings)
            if path == "/api/quota":
                return self.json(200, self.server.quota.snapshot())
            if path == "/api/snapshot":
                return self.json(200, self.snapshot())
            if path == "/api/report":
                data = self.snapshot()
                return self.send(200, report(data), "text/markdown; charset=utf-8",
                                 {"Content-Disposition": 'attachment; filename="codex-pulse-' + data["date"] + '.md"'})
            self.json(404, {"error": "页面不存在"})
        except ValueError:
            self.json(400, {"error": "日期无效或超出采集范围"})
        except Exception:
            self.json(503, {"error": "数据暂时不可用，请稍后刷新"})

    def do_POST(self):
        if not self.allowed(write=True):
            return self.json(403, {"error": "仅允许本机同源操作"})
        if self.path == "/api/quota/refresh":
            accepted = self.server.quota.request_refresh()
            return self.json(202 if accepted else 200, {"accepted": accepted, "quota": self.server.quota.snapshot()})
        if self.path != "/api/settings":
            return self.json(404, {"error": "入口不存在"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 2048 or self.headers.get("Content-Type") != "application/json":
                raise ValueError("请求格式无效")
            settings = validate_settings(json.loads(self.rfile.read(length)))
            with self.server.settings_lock:
                atomic_json(self.server.settings_path, settings)
                self.server.settings = settings
            self.json(200, settings)
        except (ValueError, TypeError, KeyError) as exc:
            self.json(400, {"error": str(exc)})
        except OSError:
            self.json(503, {"error": "设置未保存，目录不可写"})


def main():
    parser = argparse.ArgumentParser(description="Codex Pulse — local dashboard")
    parser.add_argument("--port", type=int, default=43189)
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    parser.add_argument("--data-dir", type=Path, default=Path.home() / ".local/share/codex-pulse")
    parser.add_argument("--timezone", default=os.environ.get("TZ", "Asia/Shanghai"))
    parser.add_argument("--days", type=int, choices=range(1, 366), default=30, metavar="1..365")
    parser.add_argument("--codex-cli", default=os.environ.get("CODEX_PULSE_CLI"), help="Path to Codex CLI for account quota")
    parser.add_argument("--no-quota", action="store_true", help="Disable official account quota queries")
    args = parser.parse_args()
    try:
        ZoneInfo(args.timezone)
    except (ValueError, KeyError):
        parser.error("Invalid IANA timezone")
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be 1024..65535")
    args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    collector = Collector(args.codex_home, args.data_dir, args.timezone, args.days)
    quota = QuotaMonitor(args.codex_home, args.codex_cli, enabled=not args.no_quota)
    server = PulseServer(args.port, collector, quota)
    worker = threading.Thread(target=collector.run, daemon=True, name="collector")
    worker.start()
    quota.start()
    def shutdown(*_):
        collector.stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f"Codex Pulse {__version__} · http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        collector.stop.set()
        worker.join(timeout=10)
        quota.close()
        server.server_close()


if __name__ == "__main__":
    main()
