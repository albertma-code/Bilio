"""Run manually against a packaged sidecar; does not access the network."""
import json
import os
import subprocess
import sys
import tempfile


def main():
    with tempfile.TemporaryDirectory() as directory:
        env = {**os.environ, "BILIO_TASK_DB": os.path.join(directory, "tasks.sqlite3")}
        messages = [
            {"id": 1, "cmd": "ping"},
            {"id": 2, "cmd": "tasks"},
            {"id": 3, "cmd": "parse", "args": {"url": "file:///tmp/not-allowed"}},
            {"id": 4, "cmd": "cancel", "args": {"job_id": 999}},
        ]
        result = subprocess.run([sys.argv[1]], input="".join(json.dumps(m) + "\n" for m in messages),
                                text=True, capture_output=True, timeout=30, env=env)
        assert result.returncode == 0, result.stderr
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        assert replies[0]["type"] == "ready" and replies[0]["version"] == "0.4.0", replies
        by_id = {reply["id"]: reply for reply in replies}
        assert by_id[1]["type"] == "pong", replies
        assert by_id[2]["data"] == [], replies
        assert by_id[3]["type"] == "error", replies
        assert by_id[4]["data"]["requested"] is False, replies
        assert os.path.isfile(env["BILIO_TASK_DB"])
        print("packaged sidecar: ready, ping, task storage, source validation, cancel, clean exit PASS")


if __name__ == "__main__":
    main()
