"""Versioned local task history. Never stores raw Cookies or media URLs."""
import copy
import json
import os
import sqlite3

CONFIG_FIELDS = ('url','title','policy','formatLabel','output_dir','cookies_from_browser','auth_session_id','account_ref','batchId','retryOf','conflict_policy')


class TaskStorage:
    def __init__(self, path):
        self.warnings = []
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        os.chmod(path, 0o600)
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            raise RuntimeError("任务记录版本高于当前应用支持的版本")
        if version == 1:
            backup = path + ".v1.bak"
            try:
                fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                raise RuntimeError("已存在旧版数据库备份，请先核对并保留备份后重试升级")
            os.close(fd)
            target = sqlite3.connect(backup)
            try:
                self.connection.backup(target)
            finally:
                target.close()
        self.connection.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        self.connection.execute("PRAGMA user_version=2")
        self.connection.commit()

    def load(self):
        result=[];self.warnings=[]
        for task_id,payload in self.connection.execute('SELECT id,payload FROM tasks ORDER BY id'):
            try:
                task=json.loads(payload)
                if not isinstance(task,dict) or task.get('jobId')!=task_id or not isinstance(task.get('config'),dict) or type(task.get('sequence')) is not int or task.get('status') not in {'queued','starting','downloading','processing','pausing','paused','cancelling','completed','cancelled','failed','interrupted'}:
                    raise ValueError('invalid record')
                result.append(task)
                task['config']={key:task['config'][key] for key in CONFIG_FIELDS if key in task['config']}
            except (ValueError,TypeError):
                self.warnings.append({'record_id':task_id,'reason':'invalid_record'})
        return result

    def save(self, task):
        record = copy.deepcopy(task)
        # yt-dlp errors can contain temporary signed media URLs.
        if record.get("errorMessage"):
            record["errorMessage"] = "上次任务失败，可按原配置重试"
        config = record["config"]
        record["config"] = {key: config[key] for key in CONFIG_FIELDS if key in config}
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO tasks VALUES (?, ?)",
                                    (task["jobId"], json.dumps(record, ensure_ascii=False)))

    def remove(self, task_id):
        with self.connection:
            self.connection.execute("DELETE FROM tasks WHERE id=?", (task_id,))
