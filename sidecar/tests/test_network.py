"""Real HTTP disconnect/retry without changing the user's network settings."""
import hashlib
import http.server
import os
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from yt_dlp import YoutubeDL as RealYoutubeDL
from bilio_sidecar.ytdlp import download_video


class NetworkRecoveryTest(unittest.TestCase):
    def test_dropped_http_response_resumes_and_produces_exact_file(self):
        payload = bytes(range(256)) * 1024
        attempts = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                requested = self.headers.get("Range")
                offset = int(requested.split("=", 1)[1].split("-", 1)[0]) if requested else 0
                attempts.append(offset)
                self.send_response(206 if offset else 200)
                self.send_header("Content-Length", str(len(payload) - offset))
                self.send_header("Accept-Ranges", "bytes")
                if offset:
                    self.send_header("Content-Range", f"bytes {offset}-{len(payload)-1}/{len(payload)}")
                self.end_headers()
                if len(attempts) == 1:
                    self.wfile.write(payload[:65536])
                    self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                else:
                    self.wfile.write(payload[offset:])

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/fixture.mp4"

        class LocalYoutubeDL(RealYoutubeDL):
            def extract_info(self, url, download=False, **kwargs):
                return self.process_ie_result({"id": "http", "title": "HTTP fixture", "extractor": "fixture",
                    "formats": [{"format_id": "mux", "url": url, "ext": "mp4", "vcodec": "avc1",
                                 "acodec": "aac", "filesize": len(payload)}]}, download=download)

        try:
            with tempfile.TemporaryDirectory() as directory, patch("bilio_sidecar.ytdlp.YoutubeDL", LocalYoutubeDL):
                path = download_video(url, {"mode": "exact", "format_id": "mux"}, directory,
                                      lambda message: None, threading.Event())
                with open(path, "rb") as handle:
                    self.assertEqual(hashlib.sha256(handle.read()).digest(), hashlib.sha256(payload).digest())
                self.assertTrue(os.path.isfile(path))
                self.assertGreaterEqual(len(attempts), 2)
                self.assertTrue(any(offset > 0 for offset in attempts))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
