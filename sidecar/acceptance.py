"""Explicit live-download acceptance runner; never prints credentials/media URLs."""
import argparse
import json
import multiprocessing
import os
import subprocess
import threading
import time

from bilio_sidecar.jobs import DownloadQueue
from bilio_sidecar.worker import isolated_download
from imageio_ffmpeg import get_ffmpeg_exe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--format", required=True)
    parser.add_argument("--browser")
    parser.add_argument("--output", required=True)
    parser.add_argument("--cancel-after", type=float)
    args = parser.parse_args()
    previous, printed_at, cancellation = None, 0, None
    observed = []
    timer = None

    def event(message):
        nonlocal previous, printed_at, cancellation, timer
        task = message["data"]
        status = task["status"]
        percent = task.get("progress")
        if percent is not None:
            observed.append(percent)
        now = time.monotonic()
        if status != previous or now - printed_at > 15:
            print(json.dumps({"status": status, "progress": percent,
                "bytes": task["downloadedBytes"], "actual_quality": task.get("actualQuality")}, ensure_ascii=False), flush=True)
            previous, printed_at = status, now
        if args.cancel_after is not None and status == "downloading" and timer is None:
            def cancel():
                nonlocal cancellation
                cancellation = time.monotonic()
                jobs.cancel(1)
            timer = threading.Timer(args.cancel_after, cancel)
            timer.start()

    jobs = DownloadQueue(isolated_download, event, concurrency=1)
    jobs.submit(1, {"url": args.url, "policy": {"mode": "exact", "format_id": args.format},
        "title": "Live acceptance", "formatLabel": args.format,
        "output_dir": os.path.abspath(args.output), "cookies_from_browser": args.browser})
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        task = jobs.snapshots()[0]
        if task["status"] in ("completed", "cancelled", "failed"):
            break
        time.sleep(0.1)
    else:
        jobs.cancel(1)
        raise RuntimeError("Live acceptance timed out")
    jobs.pending.join()
    if timer:
        timer.cancel()
    task = jobs.snapshots()[0]
    expected = "cancelled" if args.cancel_after is not None else "completed"
    if task["status"] != expected:
        raise RuntimeError(task.get("errorMessage") or task["status"])
    assert observed == sorted(observed), "progress went backwards"
    if expected == "cancelled":
        assert cancellation is not None and time.monotonic() - cancellation < 5, "cancel too slow"
        print("LIVE CANCEL PASS", flush=True)
    else:
        path = task["filepath"]
        assert os.path.isfile(path) and os.path.getsize(path) > 0
        ffmpeg = get_ffmpeg_exe()
        result = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-map", "0:v:0", "-map", "0:a:0",
                                 "-t", "3", "-f", "null", "-"], capture_output=True, timeout=60)
        assert result.returncode == 0, "output audio/video decode failed"
        print(json.dumps({"result": "LIVE DOWNLOAD PASS", "size": os.path.getsize(path),
                          "file": os.path.basename(path)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
