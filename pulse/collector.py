"""Incremental JSONL reader. Never opens Codex files for writing.

Only reduced metadata is cached. Prompts, messages, commands, credentials and
tool outputs are discarded. Private cache files must never enter the repository.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
          "output_tokens", "reasoning_output_tokens", "total_tokens")
INTERNAL = {"guardian_review", "memory_extraction", "memory_consolidation"}
CACHE_VERSION = 3
TERMINAL = {"task_complete", "turn_aborted", "task_failed"}
ACTIVITY_EVENTS = {"item_started", "item_completed", "agent_message", "agent_reasoning",
                   "agent_message_delta", "agent_reasoning_delta",
                   "plan_update", "plan_updated", "exec_command_begin", "exec_command_end",
                   "exec_command_output_delta", "mcp_tool_call_begin", "mcp_tool_call_end",
                   "web_search_begin", "web_search_end"}
ACTIVITY_ITEMS = {"reasoning", "agent_message", "function_call", "function_call_output",
                  "custom_tool_call", "custom_tool_call_output", "web_search_call",
                  "image_generation_call", "local_shell_call", "computer_call",
                  "computer_call_output"}


def timestamp(value, fallback=0.0):
    if isinstance(value, (float, int)):
        return value / 1000 if value > 100_000_000_000 else float(value)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError):
        return fallback


def usage(value):
    value = value if isinstance(value, dict) else {}
    result = {key: max(0, int(value.get(key, 0) or 0)) for key in FIELDS}
    if "total_tokens" not in value:
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


def add_usage(target, value):
    for key in FIELDS:
        target[key] += value[key]


def valid_plan(plan):
    return (isinstance(plan, list) and 0 < len(plan) <= 200
            and all(isinstance(s, dict) and isinstance(s.get("step"), str)
                    and bool(s["step"].strip()) and s.get("status") in
                    ("pending", "in_progress", "inProgress", "completed") for s in plan))


def progress(plans):
    if not plans or not all(valid_plan(p) for p in plans):
        return {"known": False, "completed": None, "total": None, "percent": None}
    total = sum(len(p) for p in plans)
    done = sum(s["status"] == "completed" for p in plans for s in p)
    return {"known": True, "completed": done, "total": total,
            "percent": round(done / total * 100)}


def union_seconds(intervals):
    end = None
    total = 0.0
    for start, stop in sorted(intervals):
        if stop <= start:
            continue
        total += max(0, stop - max(start, end if end is not None else start))
        end = max(stop, end if end is not None else stop)
    return total


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
    os.replace(temp, path)


class Rollout:
    def __init__(self, thread_id, cached=None):
        self.data = cached or {"id": thread_id, "offset": 0, "inode": None,
                              "turns": {}, "responses": {}, "legacy": {},
                              "ignored_turns": [],
                              "current": None, "model": "unknown", "parent": None,
                              "last_total": None, "warnings": 0, "created": 0}

    def turn(self, turn_id, ts):
        return self.data["turns"].setdefault(turn_id, {
            "id": turn_id, "start": None, "end": None, "last": ts,
            "status": "unknown", "plan": None, "model": self.data["model"],
            "waiting_call": None, "superseded": False})

    def ingest(self, row):
        d = self.data
        p = row.get("payload", {})
        if not isinstance(p, dict):
            return
        ts = timestamp(row.get("timestamp"))
        kind = row.get("type")
        if kind == "session_meta":
            if (p.get("id") or p.get("session_id")) == d["id"]:
                d["created"] = timestamp(p.get("timestamp"), ts)
                d["parent"] = p.get("parent_thread_id")
                source = p.get("source")
                if isinstance(source, dict):
                    spawn = source.get("subagent", {}).get("thread_spawn", {})
                    if isinstance(spawn, dict):
                        d["parent"] = spawn.get("parent_thread_id", d["parent"])
            return
        # Forked history predates this session and does not represent new work.
        if ts and ts < d["created"]:
            return
        event = p.get("type") if kind == "event_msg" else None
        turn_id = p.get("turn_id") or d["current"]
        # Fork exports may rebase the row timestamp while preserving the original
        # lifecycle times. Compare at lifecycle precision (whole epoch seconds).
        if (event in TERMINAL | {"task_started"} and p.get("started_at") is not None
                and timestamp(p["started_at"]) < int(d["created"])):
            if turn_id and turn_id not in d["ignored_turns"]:
                d["ignored_turns"].append(turn_id)
            if event == "task_started" or d["current"] == turn_id:
                d["current"] = None
            return
        if turn_id in d["ignored_turns"]:
            return
        if kind == "turn_context":
            d["current"] = p.get("turn_id", d["current"])
            d["model"] = p.get("model", d["model"])
            if d["current"] in d["turns"]:
                d["turns"][d["current"]]["model"] = d["model"]
            return
        if kind == "token_usage_record":
            if p.get("thread_id", d["id"]) != d["id"] or not p.get("response_id"):
                return
            if p["response_id"] in d["responses"]:
                return
            d["responses"][p["response_id"]] = {
                "thread": d["id"], "turn": p.get("turn_id", d["current"]),
                "ts": ts, "model": d["model"], "usage": usage(p.get("usage")),
                "source": "response"}
        info = p.get("info") if isinstance(p.get("info"), dict) else {}
        counter = usage(info["total_token_usage"]) if info.get("total_token_usage") else None
        if event == "token_count" and counter is None:
            return
        token_activity = (event == "token_count" and counter is not None
                          and counter["total_tokens"] > (d["last_total"] or usage({}))["total_tokens"])
        activity = (kind == "token_usage_record" or event in ACTIVITY_EVENTS or token_activity
                    or kind == "response_item" and (p.get("type") in ACTIVITY_ITEMS
                        or p.get("type") == "message" and p.get("role") == "assistant"))
        if not activity and event not in TERMINAL | {"task_started", "token_count"}:
            return  # Settings, user input and other metadata are not execution.
        if event == "task_started":
            turn_id = p.get("turn_id") or "legacy-" + str(ts)
            # An unmatched preceding start is incomplete, never a fabricated end.
            for previous_id, previous in d["turns"].items():
                if previous_id != turn_id and previous["end"] is None:
                    previous["superseded"] = True
            d["current"] = turn_id
            t = self.turn(turn_id, ts)
            if t["end"] is None:
                t.update(start=timestamp(p.get("started_at"), ts), status="running")
        if not turn_id:
            return
        t = self.turn(turn_id, ts)
        if ((activity or event == "task_started") and t["end"] is None
                and not t["superseded"]) or event in TERMINAL:
            t["last"] = max(ts, t["last"])
        if event in TERMINAL:
            t["end"] = timestamp(p.get("completed_at"), ts)
            if p.get("started_at"):
                t["start"] = timestamp(p["started_at"])
            t["status"] = {"task_complete": "ended", "turn_aborted": "interrupted",
                           "task_failed": "error"}[event]
            t["waiting_call"] = None
        if event in ("plan_update", "plan_updated"):
            t["plan"] = p.get("plan") if valid_plan(p.get("plan")) else None
        if kind == "response_item":
            name = p.get("name", "").split(".")[-1]
            if name == "update_plan":
                try:
                    args = p.get("arguments", p.get("input", {}))
                    args = json.loads(args) if isinstance(args, str) else args
                    t["plan"] = args.get("plan") if valid_plan(args.get("plan")) else None
                except (ValueError, TypeError, AttributeError):
                    t["plan"] = None
            if name in ("request_user_input", "request_user_input_async"):
                t["waiting_call"] = p.get("call_id")
                t["status"] = "waiting"
            if p.get("type") in ("function_call_output", "custom_tool_call_output"):
                if t["waiting_call"] and p.get("call_id") == t["waiting_call"]:
                    t["waiting_call"] = None
                    t["status"] = "running"
        if event == "token_count":
            info = p.get("info") or {}
            if not isinstance(info, dict) or not info.get("total_token_usage"):
                return
            current = usage(info["total_token_usage"])
            previous = d["last_total"]
            if previous is None:
                delta = usage(info.get("last_token_usage") or current)
            elif any(current[k] < previous[k] for k in ("input_tokens", "output_tokens", "total_tokens")):
                d["warnings"] += 1
                delta = None  # Rebase; a reset is not a new billable response.
            else:
                delta = {k: max(0, current[k] - previous[k]) for k in FIELDS}
            d["last_total"] = current
            if delta and delta["total_tokens"]:
                key = hashlib.sha256(json.dumps([d["id"], turn_id, ts, current], sort_keys=True).encode()).hexdigest()
                d["legacy"][key] = {"thread": d["id"], "turn": turn_id, "ts": ts,
                                    "model": d["model"], "usage": delta, "source": "legacy"}

    def read(self, path):
        stat = path.stat()
        d = self.data
        if d["inode"] != stat.st_ino or stat.st_size < d["offset"]:
            created = d["created"]
            self.__init__(d["id"])
            d = self.data
            d["inode"] = stat.st_ino
            d["created"] = created
        changed = False
        with path.open("rb") as stream:
            stream.seek(d["offset"])
            while True:
                before = stream.tell()
                line = stream.readline()
                if not line or not line.endswith(b"\n"):
                    d["offset"] = before  # Retry partial writes on the next poll.
                    break
                try:
                    self.ingest(json.loads(line))
                except (ValueError, TypeError, KeyError, AttributeError):
                    d["warnings"] += 1
                changed = True
                d["offset"] = stream.tell()
        return changed


class Collector:
    def __init__(self, codex_home, data_dir, timezone, days=30):
        self.home, self.data_dir = Path(codex_home).resolve(), Path(data_dir).resolve()
        self.zone = ZoneInfo(timezone)
        self.days = days
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.files, self.meta = {}, {}
        self.status = {"phase": "loading", "indexed": 0, "total": 0,
                       "lastScan": None, "errors": [], "excluded": 0}
        self.saved = 0
        try:
            cached = json.loads((self.data_dir / "cache.json").read_text())
            if cached.get("version") == CACHE_VERSION and cached.get("home") == str(self.home):
                self.files = {k: Rollout(v["id"], v) for k, v in cached["files"].items()}
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def metadata(self):
        databases = sorted(self.home.glob("state_*.sqlite"),
                           key=lambda p: int(p.stem.split("_")[-1]), reverse=True)
        result, projects, roots, edges, assignments = {}, {}, [], {}, {}
        if not databases:
            raise RuntimeError("未找到 Codex 任务索引；请先在本机运行 Codex。")
        cutoff = datetime.now(self.zone).timestamp() - self.days * 86400
        with sqlite3.connect(databases[0].as_uri() + "?mode=ro", uri=True, timeout=2) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            cols = {r[1] for r in conn.execute("PRAGMA table_info(threads)")}
            wanted = [k for k in ("id", "rollout_path", "created_at", "updated_at", "source",
                      "thread_source", "cwd", "title", "name", "project_id", "archived") if k in cols]
            rows = conn.execute("SELECT " + ",".join(wanted) + " FROM threads WHERE updated_at >= ?", (cutoff,)).fetchall()
            try:
                projects = {r["id"]: r["name"] for r in conn.execute("SELECT id,name FROM projects")}
                roots = [(r["path"], r["project_id"]) for r in conn.execute("SELECT path,project_id FROM project_roots")]
            except sqlite3.OperationalError:
                pass
            try:
                edges = {r["child_thread_id"]: r["parent_thread_id"] for r in conn.execute("SELECT parent_thread_id,child_thread_id FROM thread_spawn_edges")}
            except sqlite3.OperationalError:
                pass
        # Compatibility with Desktop's earlier project catalog; read only known keys.
        try:
            state = json.loads((self.home / ".codex-global-state.json").read_text())
            for pid, p in state.get("local-projects", {}).items():
                if isinstance(p, dict):
                    projects.setdefault(pid, p.get("name", "项目"))
                    roots.extend((r, pid) for r in p.get("rootPaths", []) if isinstance(r, str))
            assignments = state.get("thread-project-assignments", {})
            projectless = set(state.get("projectless-thread-ids", []))
        except (OSError, ValueError, AttributeError, TypeError):
            projectless = set()
        excluded = 0
        roots.sort(key=lambda pair: len(pair[0]), reverse=True)
        for raw in rows:
            r = dict(raw)
            source = r.get("source", "")
            if r.get("thread_source") in INTERNAL or '"guardian"' in source or '"memory_' in source:
                excluded += 1
                continue
            cwd = str(Path(r.get("cwd") or self.home).resolve())
            pid = r.get("project_id") or assignments.get(r["id"])
            if not isinstance(pid, str):
                pid = None
            if not pid and r["id"] not in projectless:
                pid = next((p for root, p in roots if cwd == root or cwd.startswith(root.rstrip("/") + "/")), None)
            if not pid:
                if r["id"] in projectless:
                    pid = "unassigned"
                else:
                    pid = "path-" + hashlib.sha256(cwd.encode()).hexdigest()[:16]
            name = projects.get(pid, "未归属项目" if pid == "unassigned" else Path(cwd).name)
            r.update(title=(r.get("name") or r.get("title") or "未命名任务")[:240],
                     project_id=pid, project_name=name, cwd=cwd,
                     parent=edges.get(r["id"]),
                     child=r.get("thread_source") == "subagent" or r["id"] in edges or '"thread_spawn"' in source)
            result[r["id"]] = r
        return result, excluded

    def scan(self):
        meta, excluded = self.metadata()
        errors, changed = [], False
        self.status.update(total=len(meta), indexed=0, excluded=excluded)
        next_files = {}
        for index, (tid, row) in enumerate(meta.items()):
            path = Path(row["rollout_path"])
            # Do not let a corrupt index point this collector at arbitrary files.
            if not any(base == path.resolve() or base in path.resolve().parents
                       for base in (self.home / "sessions", self.home / "archived_sessions")):
                errors.append("任务日志路径超出 Codex 会话目录")
                continue
            key = str(path)
            roll = self.files.get(key, Rollout(tid))
            if not roll.data["created"]:
                roll.data["created"] = row.get("created_at", 0)
            try:
                changed = roll.read(path) or changed
                row["parent"] = (row["parent"] or roll.data["parent"]) if row["child"] else None
                next_files[key] = roll
            except OSError:
                errors.append("部分日志暂时不可读")
                if key in self.files:
                    next_files[key] = self.files[key]
            self.status["indexed"] = index + 1
        with self.lock:
            self.meta, self.files = meta, next_files
            self.status.update(phase="ready", errors=sorted(set(errors)), lastScan=time.time())
        if changed and time.time() - self.saved > 30:
            self.save()

    def save(self):
        atomic_json(self.data_dir / "cache.json", {"version": CACHE_VERSION, "home": str(self.home),
                    "files": {k: v.data for k, v in self.files.items()}})
        self.saved = time.time()

    def run(self):
        while not self.stop.is_set():
            try:
                # Synchronize incremental mutations with snapshot reads.
                with self.lock:
                    self.scan()
            except Exception as exc:
                self.status.update(phase="error", errors=[str(exc) if isinstance(exc, RuntimeError)
                                                          else "采集暂时不可用：" + type(exc).__name__])
            self.stop.wait(2)
        self.save()

    def snapshot(self, date, stale_seconds=300):
        day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=self.zone)
        now = time.time()
        start, stop = day.timestamp(), (day + timedelta(days=1)).timestamp()
        today = datetime.now(self.zone).date()
        if day.date() > today or day.date() < today - timedelta(days=self.days - 1):
            raise ValueError("日期超出当前采集范围")
        with self.lock:
            ledger, turns, warnings, meta = {}, {}, 0, self.meta
            modern_turns = set()
            for roll in self.files.values():
                d = roll.data
                warnings += d["warnings"]
                for key, r in d["responses"].items():
                    ledger.setdefault(key, r)
                    modern_turns.add((r["thread"], r["turn"]))
                for tid, t in d["turns"].items():
                    turns[(d["id"], tid)] = t
            for roll in self.files.values():
                for key, r in roll.data["legacy"].items():
                    if (r["thread"], r["turn"]) not in modern_turns:
                        ledger.setdefault("legacy-" + key, r)
            tasks = {}
            intervals, task_intervals, models, legacy_count = [], {}, {}, 0
            def task(tid):
                if tid not in meta:
                    return None
                if tid not in tasks:
                    m = meta[tid]
                    parent = m.get("parent")
                    seen = {tid}
                    while parent in meta and parent not in seen:
                        seen.add(parent)
                        parent_meta = meta[parent]
                        m = {**m, "project_id": parent_meta["project_id"], "project_name": parent_meta["project_name"]}
                        parent = parent_meta.get("parent")
                    tasks[tid] = {"id": tid, "title": m["title"], "projectId": m["project_id"],
                                  "projectName": m["project_name"], "cwd": m["cwd"],
                                  "child": m["child"], "parent": m.get("parent"),
                                  "usage": usage({}), "duration": 0, "turns": [], "incompleteTurns": 0,
                                  "status": "ended", "last": 0, "plan": None, "model": "unknown",
                                  "activeToday": False}
                return tasks[tid]
            for (tid, turn_id), t in turns.items():
                is_open = t["end"] is None and t["start"] is not None
                recent = is_open and not t["superseded"] and now - t["last"] <= stale_seconds
                live = recent and self.status["phase"] == "ready"
                status = t["status"] if not is_open or live else "unknown"
                # When unconfirmed, stop the provisional interval at last evidence.
                end = t["end"] if t["end"] is not None else (now if live else t["last"])
                begin = t["start"]
                overlaps = begin is not None and begin < stop and end > start
                event_today = start <= t["last"] < stop
                if not (overlaps or event_today or is_open and recent):
                    continue
                item = task(tid)
                if not item:
                    continue
                seconds = max(0, min(end, stop) - max(begin, start)) if overlaps else 0
                if overlaps:
                    interval = (max(begin, start), min(end, stop))
                    intervals.append(interval)
                    task_intervals.setdefault(tid, []).append(interval)
                timing = "complete" if begin is not None and t["end"] is not None else "live" if live else "incomplete"
                item["incompleteTurns"] += timing == "incomplete"
                item["duration"] += seconds
                item["activeToday"] = item["activeToday"] or overlaps or event_today
                item["turns"].append({"id": turn_id, "start": begin, "end": t["end"],
                                      "seconds": round(seconds), "status": status, "plan": t["plan"],
                                      "timing": timing, "observedEnd": end})
                if t["last"] >= item["last"]:
                    item.update(status=status, last=t["last"], plan=t["plan"], model=t["model"])
            for r in ledger.values():
                if start <= r["ts"] < stop:
                    item = task(r["thread"])
                    if not item:
                        continue
                    item["activeToday"] = True
                    add_usage(item["usage"], r["usage"])
                    add_usage(models.setdefault(r["model"], usage({})), r["usage"])
                    legacy_count += r["source"] == "legacy"
            projects, total = {}, usage({})
            for t in tasks.values():
                t["duration"] = round(t["duration"])
                t["wallTime"] = round(union_seconds(task_intervals.get(t["id"], [])))
                t["progress"] = progress([t["plan"]])
                t["turns"].sort(key=lambda x: x["start"] or 0, reverse=True)
                add_usage(total, t["usage"])
                p = projects.setdefault(t["projectId"], {"id": t["projectId"], "name": t["projectName"],
                    "tasks": [], "usage": usage({}), "duration": 0, "running": 0,
                    "waiting": 0, "unknown": 0, "incompleteTurns": 0, "activeToday": False})
                p["tasks"].append(t["id"])
                p["duration"] += t["duration"]
                p["incompleteTurns"] += t["incompleteTurns"]
                add_usage(p["usage"], t["usage"])
                p["activeToday"] = p["activeToday"] or t["activeToday"]
                for s in ("running", "waiting", "unknown"):
                    p[s] += t["status"] == s
            for p in projects.values():
                p["wallTime"] = round(union_seconds([interval for tid in p["tasks"]
                                                     for interval in task_intervals.get(tid, [])]))
                active = [tasks[i] for i in p["tasks"] if not tasks[i]["child"] and tasks[i]["status"] in ("running", "waiting", "error", "unknown")]
                p["progress"] = progress([t["plan"] if t["status"] != "unknown" else None for t in active])
            all_tasks = sorted(tasks.values(), key=lambda t: (t["status"] not in ("running", "waiting"), -t["last"]))
            visible_projects = sorted(projects.values(), key=lambda p: (-p["running"], -p["usage"]["total_tokens"]))
            return {"date": date, "today": today.isoformat(), "timezone": str(self.zone),
                "minDate": (today - timedelta(days=self.days - 1)).isoformat(), "generatedAt": now,
                "source": {**self.status, "mode": "log-observation", "days": self.days,
                           "warnings": warnings, "legacyResponses": legacy_count,
                           "codexHome": str(self.home), "dataDir": str(self.data_dir)},
                "metrics": {"projects": sum(p["activeToday"] and p["id"] != "unassigned" for p in projects.values()),
                    "runningProjects": sum(bool(p["running"]) and p["id"] != "unassigned" for p in projects.values()),
                    "mainTasks": sum(t["activeToday"] and not t["child"] for t in tasks.values()),
                    "childTasks": sum(t["activeToday"] and t["child"] for t in tasks.values()),
                    "duration": sum(t["duration"] for t in tasks.values()),
                    "incompleteTurns": sum(t["incompleteTurns"] for t in tasks.values()),
                    "wallTime": round(union_seconds(intervals)), "usage": total},
                "projects": visible_projects, "tasks": all_tasks,
                "models": [{"model": k, "usage": v} for k, v in sorted(models.items(), key=lambda kv: -kv[1]["total_tokens"])],
                "quota": {"available": False, "reason": "本机日志统计不包含可核实的账户剩余额度。"}}
