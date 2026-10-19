"""Bounded download scheduling with ordered task snapshots."""
from __future__ import annotations

import copy
import os
import queue
import threading
import time
import uuid
from typing import Callable

from .ytdlp import CancelRequested, quality_selector
from .storage import CONFIG_FIELDS

TERMINAL = {"completed", "cancelled", "failed", "interrupted"}


class DownloadQueue:
    def __init__(self, download: Callable, emit: Callable, concurrency: int = 2, storage=None):
        self.download, self.emit = download, emit
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.limit, self.running = concurrency, 0
        self.tasks, self.cancels = {}, {}
        self.pauses = set()
        self.cancelled_batches = set()
        self.pending = queue.Queue()
        self.storage = storage
        self.last_save = {}
        if storage:
            for task in storage.load():
                if task["status"] not in TERMINAL and task["status"] != "paused":
                    task.update(status="interrupted", errorMessage="应用退出时任务未结束，请确认后重试")
                    task["sequence"] += 1
                    storage.save(task)
                self.tasks[task["jobId"]] = task
        for _ in range(3):
            threading.Thread(target=self._run, daemon=True).start()

    def set_concurrency(self, limit):
        if type(limit) is not int or limit not in (1, 2, 3):
            raise ValueError("并发下载数量必须为 1–3")
        with self.condition:
            self.limit = limit
            self.condition.notify_all()
        return {"concurrency": limit}

    def submit(self, task_id: int, args: dict) -> dict:
        if not isinstance(args.get("url"), str) or not args["url"].strip():
            raise ValueError("缺少视频链接")
        quality_selector(args.get("policy"))
        if args.get('conflict_policy','keep_both') not in ('keep_both','reuse_verified','replace'):
            raise ValueError('无效的文件冲突策略')
        if args.get("cookies_from_browser") == "bilio":
            from .auth import snapshot
            snapshot(args.get("auth_session_id"))
        directory = args.get("output_dir")
        if not isinstance(directory, str) or not directory.strip():
            raise ValueError("请选择下载目录")
        config = {key:copy.deepcopy(args[key]) for key in CONFIG_FIELDS if key in args}
        config['policy']={key:value for key,value in config['policy'].items() if key in ('mode','height','quality','format_id')}
        config["url"] = args["url"].strip()
        config["output_dir"] = os.path.abspath(os.path.expanduser(directory.strip()))
        with self.lock:
            if config.get("batchId") in self.cancelled_batches:
                raise ValueError("本批次已取消，不再接收后续任务")
            if task_id in self.tasks:
                raise ValueError("任务 ID 已存在")
            for task in self.tasks.values():
                if task["status"] not in TERMINAL and all(task["config"].get(k) == config.get(k)
                                                         for k in ("url", "output_dir")):
                    raise ValueError("相同视频和保存目录已有活动任务")
            task = {"jobId": task_id, "sequence": 0, "status": "queued", "config": config,
                    "progress": None, "downloadedBytes": 0, "totalBytes": None,
                    "speed": None, "eta": None, "filename": "", "filepath": None,
                    "errorMessage": None, "actualQuality": None}
            task.update(workId=uuid.uuid4().hex, attempt=0)
            self.tasks[task_id] = task
            self.cancels[task_id] = threading.Event()
            self._publish(task)
            self.pending.put((task_id, task["attempt"]))
            return copy.deepcopy(task)

    def _publish(self, task):
        # Lock protects both sequence ordering and the emitted snapshot.
        task["sequence"] += 1
        task_id = task["jobId"]
        now = time.monotonic()
        saved_status, saved_at = self.last_save.get(task_id, (None, 0))
        if self.storage and (saved_status != task["status"] or now - saved_at >= 1):
            try:
                task.pop("storageError", None)
                self.storage.save(task)
                self.last_save[task_id] = (task["status"], now)
                task.pop("storageError", None)
            except Exception:
                task["storageError"] = "任务记录保存失败，请检查应用数据目录或磁盘空间"
        self.emit({"id": task["jobId"], "type": "task", "data": copy.deepcopy(task)})

    def snapshots(self):
        with self.lock:
            return copy.deepcopy(list(self.tasks.values()))

    def temporary(self, task_id, clear=False):
        from .files import temporary_files
        with self.lock:
            task=self.tasks.get(task_id)
            if not task or task['status'] not in TERMINAL | {'paused'}:
                raise ValueError('请先暂停或取消任务，再检查临时文件')
            places=task.get('retainedWorkspaces',[])+[{'output_dir':task['config']['output_dir'],'workId':task.get('workId')}]
            summary={'file_count':0,'bytes':0}
            for place in places:
                result=temporary_files(place['output_dir'],place.get('workId'),clear)
                summary={key:summary[key]+result[key] for key in summary}
            return summary

    def relocate(self, task_id, directory):
        from .files import workspace
        with self.lock:
            task=self.tasks.get(task_id)
            if not task or task['status'] not in {'paused','failed','cancelled','interrupted'}:
                raise ValueError('请先暂停或停止任务，再更换目录')
            if not isinstance(directory,str) or not directory.strip():raise ValueError('请选择保存目录')
            new_id=uuid.uuid4().hex
            root,_=workspace(directory,new_id)
            task.setdefault('retainedWorkspaces',[]).append({'output_dir':task['config']['output_dir'],'workId':task.get('workId')})
            task['config']['output_dir']=str(root);task['workId']=new_id
            task.update(progress=None,downloadedBytes=0,totalBytes=None,filename='')
            self._publish(task)
            return copy.deepcopy(task)

    def clear_finished(self):
        removed = []
        with self.lock:
            for key in list(self.tasks):
                if self.tasks[key]["status"] in TERMINAL:
                    if self.storage:
                        self.storage.remove(key)
                    del self.tasks[key]
                    self.last_save.pop(key, None)
                    removed.append(key)
        return removed

    def cancel(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task or task["status"] in TERMINAL:
                return False
            self.pauses.discard(task_id)
            if task_id in self.cancels:
                self.cancels[task_id].set()
            task["status"] = "cancelled" if task["status"] in ("queued", "paused") else "cancelling"
            self._publish(task)
            return True

    def pause(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if task and task["status"] in ("pausing", "paused"):
                return copy.deepcopy(task)
            if not task or task["status"] not in ("queued", "starting", "downloading"):
                raise ValueError("仅排队或下载中的任务可以暂停；合并期间请等待完成")
            self.pauses.add(task_id)
            self.cancels[task_id].set()
            task["status"] = "paused" if task["status"] == "queued" else "pausing"
            task.update(speed=None, eta=None)
            self._publish(task)
            return copy.deepcopy(task)

    def resume(self, task_id, session_id=None, account_ref=None):
        with self.lock:
            task = self.tasks.get(task_id)
            if task and task["status"] in ("queued", "starting", "downloading", "processing"):
                return copy.deepcopy(task)
            if not task or task["status"] not in ("paused", "interrupted", "failed", "cancelled"):
                raise ValueError("该任务当前不能继续")
            config = task["config"]
            for other in self.tasks.values():
                if other['jobId'] != task_id and other['status'] not in TERMINAL and all(other['config'].get(k) == config.get(k) for k in ('url','output_dir')):
                    raise ValueError("相同视频和目录已有活动任务，请先停止另一任务")
            if config.get("cookies_from_browser") == "bilio":
                from .auth import snapshot
                snapshot(session_id)
                if config.get("account_ref") and config["account_ref"] != account_ref:
                    raise ValueError("请登录原任务的账号后继续")
                if not config.get("account_ref") and config.get("auth_session_id") != session_id:
                    raise ValueError("旧任务缺少账号身份，请重新解析后创建下载")
                config["auth_session_id"] = session_id
            if config.get("batchId") in self.cancelled_batches:
                config.pop("batchId", None)
            task.setdefault("workId", uuid.uuid4().hex)
            task.setdefault('attemptHistory',[]).append({'attempt':task.get('attempt',0)+1,'status':task['status'],'errorCode':task.get('errorCode')})
            task["attempt"] = task.get("attempt", 0) + 1
            task.update(status="queued", errorMessage=None, errorCode=None, progress=None, speed=None, eta=None)
            self.pauses.discard(task_id)
            self.cancels[task_id] = threading.Event()
            self._publish(task)
            self.pending.put((task_id, task["attempt"]))
            return copy.deepcopy(task)

    def remove_finished(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return []
            if task["status"] not in TERMINAL:
                raise ValueError("请先停止任务，再移除记录")
            if self.storage:
                self.storage.remove(task_id)
            del self.tasks[task_id]
            self.last_save.pop(task_id, None)
            return [task_id]

    def cancel_batch(self, batch_id):
        if not isinstance(batch_id, str) or not batch_id:
            raise ValueError("缺少批次 ID")
        with self.lock:
            self.cancelled_batches.add(batch_id)
            for task in list(self.tasks.values()):
                if task["config"].get("batchId") == batch_id:
                    self.cancel(task["jobId"])

    def _progress(self, task_id, data):
        with self.lock:
            task = self.tasks.get(task_id)
            if task is None:
                return
            if task["status"] in TERMINAL or task["status"] in ("cancelling", "pausing", "paused"):
                return
            task["status"] = data.get("status") if data.get("status") in ("starting", "processing") else "downloading"
            downloaded = data.get("downloaded_bytes") or 0
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            task.update(downloadedBytes=downloaded, totalBytes=total, speed=data.get("speed"), eta=data.get("eta"))
            if data.get("filename"):
                task["filename"] = os.path.basename(data["filename"])
            percent = data.get("overall_percent")
            if percent is not None:
                task["progress"] = max(task["progress"] or 0, min(99, percent))
            if data.get("actual_quality"):
                task["actualQuality"] = data["actual_quality"]
            self._publish(task)

    def _run(self):
        while True:
            task_id, attempt = self.pending.get()
            owns_attempt = False
            try:
                with self.lock:
                    while self.running >= self.limit:
                        self.condition.wait()
                    task = self.tasks.get(task_id)
                    if task is None or task["status"] != "queued" or task.get("attempt", 0) != attempt:
                        continue
                    owns_attempt = True
                    self.running += 1
                    task["status"] = "starting"
                    self._publish(task)
                    cancel = self.cancels[task_id]
                    config = copy.deepcopy(task["config"])
                extra = {}
                if config.get("cookies_from_browser") == "bilio":
                    from .auth import snapshot
                    extra["session_cookies"] = snapshot(config.get("auth_session_id"))
                path = self.download(url=config["url"], policy=config["policy"],
                    output_dir=config["output_dir"], cookies_from_browser=config.get("cookies_from_browser"),
                    cancel_event=cancel, progress_callback=lambda data: self._progress(task_id, data), work_id=task["workId"], display_title=config.get('title'), conflict_policy=config.get('conflict_policy','keep_both'), **extra)
                with self.lock:
                    if cancel.is_set():
                        raise CancelRequested()
                    if not path or not os.path.isfile(path):
                        raise RuntimeError("下载结束，但未找到最终文件")
                    task.update(status="completed", filepath=path, filename=os.path.basename(path),
                                progress=100, downloadedBytes=os.path.getsize(path),
                                totalBytes=os.path.getsize(path), speed=None, eta=None)
                    self._publish(task)
            except Exception as exc:
                with self.lock:
                    task = self.tasks.get(task_id)
                    if task:
                        cancel = self.cancels.get(task_id)
                        task["status"] = "paused" if task_id in self.pauses else "cancelled" if isinstance(exc, CancelRequested) or (cancel and cancel.is_set()) else "failed"
                        from .errors import failure
                        error = failure(exc)
                        task["errorMessage"] = error["message"] if task["status"] == "failed" else None
                        task["errorCode"] = error["code"] if task["status"] == "failed" else None
                        task.update(speed=None, eta=None)
                        self._publish(task)
            finally:
                with self.lock:
                    if owns_attempt:
                        self.running -= 1
                        self.condition.notify_all()
                    if owns_attempt and self.tasks.get(task_id, {}).get("attempt", 0) == attempt:
                        self.cancels.pop(task_id, None)
                self.pending.task_done()

    def close(self):
        with self.lock:
            for cancel in self.cancels.values():
                cancel.set()
