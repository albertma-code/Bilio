"""Live packaged-engine acceptance with private temporary history, no credential logs."""
import argparse
import json
import os
import select
import subprocess
import tempfile
import time


class Engine:
    def __init__(self, binary, database):
        self.process = subprocess.Popen([binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env={**os.environ, "BILIO_TASK_DB": database}, bufsize=0)
        self.buffer = b""
        self.messages = []
        self.tasks = {}
        self.sequence = 100
        self.until(lambda message: message.get("type") == "ready")

    def send(self, cmd, args=None, rid=None):
        self.sequence += 1
        rid = rid or self.sequence
        self.process.stdin.write((json.dumps({"id": rid, "cmd": cmd, "args": args or {}}) + "\n").encode())
        self.process.stdin.flush()
        return rid

    def until(self, predicate, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                message = json.loads(line)
                self.messages.append(message)
                if message.get("type") == "task":
                    task = message["data"]
                    self.tasks[task["jobId"]] = task
                if predicate(message):
                    return message
            else:
                if select.select([self.process.stdout], [], [], 0.2)[0]:
                    data = os.read(self.process.stdout.fileno(), 65536)
                    if not data:
                        raise RuntimeError("packaged engine exited unexpectedly")
                    self.buffer += data
        raise TimeoutError("packaged engine response timed out")

    def request(self, cmd, args=None):
        rid = self.send(cmd, args)
        response = self.until(lambda message: message.get("id") == rid and message.get("type") in ("result", "error", "pong"))
        if response["type"] == "error":
            raise RuntimeError(response.get("error", "request failed"))
        return response.get("data", response)

    def terminal(self, job_id):
        terminal = ("completed", "cancelled", "failed", "interrupted")
        if self.tasks.get(job_id, {}).get("status") not in terminal:
            self.until(lambda message: self.tasks.get(job_id, {}).get("status") in terminal, timeout=180)
        return self.tasks[job_id]

    def close(self, force=False):
        if self.process.poll() is None:
            if force:
                self.process.terminate()
            else:
                self.process.stdin.close()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(5)
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        self.process.stdout.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary")
    parser.add_argument("--output", required=True)
    parser.add_argument("--browser", default="chrome")
    parser.add_argument("--url", required=True, help="Explicitly authorized bangumi episode URL; this runner downloads real media")
    args = parser.parse_args()
    url = args.url
    config = {"url": url, "policy": {"mode": "exact", "format_id": "30077"},
              "title": "Live packaged acceptance", "formatLabel": "1080P HEVC", "cookies_from_browser": args.browser,
              "output_dir": os.path.abspath(args.output)}
    with tempfile.TemporaryDirectory() as directory:
        database = os.path.join(directory, "tasks.sqlite3")
        engine = Engine(args.binary, database)
        try:
            status = engine.request("check_cookies", {"cookies_from_browser": args.browser})
            assert status["logged_in"] and status["is_vip"], "member session not available"
            print("PACKAGED member login PASS", flush=True)
            parsed = engine.request("parse", {"url": url, "cookies_from_browser": args.browser})
            assert any(f.get("width") == 3840 for f in parsed["formats"]), "4K format not found"
            assert parsed.get("duration") and parsed["duration"] > 0
            print("PACKAGED member formats + episode duration PASS", flush=True)
            engine.request("download", {**config, "job_id": 1})
            task = engine.terminal(1)
            assert task["status"] == "completed", task.get("errorMessage")
            assert os.path.isfile(task["filepath"])
            print("PACKAGED download + final file PASS", flush=True)
            engine.request("download", {**config, "job_id": 6, "policy": {"mode": "exact", "format_id": "99999999"}})
            failed = engine.terminal(6)
            assert failed["status"] == "failed" and "画质" in failed["errorMessage"]
            engine.request("download", {**config, "job_id": 7, "retryOf": 6})
            assert engine.terminal(7)["status"] == "completed"
            print("PACKAGED unavailable format + retry PASS", flush=True)
            batch = "packaged-cancel"
            for job_id in (2, 3, 4):
                engine.request("download", {**config, "job_id": job_id, "batchId": batch,
                    "output_dir": os.path.join(args.output, "cancel-" + str(job_id))})
            assert engine.tasks[4]["status"] == "queued"
            started = time.monotonic()
            engine.request("cancel_batch", {"batch_id": batch})
            for job_id in (2, 3, 4):
                assert engine.terminal(job_id)["status"] == "cancelled"
            assert time.monotonic() - started < 5
            print("PACKAGED bounded queue + batch cancel PASS", flush=True)
            engine.request("download", {**config, "job_id": 5,
                "output_dir": os.path.join(args.output, "interrupted")})
            engine.until(lambda message: engine.tasks.get(5, {}).get("status") == "downloading")
            engine.close(force=True)
            engine = Engine(args.binary, database)
            recovered = engine.request("tasks")
            interrupted = next(task for task in recovered if task["jobId"] == 5)
            assert interrupted["status"] == "interrupted"
            assert interrupted["config"]["policy"] == config["policy"]
            assert not any(task["status"] in ("queued", "starting", "downloading") for task in recovered)
            print("PACKAGED crash recovery without automatic replay PASS", flush=True)
            result = engine.request("clear_finished")
            assert set(result["removed"]) == {1, 2, 3, 4, 5, 6, 7}
            assert engine.request("tasks") == []
            print("PACKAGED history cleanup PASS", flush=True)
        finally:
            engine.close()


if __name__ == "__main__":
    main()
