import json
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pulse.collector import Collector, Rollout, progress, reported_plan, timestamp, union_seconds, usage


def event(kind, ts, **payload):
    return {"type": kind, "timestamp": ts, "payload": payload}


def plan_message(plan, role="assistant"):
    return {"type": "message", "role": role, "content": [{"type": "output_text", "text":
            "```codex-pulse-plan\n" + json.dumps({"version": 1, "plan": plan}) + "\n```"}]}


class ParserTests(unittest.TestCase):
    def test_tokens_do_not_double_count_subsets(self):
        u = usage({"input_tokens": 100, "cached_input_tokens": 80,
                   "output_tokens": 20, "reasoning_output_tokens": 10})
        self.assertEqual(u["total_tokens"], 120)

    def test_union_parallel_and_adjacent_intervals(self):
        self.assertEqual(union_seconds([(0, 60), (30, 90), (90, 120), (100, 100)]), 120)

    def test_weighted_steps_and_missing_plan(self):
        p = [{"step": "a", "status": "completed"}, {"step": "b", "status": "in_progress"}]
        self.assertEqual(progress([p, p])["percent"], 50)
        self.assertFalse(progress([p, None])["known"])
        self.assertFalse(progress([[{"step": "", "status": "completed"}]])["known"])

    def test_explicit_assistant_progress_and_native_event(self):
        r = Rollout("a")
        r.ingest(event("event_msg", 100, type="task_started", turn_id="t"))
        plan = [{"step": "implemented", "status": "completed"}, {"step": "verify", "status": "in_progress"}]
        r.ingest(event("response_item", 110, **plan_message(plan)))
        self.assertEqual(r.data["turns"]["t"]["plan"], plan)
        self.assertEqual(r.data["turns"]["t"]["plan_source"], "agent-report")
        self.assertEqual(r.data["turns"]["t"]["plan_updated_at"], 110)
        updated = [{"step": "new scope", "status": "inProgress"}]
        r.ingest(event("event_msg", 120, type="turn/plan/updated", turnId="t", plan=updated))
        self.assertEqual(r.data["turns"]["t"]["plan"], updated)
        self.assertEqual(r.data["turns"]["t"]["plan_source"], "codex-plan")
        r.ingest(event("response_item", 115, **plan_message(plan)))
        self.assertEqual(r.data["turns"]["t"]["plan"], updated)

    def test_plan_reports_ignore_user_tool_prose_and_extra_fields(self):
        plan = [{"step": "a", "status": "completed", "privateExtra": "DO-NOT-CACHE"}]
        found, reduced = reported_plan(plan_message(plan))
        self.assertTrue(found)
        self.assertEqual(reduced, [{"step": "a", "status": "completed"}])
        for role in ("user", "developer", "tool"):
            self.assertEqual(reported_plan(plan_message(plan, role)), (False, None))
        for text in ['已完成 90%，接下来测试', '```json\n{"plan": []}\n```']:
            self.assertEqual(reported_plan({"type":"message", "role":"assistant", "content":text}), (False, None))

    def test_invalid_report_clears_old_plan_and_new_turn_starts_unknown(self):
        r = Rollout("a")
        r.ingest(event("event_msg", 100, type="task_started", turn_id="t"))
        r.ingest(event("response_item", 110, **plan_message([{"step":"a", "status":"completed"}])))
        r.ingest(event("response_item", 120, **plan_message([{"step":"b", "status":"invented"}])))
        self.assertIsNone(r.data["turns"]["t"]["plan"])
        r.ingest(event("response_item", 130, **plan_message([{"step":"a", "status":"completed"}])))
        r.ingest(event("event_msg", 140, type="task_complete", turn_id="t"))
        r.ingest(event("event_msg", 150, type="task_started", turn_id="new"))
        self.assertIsNone(r.data["turns"]["new"]["plan"])
        self.assertIsNone(r.data["turns"]["new"]["plan_updated_at"])

    def test_truncated_unsupported_and_forked_reports_do_not_set_progress(self):
        for text in ['```codex-pulse-plan\n{"version":', '```codex-pulse-plan\nnot json\n```',
                     '```codex-pulse-plan\n{"version":2,"plan":[]}\n```']:
            self.assertIsNone(reported_plan({"type":"message", "role":"assistant", "content":text})[1])
        r = Rollout("child")
        r.data["created"] = 200
        r.ingest(event("event_msg", 210, type="task_started", turn_id="own"))
        r.ingest(event("response_item", 100, **plan_message([{"step":"copied", "status":"completed"}])))
        self.assertIsNone(r.data["turns"]["own"]["plan"])

    def test_partial_append_and_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log.jsonl"
            line = json.dumps(event("event_msg", 100, type="task_started", turn_id="t"))
            path.write_text(line[:20])
            roll = Rollout("a")
            roll.read(path)
            self.assertEqual(roll.data["offset"], 0)
            with path.open("a") as f:
                f.write(line[20:] + "\n")
            roll.read(path)
            self.assertEqual(len(roll.data["turns"]), 1)
            self.assertFalse(roll.read(path))

    def test_truncated_file_rebuilds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log.jsonl"
            path.write_text(json.dumps(event("event_msg", 100, type="task_started", turn_id="first-long-id")) + "\n")
            roll = Rollout("a"); roll.read(path)
            path.write_text(json.dumps(event("event_msg", 200, type="task_started", turn_id="b")) + "\n")
            roll.read(path)
            self.assertEqual(list(roll.data["turns"]), ["b"])

    def test_fork_ignores_inherited_responses(self):
        r = Rollout("fork"); r.data["created"] = 200
        r.ingest(event("token_usage_record", 100, thread_id="fork", response_id="copied", usage={"total_tokens": 900}))
        r.ingest(event("token_usage_record", 300, thread_id="parent", response_id="foreign", usage={"total_tokens": 900}))
        r.ingest(event("token_usage_record", 300, thread_id="fork", response_id="new", usage={"total_tokens": 50}))
        self.assertEqual(list(r.data["responses"]), ["new"])

    def test_abort_and_plan_replacement(self):
        r = Rollout("a")
        r.ingest(event("event_msg", 100, type="task_started", turn_id="t"))
        for plan in [[{"step": "a", "status": "completed"}], [{"step": "b", "status": "pending"}]]:
            r.ingest(event("response_item", 110, type="function_call", name="update_plan", arguments=json.dumps({"plan": plan})))
        r.ingest(event("event_msg", 120, type="turn_aborted", turn_id="t"))
        self.assertEqual(r.data["turns"]["t"]["status"], "interrupted")
        self.assertEqual(r.data["turns"]["t"]["plan"][0]["step"], "b")

    def test_legacy_reset_rebases_without_fabricating_usage(self):
        r = Rollout("a")
        r.ingest(event("event_msg", 100, type="task_started", turn_id="t"))
        for ts, count in [(101, 100), (102, 150), (103, 20), (104, 40)]:
            r.ingest(event("event_msg", ts, type="token_count", info={"total_token_usage": {"input_tokens": count}}))
        self.assertEqual(sum(x["usage"]["total_tokens"] for x in r.data["legacy"].values()), 170)
        self.assertEqual(r.data["warnings"], 1)

    def test_late_metadata_does_not_extend_completed_turn(self):
        r = Rollout("a")
        r.ingest(event("event_msg", 100, type="task_started", turn_id="t"))
        r.ingest(event("event_msg", 120, type="task_complete", turn_id="t"))
        r.ingest(event("event_msg", 90000, type="thread_settings_applied"))
        self.assertEqual(r.data["turns"]["t"]["last"], 120)

    def test_metadata_after_missing_end_does_not_count_idle_hours(self):
        r = Rollout("a")
        r.ingest(event("event_msg", 100, type="task_started", turn_id="old"))
        r.ingest(event("event_msg", 160, type="item_completed"))
        for row in [event("event_msg", 21700, type="thread_settings_applied"),
                    event("world_state", 21700),
                    event("event_msg", 21700, type="token_count", info=None),
                    event("turn_context", 21700, turn_id="old", model="model"),
                    event("response_item", 21700, type="message", role="user")]:
            r.ingest(row)
        r.ingest(event("event_msg", 21700, type="task_started", turn_id="new"))
        self.assertEqual(r.data["turns"]["old"]["last"], 160)
        self.assertIsNone(r.data["turns"]["old"]["end"])
        self.assertTrue(r.data["turns"]["old"]["superseded"])

    def test_rebased_fork_history_uses_original_lifecycle_time(self):
        r = Rollout("child")
        r.ingest(event("session_meta", 300.89, id="child", timestamp=300.89))
        r.ingest(event("session_meta", 300.89, id="parent", timestamp=100))
        r.ingest(event("event_msg", 300.891, type="task_started", turn_id="copied", started_at=100))
        r.ingest(event("turn_context", 300.891, turn_id="copied", model="old"))
        r.ingest(event("response_item", 300.891, type="reasoning"))
        r.ingest(event("event_msg", 300.891, type="token_count", info={"total_token_usage": {"input_tokens": 900}}))
        r.ingest(event("event_msg", 300.892, type="task_complete", turn_id="copied", started_at=100, completed_at=160))
        r.ingest(event("event_msg", 300.892, type="task_started", turn_id="copied-open", started_at=290))
        # The actual child's lifecycle has only whole-second precision.
        r.ingest(event("event_msg", 300.932, type="task_started", turn_id="own", started_at=300))
        r.ingest(event("event_msg", 364.917, type="task_complete", turn_id="own", started_at=300, completed_at=364))
        self.assertEqual(list(r.data["turns"]), ["own"])
        self.assertEqual(r.data["turns"]["own"]["end"] - r.data["turns"]["own"]["start"], 64)
        self.assertFalse(r.data["legacy"])

    def test_unchanged_usage_does_not_extend_unfinished_turn(self):
        r = Rollout("a")
        r.ingest(event("event_msg", 100, type="task_started", turn_id="t"))
        for ts in [110, 21700]:
            r.ingest(event("event_msg", ts, type="token_count", info={"total_token_usage": {"input_tokens": 50}}))
        self.assertEqual(r.data["turns"]["t"]["last"], 110)
        for ts in [120, 21800]:
            r.ingest(event("token_usage_record", ts, thread_id="a", turn_id="t", response_id="same", usage={"input_tokens": 50}))
        self.assertEqual(r.data["turns"]["t"]["last"], 120)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.collector = Collector(self.root / "codex", self.root / "data", "Asia/Shanghai")
        self.day = datetime.now(ZoneInfo("Asia/Shanghai")).replace(hour=0, minute=0, second=0, microsecond=0)
        self.date = self.day.date().isoformat()
        self.start = self.day.timestamp()
        self.collector.status["phase"] = "ready"

    def make(self, tid, child=False, parent=None, project="p"):
        self.collector.meta[tid] = {"title": tid, "project_id": project, "project_name": project,
                                    "cwd": "/synthetic", "child": child, "parent": parent}
        r = Rollout(tid)
        self.collector.files[tid] = r
        return r

    def test_cross_midnight_clipping_and_response_dedup(self):
        r = self.make("a")
        r.ingest(event("event_msg", self.start - 10, type="task_started", turn_id="t"))
        r.ingest(event("event_msg", self.start + 20, type="task_complete", turn_id="t"))
        token_row = event("token_usage_record", self.start + 5, thread_id="a", turn_id="t", response_id="same", usage={"input_tokens": 100})
        r.ingest(token_row); r.ingest(token_row)
        r.ingest(event("event_msg", self.start + 6, type="token_count", turn_id="t", info={"total_token_usage": {"input_tokens": 100}}))
        self.collector.files["copy"] = Rollout("a", json.loads(json.dumps(r.data)))
        snapshot = self.collector.snapshot(self.date)
        self.assertEqual(snapshot["metrics"]["duration"], 20)
        self.assertEqual(snapshot["metrics"]["usage"]["total_tokens"], 100)
        self.assertEqual(snapshot["source"]["legacyResponses"], 0)

    def test_child_project_rollup_and_parallel_time(self):
        a = self.make("a")
        b = self.make("b", child=True, parent="a", project="different")
        for r, delta in [(a, 0), (b, 30)]:
            r.ingest(event("event_msg", self.start + delta, type="task_started", turn_id="t"))
            r.ingest(event("event_msg", self.start + delta + 60, type="task_complete", turn_id="t"))
        s = self.collector.snapshot(self.date)
        self.assertEqual((s["metrics"]["projects"], s["metrics"]["mainTasks"], s["metrics"]["childTasks"]), (1, 1, 1))
        self.assertEqual((s["metrics"]["duration"], s["metrics"]["wallTime"]), (120, 90))
        self.assertEqual(s["projects"][0]["wallTime"], 90)

    def test_new_turn_does_not_keep_incomplete_predecessor_running(self):
        r = self.make("a")
        r.ingest(event("event_msg", self.start + 100, type="task_started", turn_id="old"))
        r.ingest(event("response_item", self.start + 110, type="reasoning"))
        r.ingest(event("event_msg", self.start + 120, type="task_started", turn_id="new"))
        with patch("pulse.collector.time.time", return_value=self.start + 130):
            s = self.collector.snapshot(self.date)
        turns = {t["id"]: t for t in s["tasks"][0]["turns"]}
        self.assertEqual((turns["old"]["seconds"], turns["new"]["seconds"]), (10, 10))
        self.assertEqual(turns["old"]["timing"], "incomplete")
        self.assertEqual(turns["old"]["observedEnd"], self.start + 110)
        self.assertEqual(s["metrics"]["incompleteTurns"], 1)

    def test_old_cache_is_rebuilt_after_timing_fix(self):
        data = self.root / "data"; data.mkdir()
        (data / "cache.json").write_text(json.dumps({
            "version": 2, "home": str(self.collector.home),
            "files": {"bad": Rollout("old").data}}))
        restored = Collector(self.collector.home, data, "Asia/Shanghai")
        self.assertEqual(restored.files, {})

    def test_silent_open_turn_is_unknown_and_timer_stops(self):
        r = self.make("a")
        now = time.time()
        r.ingest(event("event_msg", now - 1200, type="task_started", turn_id="t"))
        r.ingest(event("response_item", now - 1000, type="reasoning"))
        observed_date = datetime.fromtimestamp(now - 1000, self.collector.zone).date().isoformat()
        s = self.collector.snapshot(observed_date, 300)
        self.assertEqual(s["tasks"][0]["status"], "unknown")
        self.assertLessEqual(s["tasks"][0]["duration"], 200)
        self.assertEqual(s["metrics"]["runningProjects"], 0)

    def test_active_plan_percentage_and_unknown_other_plan(self):
        for tid in ["a", "b"]:
            r = self.make(tid)
            r.ingest(event("event_msg", time.time() - 5, type="task_started", turn_id="t"))
            if tid == "a":
                r.ingest(event("event_msg", time.time(), type="plan_updated", plan=[{"step":"one","status":"completed"}]))
        s = self.collector.snapshot(self.date)
        self.assertEqual(s["metrics"]["runningProjects"], 1)
        self.assertFalse(s["projects"][0]["progress"]["known"])

    def test_reported_progress_updates_preserves_completed_and_resets_for_new_work(self):
        r = self.make("a")
        r.ingest(event("event_msg", self.start + 100, type="task_started", turn_id="t"))
        plan = [{"step":"build", "status":"completed"}, {"step":"verify", "status":"in_progress"}]
        r.ingest(event("response_item", self.start + 110, **plan_message(plan)))
        with patch("pulse.collector.time.time", return_value=self.start + 120):
            s = self.collector.snapshot(self.date)
        p = s["projects"][0]["progress"]
        self.assertEqual(p["percent"], 50)
        self.assertEqual(p["currentSteps"], ["verify"])
        self.assertEqual(p["updatedAt"], self.start + 110)
        self.assertEqual(p["sources"], ["agent-report"])
        with patch("pulse.collector.time.time", return_value=self.start + 1000):
            stale = self.collector.snapshot(self.date)["projects"][0]["progress"]
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["percent"], 50)
        plan[1]["status"] = "completed"
        r.ingest(event("response_item", self.start + 1200, **plan_message(plan)))
        r.ingest(event("event_msg", self.start + 1210, type="task_complete", turn_id="t"))
        with patch("pulse.collector.time.time", return_value=self.start + 1220):
            p = self.collector.snapshot(self.date)["projects"][0]["progress"]
        self.assertEqual((p["percent"], p["scope"]), (100, "latest"))
        r.ingest(event("event_msg", self.start + 1230, type="task_started", turn_id="new"))
        with patch("pulse.collector.time.time", return_value=self.start + 1240):
            p = self.collector.snapshot(self.date)["projects"][0]["progress"]
        self.assertFalse(p["known"])
        self.assertIn("1/1", p["reason"])

    def test_project_progress_omits_child_plan_and_reports_missing_main_plan(self):
        for tid, child, plan in [("a",False,[{"step":"build","status":"completed"}]),
                                 ("b",False,None), ("child",True,[{"step":"child","status":"completed"}])]:
            r=self.make(tid,child=child,parent="a" if child else None)
            r.ingest(event("event_msg",self.start+100,type="task_started",turn_id="t"))
            if plan:r.ingest(event("response_item",self.start+110,**plan_message(plan)))
        with patch("pulse.collector.time.time",return_value=self.start+120):
            p=self.collector.snapshot(self.date)["projects"][0]["progress"]
        self.assertFalse(p["known"])
        self.assertIn("1/2",p["reason"])

    def test_v3_cache_is_replayed_for_new_progress_fields(self):
        directory=self.root/"data";directory.mkdir()
        (directory/"cache.json").write_text(json.dumps({"version":3,"home":str(self.collector.home),
                                                     "files":{"old":Rollout("old").data}}))
        self.assertEqual(Collector(self.collector.home,directory,"Asia/Shanghai").files,{})

    def test_cache_restart_does_not_store_messages(self):
        r = self.make("a")
        r.ingest(event("event_msg", time.time(), type="task_started", turn_id="t"))
        r.ingest(event("response_item", time.time(), type="message", content="PRIVATE PROMPT TEST"))
        self.collector.save()
        raw = (self.root / "data/cache.json").read_text()
        self.assertNotIn("PRIVATE PROMPT TEST", raw)
        restored = Collector(self.root / "codex", self.root / "data", "Asia/Shanghai")
        self.assertEqual(restored.files["a"].data["turns"], r.data["turns"])

    def test_metadata_excludes_guardians_and_reads_original_index_only(self):
        home = self.root / "codex"; home.mkdir()
        path = home / "state_5.sqlite"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE threads(id,title,rollout_path,created_at,updated_at,source,thread_source,cwd)")
        for tid, kind, source in [("u", "user", "vscode"), ("g", "guardian_review", "vscode"), ("old", "subagent", '{"subagent":{"other":"guardian"}}')]:
            conn.execute("INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)", (tid, tid, "/synthetic", 100, time.time(), source, kind, "/synthetic"))
        conn.commit(); conn.close()
        before = path.read_bytes()
        meta, excluded = self.collector.metadata()
        self.assertEqual(list(meta), ["u"])
        self.assertEqual(excluded, 2)
        self.assertEqual(path.read_bytes(), before)

    def test_out_of_range_date_rejected(self):
        with self.assertRaises(ValueError):
            self.collector.snapshot("2000-01-01")

    def test_iso_timestamp_and_millisecond_epoch(self):
        self.assertEqual(timestamp("2026-01-01T00:00:00Z"), timestamp(1767225600000))


if __name__ == "__main__":
    unittest.main()
