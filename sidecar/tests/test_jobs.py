import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from bilio_sidecar.jobs import DownloadQueue


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.directory.name, "result.mp4")
        with open(self.path, "wb") as handle:
            handle.write(b"mock")
        self.release = threading.Event()
        self.messages = []
        self.queues = []

    def tearDown(self):
        self.release.set()
        for jobs in self.queues:
            jobs.pending.join()
            jobs.close()
        self.directory.cleanup()

    def args(self, index):
        return {"url": f"https://example.invalid/{index}", "policy": {"mode": "best"},
                "title": f"Video {index}", "output_dir": self.directory.name}

    def queue(self, worker, concurrency=2):
        jobs = DownloadQueue(worker, self.messages.append, concurrency)
        self.queues.append(jobs)
        return jobs

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("worker did not reach expected state")
            threading.Event().wait(0.005)

    def test_concurrency_is_bounded_and_queued_cancel_never_starts(self):
        started = []
        def worker(**args):
            started.append(args["url"])
            self.release.wait(3)
            return self.path
        jobs = self.queue(worker)
        for index in range(20):
            jobs.submit(index + 1, self.args(index))
        self.wait(lambda: len(started) == 2)
        self.assertEqual(sum(t["status"] == "queued" for t in jobs.snapshots()), 18)
        jobs.cancel(20)
        self.release.set()
        jobs.pending.join()
        self.assertNotIn(self.args(19)["url"], started)
        self.assertEqual(jobs.snapshots()[-1]["status"], "cancelled")

    def test_failure_releases_slot_and_missing_file_is_not_completed(self):
        def worker(**args):
            if args["url"].endswith("0"):
                raise RuntimeError("simulated error")
            return "/missing/bilio/file.mp4"
        jobs = self.queue(worker, 1)
        jobs.submit(1, self.args(0))
        jobs.submit(2, self.args(1))
        jobs.pending.join()
        self.assertEqual([t["status"] for t in jobs.snapshots()], ["failed", "failed"])

    def test_completed_filename_and_size_are_final_file(self):
        def worker(**args):
            args["progress_callback"]({"filename": "video.f30280.m4a", "downloaded_bytes": 99})
            return self.path
        jobs = self.queue(worker, 1)
        jobs.submit(1, self.args(0))
        jobs.pending.join()
        task = jobs.snapshots()[0]
        self.assertEqual(task["filename"], "result.mp4")
        self.assertEqual(task["downloadedBytes"], 4)
        self.assertEqual(task["totalBytes"], 4)

    def test_remove_record_does_not_remove_file_or_active_task(self):
        def worker(**args):
            self.release.wait(3)
            return self.path
        jobs = self.queue(worker, 1)
        jobs.submit(1, self.args(0))
        with self.assertRaisesRegex(ValueError, "请先停止"):
            jobs.remove_finished(1)
        self.release.set()
        jobs.pending.join()
        self.assertEqual(jobs.remove_finished(1), [1])
        self.assertEqual(jobs.snapshots(), [])
        self.assertTrue(os.path.isfile(self.path))

    def test_snapshot_config_and_emitted_events_are_immutable(self):
        def worker(**args):
            self.release.wait(3)
            return self.path
        jobs = self.queue(worker, 1)
        args = self.args(0)
        jobs.submit(1, args)
        args["policy"]["mode"] = "changed"
        snapshots = jobs.snapshots()
        snapshots[0]["config"]["title"] = "changed"
        self.assertEqual(jobs.snapshots()[0]["config"]["policy"]["mode"], "best")
        self.assertEqual(self.messages[0]["data"]["status"], "queued")
        self.release.set()
        jobs.pending.join()
        seq = [m["data"]["sequence"] for m in self.messages]
        self.assertEqual(seq, list(range(1, len(seq) + 1)))
        jobs._progress(1, {"downloaded_bytes": 1})
        self.assertEqual(jobs.snapshots()[0]["status"], "completed")

    def test_duplicate_activity_is_rejected_but_terminal_retry_is_allowed(self):
        def worker(**args):
            self.release.wait(3)
            return self.path
        jobs = self.queue(worker)
        jobs.submit(1, self.args(0))
        with self.assertRaises(ValueError):
            jobs.submit(2, self.args(0))
        self.release.set()
        jobs.pending.join()
        jobs.submit(3, self.args(0))
        jobs.pending.join()
        self.assertEqual(jobs.snapshots()[-1]["status"], "completed")

    def test_cancel_is_terminal_even_if_worker_returns_success(self):
        def worker(**args):
            self.release.wait(3)
            return self.path
        jobs = self.queue(worker, 1)
        jobs.submit(1, self.args(0))
        self.wait(lambda: jobs.snapshots()[0]["status"] == "starting")
        jobs.cancel(1)
        self.assertEqual(jobs.snapshots()[0]["status"], "cancelling")
        self.release.set()
        jobs.pending.join()
        self.assertEqual(jobs.snapshots()[0]["status"], "cancelled")

    def test_batch_cancel_rejects_later_submissions(self):
        def worker(**args):
            self.release.wait(3)
            return self.path
        jobs = self.queue(worker, 1)
        jobs.submit(1, {**self.args(0), "batchId": "batch"})
        jobs.cancel_batch("batch")
        with self.assertRaisesRegex(ValueError, "本批次已取消"):
            jobs.submit(2, {**self.args(1), "batchId": "batch"})

    def test_active_pause_waits_for_worker_and_resume_retains_workspace(self):
        runs = []
        def worker(**args):
            runs.append(args['work_id'])
            if len(runs) == 1:
                args['cancel_event'].wait(2)
                raise RuntimeError('stopped')
            return self.path
        jobs = self.queue(worker, 1);jobs.submit(1,self.args(0))
        self.wait(lambda: len(runs)==1)
        jobs.pause(1);self.wait(lambda: jobs.snapshots()[0]['status']=='paused')
        jobs.pending.join();jobs.resume(1);jobs.pending.join()
        self.assertEqual(jobs.snapshots()[0]['status'],'completed')
        self.assertEqual(runs[0],runs[1])

    def test_queued_pause_resume_ticket_cannot_start_twice(self):
        started=[]
        def worker(**args):
            started.append(args['url']);self.release.wait(3);return self.path
        jobs=self.queue(worker,1);jobs.submit(1,self.args(0))
        self.wait(lambda:len(started)==1);jobs.submit(2,self.args(1))
        jobs.pause(2);jobs.pause(2);jobs.resume(2);jobs.resume(2)
        self.release.set();jobs.pending.join()
        self.assertEqual(started.count(self.args(1)['url']),1)

    def test_fifty_tasks_and_dynamic_concurrency_release_all_slots(self):
        started=[];active=0;later_peak=0;lock=threading.Lock()
        def worker(**args):
            nonlocal active,later_peak
            with lock:
                active+=1;started.append(args['url'])
                if len(started)>3:later_peak=max(later_peak,active)
            self.release.wait(3);time.sleep(.002)
            with lock:active-=1
            return self.path
        jobs=self.queue(worker,1)
        for index in range(50):jobs.submit(index+1,self.args(index))
        self.wait(lambda:len(started)==1);jobs.set_concurrency(3);self.wait(lambda:len(started)==3)
        jobs.set_concurrency(1);self.release.set();jobs.pending.join()
        self.assertEqual(len(started),50);self.assertLessEqual(later_peak,1)
        self.assertEqual(jobs.running,0)

    def test_processing_cannot_pause_and_paused_record_cannot_be_removed(self):
        jobs=self.queue(lambda **kw:self.path,1)
        self.release.set();jobs.submit(1,self.args(0));jobs.pending.join()
        with jobs.lock:jobs.tasks[1]['status']='processing'
        with self.assertRaisesRegex(ValueError,'合并期间'):jobs.pause(1)
        with jobs.lock:jobs.tasks[1]['status']='paused'
        with self.assertRaises(ValueError):jobs.remove_finished(1)
        self.assertEqual(jobs.clear_finished(),[])


class DispatchTest(unittest.TestCase):
    def test_parse_does_not_block_cancel_or_ping(self):
        from bilio_sidecar import commands
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        messages = []
        def parse(url, browser, callback, cancel):
            entered.set()
            release.wait(3)
            stopped.set()
            return {}
        with patch.object(commands, "parse_url", parse), patch.object(commands, "emit", messages.append):
            try:
                list(commands.dispatch({"id": 900, "cmd": "parse", "args": {"url": "https://www.bilibili.com/video/BVmock"}}))
                self.assertTrue(entered.wait(1))
                self.assertEqual(list(commands.dispatch({"id": 901, "cmd": "ping"}))[0]["type"], "pong")
                result = list(commands.dispatch({"id": 902, "cmd": "cancel", "args": {"job_id": 900}}))
                self.assertTrue(result[0]["data"]["requested"])
            finally:
                release.set()
                self.assertTrue(stopped.wait(1))
                deadline = time.monotonic() + 1
                while 900 in commands._metadata and time.monotonic() < deadline:
                    threading.Event().wait(0.005)
        self.assertEqual(messages[-1]["error"], "已取消解析")
