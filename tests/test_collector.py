import json
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pulse.collector import Collector, Rollout, progress, timestamp, union_seconds, usage


def event(kind, ts, **payload):
    return {"type": kind, "timestamp": ts, "payload": payload}


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

    def test_silent_open_turn_is_unknown_and_timer_stops(self):
        r = self.make("a")
        now = time.time()
        r.ingest(event("event_msg", now - 1200, type="task_started", turn_id="t"))
        r.ingest(event("response_item", now - 1000, type="reasoning"))
        s = self.collector.snapshot(self.date, 300)
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
