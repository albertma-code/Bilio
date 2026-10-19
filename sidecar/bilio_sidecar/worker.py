"""Isolate each download so cancel/parent exit also stop ffmpeg children."""
from __future__ import annotations

import multiprocessing
import os
import signal
import threading
import time

from .ytdlp import CancelRequested, download_video
from .errors import user_error
from .files import download_safely


def _watch_parent(parent_pid):
    while True:
        time.sleep(0.5)
        if os.getppid() != parent_pid:
            os.killpg(os.getpgrp(), signal.SIGTERM)
            return


def download_worker(connection, config):
    cancel = threading.Event()
    try:
        path = download_safely(download_video, **config, cancel_event=cancel,
                              progress_callback=lambda data: connection.send({"type": "progress", "data": data}))
        connection.send({"type": "completed", "path": path})
    except Exception as exc:
        connection.send({"type": "failed", "error": user_error(exc)})


def _child_main(connection, config, target):
    if os.name == "posix":
        parent_pid = os.getppid()
        os.setsid()
        # multiprocessing may terminate just this PID during interpreter exit.
        # Stop the entire isolated group so a running ffmpeg cannot outlive it.
        signal.signal(signal.SIGTERM, lambda *_: os.killpg(os.getpgrp(), signal.SIGKILL))
        threading.Thread(target=_watch_parent, args=(parent_pid,), daemon=True).start()
    connection.send({"type": "worker_ready"})
    try:
        target(connection, config)
    finally:
        connection.close()


def _stop(process, group_ready):
    if not process.is_alive() and not (os.name == "posix" and group_ready):
        return
    if os.name == "posix" and group_ready:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    else:
        process.terminate()
    process.join(2)
    if process.is_alive():
        if os.name == "posix" and group_ready:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.join(2)


def isolated_download(*, cancel_event, progress_callback, _target=download_worker, **config):
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_child_main, args=(sender, config, _target), daemon=True)
    group_ready = False
    process.start()
    sender.close()
    last_update = time.monotonic()
    phase = "starting"
    try:
        while True:
            if cancel_event.is_set():
                raise CancelRequested()
            if receiver.poll(0.1):
                try:
                    message = receiver.recv()
                except EOFError:
                    raise RuntimeError("下载工作进程意外退出，可重试此任务")
                last_update = time.monotonic()
                kind = message.get("type")
                if kind == "worker_ready":
                    group_ready = True
                elif kind == "progress":
                    phase = message["data"].get("status", phase)
                    progress_callback(message["data"])
                elif kind == "completed":
                    return message.get("path")
                elif kind == "failed":
                    raise RuntimeError(message.get("error") or "下载失败")
            elif not process.is_alive():
                raise RuntimeError("下载工作进程意外退出，可重试此任务")
            if time.monotonic() - last_update > (900 if phase == "processing" else 120):
                raise RuntimeError("下载长时间没有响应，已停止任务，请检查网络后重试")
    finally:
        _stop(process, group_ready)
        receiver.close()
        process.join(2)
        process.close()
