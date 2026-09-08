import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from pulse.collector import Collector
from pulse.server import PulseServer, report, validate_settings


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.collector = Collector(root / "codex", root / "data", "Asia/Shanghai")
        self.collector.status["phase"] = "ready"
        self.server = PulseServer(0, self.collector)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.close)
        self.url = "http://127.0.0.1:" + str(self.server.server_port)

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join()

    def request(self, path, headers=None, body=None):
        return urllib.request.urlopen(urllib.request.Request(self.url + path, data=body, headers=headers or {}), timeout=3)

    def test_page_and_empty_snapshot(self):
        with self.request("/") as r:
            self.assertIn("Codex Pulse", r.read().decode())
            self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
        with self.request("/api/snapshot") as r:
            self.assertEqual(json.load(r)["metrics"]["projects"], 0)

    def test_settings_save_and_reload(self):
        value = {"theme":"dark","refreshSeconds":10,"staleSeconds":120}
        with self.request("/api/settings", {"X-Pulse-Request":"1","Content-Type":"application/json"}, json.dumps(value).encode()) as r:
            self.assertEqual(json.load(r), value)
        reloaded = PulseServer(0, self.collector)
        try: self.assertEqual(reloaded.settings, value)
        finally: reloaded.server_close()

    def test_cross_origin_and_dns_rebinding_rejected(self):
        for headers in [{"Origin":"https://evil.example"}, {"Host":"evil.example"}, {"Sec-Fetch-Site":"cross-site"}]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/api/snapshot", headers)
            self.assertEqual(error.exception.code, 403)

    def test_csrf_write_without_header_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/settings", {"Content-Type":"application/json"}, b"{}")
        self.assertEqual(error.exception.code, 403)

    def test_private_files_and_path_traversal_not_served(self):
        for path in ["/../collector.py", "/%2e%2e/collector.py", "/cache.json", "/.git/config", "/api/missing"]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(path)
            self.assertEqual(error.exception.code, 404)

    def test_invalid_settings_and_date(self):
        with self.assertRaises(ValueError):
            validate_settings({"theme":"light", "refreshSeconds":True, "staleSeconds":120})
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/snapshot?date=invalid")
        self.assertEqual(error.exception.code, 400)

    def test_markdown_download(self):
        with self.request("/api/report") as r:
            self.assertIn("attachment", r.headers["Content-Disposition"])
            body = r.read().decode()
            self.assertIn("当日运行时长 0 秒（并行重叠只计一次）；回合并行累计 0 秒", body)
            self.assertIn("记录不完整的回合 0 个", body)
            self.assertIn("| 运行秒数 | 并行累计秒数 | 不完整回合 |", body)


if __name__ == "__main__":
    unittest.main()
