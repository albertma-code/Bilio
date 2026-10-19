"""Nonblocking JSONL command dispatch; workers own slow operations."""
from __future__ import annotations

import threading
import time
import os
from typing import Any, Iterator

from .io import emit
from .jobs import DownloadQueue
from .storage import TaskStorage
from .source import normalize_source
from .worker import isolated_download
from .errors import user_error
from . import auth
from .ytdlp import CancelRequested, check_cookies, download_video, parse_url

_storage_path = os.environ.get("BILIO_TASK_DB")
_downloads = DownloadQueue(isolated_download, emit, storage=TaskStorage(_storage_path) if _storage_path else None)
_metadata_slots = threading.BoundedSemaphore(2)
_metadata_lock = threading.Lock()
_metadata: dict[Any, threading.Event] = {}


def _start_metadata(rid, cmd, args):
    if not _metadata_slots.acquire(blocking=False):
        raise ValueError("解析或账号检测正在进行，请稍后重试")
    cancel = threading.Event()
    with _metadata_lock:
        if rid in _metadata:
            _metadata_slots.release()
            raise ValueError("任务 ID 已存在")
        _metadata[rid] = cancel

    def worker():
        try:
            if cmd == "parse":
                def on_entry(index, total, entry):
                    if cancel.is_set():
                        raise CancelRequested()
                    emit({"id": rid, "type": "parse_progress", "data": {
                        "index": index, "total": total, "entry": entry}})
                extra = {"session_cookies": auth.snapshot(args.get("auth_session_id"))} if args.get("cookies_from_browser") == "bilio" else {}
                data = parse_url(args["url"], args.get("cookies_from_browser"), on_entry, cancel, **extra)
            elif cmd == "check_session":
                data = check_cookies("bilio", session_cookies=auth.validate_cookies(args.get("cookies")))
            elif cmd=='check_update':
                from .updates import check_latest
                data=check_latest()
            else:
                data = check_cookies(args.get("cookies_from_browser"))
            if cancel.is_set():
                raise CancelRequested()
            emit({"id": rid, "type": "result", "data": data})
        except Exception as exc:
            emit({"id": rid, "type": "error", "error": "已取消解析" if cancel.is_set() else user_error(exc)})
        finally:
            with _metadata_lock:
                _metadata.pop(rid, None)
            _metadata_slots.release()
    threading.Thread(target=worker, daemon=True).start()


def dispatch(req: dict[str, Any]) -> Iterator[dict[str, Any]]:
    cmd, rid, args = req.get("cmd"), req.get("id"), req.get("args") or {}
    if cmd == "ping":
        yield {"id": rid, "type": "pong", "ts": time.time()}
        return
    if cmd in ("parse", "check_cookies", "check_session", "check_update"):
        if cmd == "parse" and (not isinstance(args.get("url"), str) or not args["url"].strip()):
            raise ValueError("缺少视频链接")
        if cmd == "parse":
            args["url"] = normalize_source(args["url"])
        _start_metadata(rid, cmd, args)
        return
    if cmd == "set_session":
        auth.install(args.get("session_id"), args.get("cookies"))
        yield {"id": rid, "type": "result", "data": {"installed": True}}
        return
    if cmd == "clear_session":
        if auth.clear(args.get("session_id")):
            for task in _downloads.snapshots():
                if task["config"].get("cookies_from_browser") == "bilio":
                    _downloads.cancel(task["jobId"])
            with _metadata_lock:
                for cancel in _metadata.values():
                    cancel.set()
        yield {"id": rid, "type": "result", "data": {"cleared": True}}
        return
    if cmd == "download":
        args["url"] = normalize_source(args.get("url"))
        data = _downloads.submit(args["job_id"], args)
    elif cmd == 'diagnostics':
        import platform
        from yt_dlp.version import __version__ as yt_version
        from . import __version__
        from collections import Counter
        snapshots = _downloads.snapshots()
        allowed_states={'queued','starting','downloading','processing','paused','pausing','cancelling','completed','cancelled','failed','interrupted'}
        allowed_errors={'disk_full','permission','auth','format','network','file','unknown'}
        data = {'engine_version':__version__,'python':platform.python_version(),'yt_dlp':yt_version,
                'states':dict(Counter(t['status'] if t['status'] in allowed_states else 'unknown' for t in snapshots)),
                'errors':dict(Counter(t.get('errorCode') if t.get('errorCode') in allowed_errors else 'unknown' for t in snapshots if t['status']=='failed')),
                'unreadable_records':len(_downloads.storage.warnings) if _downloads.storage else 0}
    elif cmd=='storage_status':
        data={'unreadable_records':len(_downloads.storage.warnings) if _downloads.storage else 0}
    elif cmd == "tasks":
        data = _downloads.snapshots()
    elif cmd == "pause":
        data = _downloads.pause(args.get("job_id"))
    elif cmd == "set_concurrency":
        data = _downloads.set_concurrency(args.get("concurrency"))
    elif cmd == "resume":
        data = _downloads.resume(args.get("job_id"), args.get("auth_session_id"), args.get("account_ref"))
    elif cmd in ('temp_info', 'clear_temp'):
        data = _downloads.temporary(args.get('job_id'), cmd == 'clear_temp')
    elif cmd=='relocate':
        data=_downloads.relocate(args.get('job_id'),args.get('output_dir'))
    elif cmd == "cancel_batch":
        _downloads.cancel_batch(args.get("batch_id"))
        data = {"requested": True}
    elif cmd == "clear_finished":
        data = {"removed": _downloads.clear_finished()}
    elif cmd == "remove_finished":
        data = {"removed": _downloads.remove_finished(args.get("job_id"))}
    elif cmd == "cancel":
        job_id = args.get("job_id")
        with _metadata_lock:
            cancel = _metadata.get(job_id)
            if cancel:
                cancel.set()
        data = {"requested": bool(cancel) or _downloads.cancel(job_id)}
    else:
        raise ValueError(f"unknown cmd: {cmd!r}")
    yield {"id": rid, "type": "result", "data": data}


def shutdown():
    _downloads.close()
    with _metadata_lock:
        for cancel in _metadata.values():
            cancel.set()
