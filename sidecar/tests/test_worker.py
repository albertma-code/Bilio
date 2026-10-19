import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from bilio_sidecar.worker import isolated_download
from bilio_sidecar.ytdlp import CancelRequested


def successful_worker(connection, config):
    connection.send({"type": "progress", "data": {"status": "downloading", "downloaded_bytes": 10}})
    connection.send({"type": "completed", "path": config["path"]})


def crashed_worker(connection, config):
    os._exit(7)


def blocked_worker(connection, config):
    # The marker must never appear if cancellation also stops subprocesses.
    subprocess.Popen([sys.executable, "-c", "import time,pathlib;time.sleep(1.5);pathlib.Path(" + repr(config["marker"]) + ").write_text('orphan')"])
    connection.send({"type": "progress", "data": {"status": "processing"}})
    time.sleep(30)


class WorkerTest(unittest.TestCase):
    def test_success_and_progress_across_spawn_boundary(self):
        events = []
        result = isolated_download(cancel_event=threading.Event(), progress_callback=events.append,
                                   _target=successful_worker, path="result.mp4")
        self.assertEqual(result, "result.mp4")
        self.assertEqual(events[0]["downloaded_bytes"], 10)

    def test_worker_crash_becomes_failure_not_hang(self):
        with self.assertRaisesRegex(RuntimeError, "意外退出"):
            isolated_download(cancel_event=threading.Event(), progress_callback=lambda msg: None, _target=crashed_worker)

    @unittest.skipUnless(os.name == "posix", "process-group cancellation is macOS/POSIX")
    def test_cancel_terminates_blocked_processing_and_children(self):
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            marker = os.path.join(directory, "orphan")
            started = time.monotonic()
            with self.assertRaises(CancelRequested):
                isolated_download(cancel_event=cancel, progress_callback=lambda msg: cancel.set(),
                                  _target=blocked_worker, marker=marker)
            self.assertLess(time.monotonic() - started, 5)
            time.sleep(1.7)
            self.assertFalse(os.path.exists(marker))
