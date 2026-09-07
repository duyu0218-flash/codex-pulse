#!/usr/bin/env python3
"""Install this source as a per-user macOS LaunchAgent; never modifies Codex."""
import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

LABEL = "io.codex-pulse.dashboard"
ROOT = Path(__file__).resolve().parents[1]
BASE = Path.home() / "Library/Application Support/Codex Pulse"
PLIST = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
TARGET = "gui/" + str(os.getuid())


def launch(*args, check=True):
    return subprocess.run(["/bin/launchctl", *args], check=check, capture_output=True, text=True)


def config(args, release):
    return {"Label": LABEL,
            "ProgramArguments": [sys.executable, "-B", "-m", "pulse", "--port", str(args.port),
                                 "--data-dir", str(BASE / "data"), "--codex-home", str(args.codex_home),
                                 "--timezone", args.timezone, "--days", str(args.days)],
            "WorkingDirectory": str(release), "RunAtLoad": True, "KeepAlive": True,
            "ThrottleInterval": 10, "ProcessType": "Background",
            "StandardOutPath": str(BASE / "logs/stdout.log"),
            "StandardErrorPath": str(BASE / "logs/stderr.log")}


def install(args):
    digest = hashlib.sha256()
    files = sorted(p for p in (ROOT / "pulse").rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    for file in files:
        digest.update(str(file.relative_to(ROOT)).encode()); digest.update(file.read_bytes())
    release = BASE / "releases" / digest.hexdigest()[:16]
    plan = config(args, release)
    if args.action == "plan":
        print(json.dumps({"source": str(ROOT), "release": str(release), "launchAgent": str(PLIST),
                          "url": "http://127.0.0.1:" + str(args.port), "settings": plan}, indent=2))
        return
    for path in [release, BASE / "data", BASE / "logs", PLIST.parent]:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not (release / "pulse").exists():
        shutil.copytree(ROOT / "pulse", release / "pulse", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    old = PLIST.read_bytes() if PLIST.exists() else None
    if old:
        launch("bootout", TARGET, str(PLIST), check=False)
    temp = PLIST.with_suffix(".tmp")
    temp.write_bytes(plistlib.dumps(plan)); temp.chmod(0o600); os.replace(temp, PLIST)
    try:
        launch("bootstrap", TARGET, str(PLIST))
    except subprocess.CalledProcessError as exc:
        if old:
            PLIST.write_bytes(old)
            launch("bootstrap", TARGET, str(PLIST), check=False)
        else:
            PLIST.unlink(missing_ok=True)
        raise SystemExit("LaunchAgent could not start: " + exc.stderr.strip())
    print("Installed: http://127.0.0.1:" + str(args.port))
    print("LaunchAgent: " + str(PLIST))
    print("Private data: " + str(BASE / "data"))


def status():
    result = launch("print", TARGET + "/" + LABEL, check=False)
    if result.returncode:
        print("Service is not loaded"); return 1
    print("LaunchAgent is loaded")
    for line in result.stdout.splitlines():
        if line.strip().startswith(("state =", "pid =", "last exit code =")):
            print(line.strip())
    if PLIST.exists():
        cfg = plistlib.loads(PLIST.read_bytes())
        argv = cfg["ProgramArguments"]
        port = argv[argv.index("--port") + 1]
        try:
            with urllib.request.urlopen("http://127.0.0.1:" + port + "/api/health", timeout=10) as response:
                print(json.dumps(json.load(response), ensure_ascii=False))
        except OSError:
            print("HTTP endpoint is not ready"); return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "install", "status", "restart", "uninstall"])
    parser.add_argument("--port", type=int, default=43189)
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    args = parser.parse_args()
    args.codex_home = args.codex_home.expanduser().resolve()
    if sys.platform != "darwin":
        parser.error("LaunchAgent installation requires macOS; use python3 -m pulse elsewhere.")
    if args.action in ("plan", "install"):
        from zoneinfo import ZoneInfo
        ZoneInfo(args.timezone)
        if not 1024 <= args.port <= 65535 or not 1 <= args.days <= 365:
            parser.error("Invalid port or history window")
        install(args)
    elif args.action == "status":
        sys.exit(status())
    elif args.action == "restart":
        launch("kickstart", "-k", TARGET + "/" + LABEL)
        print("Service restart requested")
    else:
        launch("bootout", TARGET, str(PLIST), check=False)
        PLIST.unlink(missing_ok=True)
        print("Service removed. Private data and installed releases are preserved at " + str(BASE))


if __name__ == "__main__":
    main()
