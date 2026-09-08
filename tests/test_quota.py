import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pulse.quota import QuotaError, QuotaMonitor, normalize_quota, read_quota


def fixture(used=32):
    return {"rateLimits": {"primary": {"usedPercent": 99}},
            "rateLimitsByLimitId": {
                "codex": {"primary": {"usedPercent": used, "windowDurationMins": 10080, "resetsAt": 2000000000},
                          "secondary": None, "credits": {"balance": "0", "unlimited": False}},
                "spark": {"limitName": "Example model", "primary": {"usedPercent": 0, "windowDurationMins": 300},
                          "secondary": {"usedPercent": None, "windowDurationMins": 10080}}},
            "accountId": "PRIVATE-ACCOUNT", "email": "private@example.test",
            "rateLimitResetCredits": {"availableCount": 3, "credits": [{"id": "PRIVATE-RESET"}]}}


class QuotaNormalizationTests(unittest.TestCase):
    def test_multi_bucket_priority_dynamic_windows_and_privacy(self):
        value = normalize_quota(fixture())
        codex, spark = value["buckets"]
        self.assertEqual(codex["windows"][0]["remainingPercent"], 68)
        self.assertEqual(codex["windows"][0]["windowDurationMins"], 10080)
        self.assertEqual(codex["credits"]["balance"], "0")
        self.assertEqual(spark["windows"][0]["remainingPercent"], 100)
        self.assertIsNone(spark["windows"][1]["remainingPercent"])
        self.assertEqual(value["resetCreditsAvailable"], 3)
        self.assertNotIn("PRIVATE", json.dumps(value))
        self.assertNotIn("private@example", json.dumps(value))

    def test_legacy_fallback_and_unknowns_are_not_zero(self):
        value = normalize_quota({"rateLimitsByLimitId": None, "rateLimits": {"primary": {}}})
        window = value["buckets"][0]["windows"][0]
        self.assertIsNone(window["remainingPercent"])
        self.assertIsNone(window["resetsAt"])
        self.assertIsNone(window["windowDurationMins"])
        self.assertIsNone(value["resetCreditsAvailable"])
        self.assertIsNone(value["buckets"][0]["credits"])

    def test_percent_clamping_and_invalid_numbers(self):
        for used, expected in [(-4, 100), (140, 0), (100, 0), (25.5, 74.5),
                               (None, None), (True, None), ("0", None), (float("nan"), None), (float("inf"), None)]:
            with self.subTest(used=used):
                value = normalize_quota(fixture(used))
                self.assertEqual(value["buckets"][0]["windows"][0]["remainingPercent"], expected)

    def test_empty_invalid_and_count_only_responses(self):
        for raw in (None, [], {}, {"rateLimitsByLimitId": {"codex": None}}):
            with self.assertRaises(QuotaError):
                normalize_quota(raw)
        value = normalize_quota({"rateLimitResetCredits": {"availableCount": 0, "credits": None}})
        self.assertEqual(value["resetCreditsAvailable"], 0)
        self.assertEqual(value["buckets"], [])


class QuotaRPCTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = self.root / "calls.json"
        self.pid = self.root / "pid"

    def cli(self, mode="ok", account="chatgpt", error=None):
        file = self.root / "codex"
        config = {"mode": mode, "account": account, "error": error, "quota": fixture()}
        file.write_text("#!" + sys.executable + "\n" + "import json, os, sys, time\n" +
                        "config = " + repr(config) + "\n" +
                        "calls = " + repr(str(self.calls)) + "\n" +
                        "open(" + repr(str(self.pid)) + ", 'w').write(str(os.getpid()))\n" + '''
methods = []
for line in sys.stdin:
    req = json.loads(line)
    methods.append(req['method'])
    with open(calls, 'w') as f: json.dump(methods, f)
    if req['method'] == 'initialized': continue
    if config['mode'] == 'timeout': time.sleep(10)
    if config['mode'] == 'oversize':
        sys.stdout.write('x' * (1024 * 1024 + 1)); sys.stdout.flush(); continue
    if config['mode'] == 'eof': sys.exit(0)
    if req['method'] == 'initialize': result = {'userAgent': 'synthetic'}
    elif req['method'] == 'account/read':
        result = {'account': {'type': config['account'], 'email': 'private@example.test'} if config['account'] else None}
    elif req['method'] == 'account/rateLimits/read': result = config['quota']
    else: sys.exit(1)
    response = {'id': req['id'], 'result': result}
    if config['error'] and req['method'] == 'account/rateLimits/read':
        response = {'id': req['id'], 'error': config['error']}
    text = json.dumps({'method': 'account/updated', 'params': {}}) + '\\n' + json.dumps(response) + '\\n'
    mid = len(text) // 2
    sys.stdout.write(text[:mid]); sys.stdout.flush()
    sys.stdout.write(text[mid:]); sys.stdout.flush()
''')
        file.chmod(0o700)
        return str(file)

    def test_read_only_handshake_handles_notifications_and_fragmented_lines(self):
        result = read_quota(self.root, self.cli())
        self.assertEqual(result["buckets"][0]["windows"][0]["remainingPercent"], 68)
        self.assertEqual(json.loads(self.calls.read_text()),
                         ["initialize", "initialized", "account/read", "account/rateLimits/read"])
        with self.assertRaises(ProcessLookupError):
            os.kill(int(self.pid.read_text()), 0)

    def test_missing_login_and_api_key_stop_before_quota(self):
        for account, code in [(None, "login_required"), ("apiKey", "auth_unsupported")]:
            with self.subTest(account=account), self.assertRaises(QuotaError) as error:
                read_quota(self.root, self.cli(account=account))
            self.assertEqual(error.exception.code, code)
            self.assertNotIn("account/rateLimits/read", json.loads(self.calls.read_text()))

    def test_safe_rpc_errors(self):
        for upstream, code in [({"code": -32601}, "cli_unsupported"),
                               ({"message": "401 private@example.test"}, "login_required"),
                               ({"message": "429 PRIVATE-TOKEN"}, "rate_limited"),
                               ({"message": "PRIVATE-TOKEN"}, "fetch_failed")]:
            with self.subTest(code=code), self.assertRaises(QuotaError) as error:
                read_quota(self.root, self.cli(error=upstream))
            self.assertEqual(error.exception.code, code)
            self.assertNotIn("PRIVATE", str(error.exception))
            self.assertNotIn("example.test", str(error.exception))

    def test_timeout_oversize_eof_and_missing_cli(self):
        for mode, code in [("timeout", "timeout"), ("oversize", "fetch_failed"), ("eof", "fetch_failed")]:
            with self.subTest(mode=mode), self.assertRaises(QuotaError) as error:
                read_quota(self.root, self.cli(mode), timeout=0.4)
            self.assertEqual(error.exception.code, code)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(self.pid.read_text()), 0)
        with self.assertRaises(QuotaError) as error:
            read_quota(self.root, self.root / "missing")
        self.assertEqual(error.exception.code, "cli_missing")


class QuotaMonitorTests(unittest.TestCase):
    def wait_until(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.005)
        self.fail("Monitor did not reach the expected state")

    def monitor(self, fetch, **kwargs):
        monitor = QuotaMonitor(Path("/unused"), fetch=fetch, **kwargs)
        self.addCleanup(monitor.close)
        monitor.start()
        return monitor

    def test_initial_loading_disabled_and_nonblocking_snapshots(self):
        entered, release = threading.Event(), threading.Event()
        def fetch():
            entered.set()
            release.wait(2)
            return normalize_quota(fixture())
        monitor = self.monitor(fetch)
        self.assertTrue(entered.wait(1))
        for _ in range(30):
            self.assertEqual(monitor.snapshot()["status"], "loading")
            self.assertFalse(monitor.request_refresh())
        release.set()
        self.wait_until(lambda: monitor.snapshot()["status"] == "ready")
        self.assertFalse(monitor.request_refresh())
        disabled = QuotaMonitor(Path("/unused"), enabled=False, fetch=lambda: self.fail("Disabled fetch"))
        disabled.start()
        self.assertFalse(disabled.request_refresh())
        self.assertEqual(disabled.snapshot()["errorCode"], "disabled")

    def test_account_failure_clears_previous_values_and_recovers(self):
        sequence = iter([normalize_quota(fixture()), QuotaError("login_required"), normalize_quota(fixture(40))])
        def fetch():
            value = next(sequence)
            if isinstance(value, Exception): raise value
            return value
        monitor = self.monitor(fetch, min_refresh_seconds=0)
        self.wait_until(lambda: monitor.snapshot()["status"] == "ready")
        self.assertTrue(monitor.request_refresh())
        self.wait_until(lambda: monitor.snapshot()["errorCode"] == "login_required")
        self.assertFalse(monitor.snapshot()["available"])
        self.assertEqual(monitor.snapshot()["buckets"], [])
        self.assertTrue(monitor.request_refresh())
        self.wait_until(lambda: monitor.snapshot()["status"] == "ready")
        self.assertEqual(monitor.snapshot()["buckets"][0]["windows"][0]["remainingPercent"], 60)

    def test_automatic_refresh_and_stale_reset_do_not_invent_remaining(self):
        calls = []
        def fetch():
            calls.append(1)
            return normalize_quota(fixture())
        monitor = self.monitor(fetch, refresh_seconds=0.05)
        self.wait_until(lambda: len(calls) >= 2 and monitor.snapshot()["available"])
        monitor.close()
        with patch("pulse.quota.time.time", return_value=2000000001):
            snapshot = monitor.snapshot()
        self.assertEqual(snapshot["status"], "stale")
        self.assertTrue(snapshot["buckets"][0]["windows"][0]["resetDue"])
        self.assertEqual(snapshot["buckets"][0]["windows"][0]["remainingPercent"], 68)


if __name__ == "__main__":
    unittest.main()
