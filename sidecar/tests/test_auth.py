import json
import os
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

from bilio_sidecar import auth
from bilio_sidecar.jobs import DownloadQueue
from bilio_sidecar.storage import TaskStorage
from bilio_sidecar.ytdlp import _session_jar, _apply_session, check_cookies


def cookies(domain=".bilibili.com"):
    return [{"name": "SESSDATA", "value": "test-session-value", "domain": domain, "path": "/", "secure": True}]


class AuthTest(unittest.TestCase):
    def tearDown(self):
        auth.clear()

    def test_only_bilibili_cookies_and_no_header_injection(self):
        for domain in (".bilibili.com", "passport.bilibili.com"):
            self.assertEqual(len(auth.validate_cookies(cookies(domain))), 1)
        for domain in ("bilibili.com.evil.test", "evilbilibili.com", "example.com"):
            with self.assertRaises(ValueError):
                auth.validate_cookies(cookies(domain))
        bad = cookies()
        bad[0]["value"] = "invalid\r\nInjected: true"
        with self.assertRaises(ValueError):
            auth.validate_cookies(bad)

    def test_session_snapshot_is_private_immutable_and_bound(self):
        auth.install("one", cookies())
        first = auth.snapshot("one")
        first[0]["value"] = "changed"
        self.assertEqual(auth.snapshot("one")[0]["value"], "test-session-value")
        with self.assertRaisesRegex(ValueError, "会话已变化"):
            auth.snapshot("old")
        self.assertFalse(auth.clear("old"))
        self.assertTrue(auth.snapshot("one"))
        self.assertTrue(auth.clear("one"))
        with self.assertRaisesRegex(ValueError, "请先"):
            auth.snapshot()

    def test_cookie_jar_never_sends_credentials_to_other_hosts_or_http(self):
        jar = _session_jar(cookies())
        for url in ("https://api.bilibili.com/", "https://www.bilibili.com/"):
            request = urllib.request.Request(url)
            jar.add_cookie_header(request)
            self.assertIn("SESSDATA=", request.get_header("Cookie"))
        for url in ("https://bilibili.com.evil.test/", "https://example.com/", "http://www.bilibili.com/"):
            request = urllib.request.Request(url)
            jar.add_cookie_header(request)
            self.assertIsNone(request.get_header("Cookie"))

    def test_native_session_does_not_read_browser_database(self):
        import http.cookiejar
        class Ydl:
            cookiejar = http.cookiejar.CookieJar()
        with patch("yt_dlp.cookies.extract_cookies_from_browser", side_effect=AssertionError("browser access forbidden")):
            _apply_session(Ydl, "bilio", cookies())
        self.assertEqual(next(iter(Ydl.cookiejar)).name, "SESSDATA")

    def test_session_probe_does_not_return_credentials(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({"code": 0, "data": {"isLogin": True, "mid": 123, "uname": "Test", "vipStatus": 1}}).encode()
        with patch("urllib.request.OpenerDirector.open", return_value=Response()):
            status = check_cookies("bilio", session_cookies=cookies())
        self.assertTrue(status["logged_in"])
        self.assertTrue(status["is_vip"])
        self.assertEqual(status["account_id"], "123")
        self.assertNotIn("test-session-value", json.dumps(status))

    def test_download_credentials_never_enter_snapshots_or_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "result.mp4")
            with open(path, "wb") as handle:
                handle.write(b"test")
            captured = []
            def worker(**args):
                captured.append(args["session_cookies"])
                return path
            auth.install("one", cookies())
            storage = TaskStorage(os.path.join(directory, "tasks.sqlite3"))
            queue = DownloadQueue(worker, lambda message: None, 1, storage)
            queue.submit(1, {"url": "https://www.bilibili.com/video/BVtest", "policy": {"mode": "best"},
                            "output_dir": directory, "cookies_from_browser": "bilio", "auth_session_id": "one"})
            queue.pending.join()
            self.assertEqual(captured[0][0]["value"], "test-session-value")
            self.assertNotIn("test-session-value", json.dumps(queue.snapshots()))
            self.assertNotIn("test-session-value", json.dumps(storage.load()))
            self.assertEqual(storage.load()[0]["config"]["auth_session_id"], "one")
            queue.close()
            auth.clear()
            with self.assertRaisesRegex(ValueError, "请先"):
                queue.submit(2, {"url": "https://www.bilibili.com/video/BVtest", "policy": {"mode": "best"},
                            "output_dir": directory, "cookies_from_browser": "bilio"})
