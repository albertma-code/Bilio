import os
import tempfile
import unittest
import sqlite3

from bilio_sidecar.storage import TaskStorage
from bilio_sidecar.jobs import DownloadQueue
from bilio_sidecar.source import normalize_source


class StorageTest(unittest.TestCase):
    def test_one_corrupt_record_is_quarantined_without_deleting_original(self):
        with tempfile.TemporaryDirectory() as directory:
            storage=TaskStorage(os.path.join(directory,'tasks.sqlite3'))
            storage.save({'jobId':1,'sequence':1,'status':'completed','config':{}})
            storage.connection.execute('INSERT INTO tasks VALUES (?,?)',(2,'broken-json'))
            storage.connection.commit()
            self.assertEqual(len(storage.load()),1)
            self.assertEqual(storage.warnings,[{'record_id':2,'reason':'invalid_record'}])
            self.assertEqual(storage.connection.execute('SELECT payload FROM tasks WHERE id=2').fetchone()[0],'broken-json')
            storage.connection.close()
    def test_v1_migration_has_readable_backup_and_preserves_paused(self):
        with tempfile.TemporaryDirectory() as directory:
            path=os.path.join(directory,'tasks.sqlite3');storage=TaskStorage(path)
            storage.save({'jobId':1,'sequence':1,'status':'paused','workId':'a'*32,'config':{'account_ref':'bili-test','policy':{'mode':'best'}}})
            storage.connection.execute('PRAGMA user_version=1');storage.connection.close()
            migrated=TaskStorage(path)
            self.assertEqual(migrated.connection.execute('PRAGMA user_version').fetchone()[0],2)
            backup=sqlite3.connect(path+'.v1.bak')
            self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0],1);backup.close()
            jobs=DownloadQueue(lambda **kw:None,lambda msg:None,storage=migrated)
            self.assertEqual(jobs.snapshots()[0]['status'],'paused')
            self.assertEqual(jobs.snapshots()[0]['config']['account_ref'],'bili-test')
            migrated.connection.close()
    def test_restart_marks_unfinished_tasks_interrupted_without_replaying(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "tasks.sqlite3")
            storage = TaskStorage(path)
            storage.save({"jobId": 1, "sequence": 3, "status": "downloading", "config": {
                "url": "https://www.bilibili.com/video/BVtest", "output_dir": directory,
                "policy": {"mode": "best"}, "cookies_from_browser": "chrome", "raw_cookie": "must-not-persist"},
                "errorMessage": "https://media.invalid?token=secret"})
            storage.connection.close()
            recovered = TaskStorage(path)
            calls = []
            jobs = DownloadQueue(lambda **kw: calls.append(kw), lambda msg: None, storage=recovered)
            task = jobs.snapshots()[0]
            self.assertEqual(task["status"], "interrupted")
            self.assertEqual(task["sequence"], 4)
            self.assertNotIn("raw_cookie", task["config"])
            self.assertNotIn("token=secret", task["errorMessage"])
            self.assertEqual(calls, [])
            jobs.clear_finished()
            self.assertEqual(recovered.load(), [])
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            recovered.connection.close()

    def test_source_retains_part_index_but_discards_tracking_and_fragment(self):
        self.assertEqual(normalize_source("https://www.bilibili.com/video/BVtest?spm_id_from=x&p=2#reply"),
                         "https://www.bilibili.com/video/BVtest?p=2")
        for url in ("file:///tmp/test", "https://bilibili.com.evil.invalid/video/test", "https://user:pass@bilibili.com/video/test"):
            with self.assertRaises(ValueError):
                normalize_source(url)
