"""Run measured dev benchmark against an isolated temporary local backend."""

import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="memory-harness-eval-") as folder:
        env = os.environ.copy()
        for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET", "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BACKUP_MODEL"):
            env.pop(name, None)
        env.update(
            ADMIN_TOKEN=secrets.token_urlsafe(32),
            STAFF_PASSWORD=secrets.token_urlsafe(32),
            HARNESS_DB=str(Path(folder) / "harness.sqlite3"),
            LLM_BACKEND="rules",
            IDENTITY_HISTORY_ENABLED="false",
        )
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        with open(Path(folder) / "server.log", "w+") as log:
            server = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "harness.api:app", "--host", "127.0.0.1", "--port", str(port)],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                for _ in range(100):
                    if server.poll() is not None:
                        log.seek(0)
                        print(log.read(), file=sys.stderr)
                        return 2
                    try:
                        request = urllib.request.Request(
                            base_url + "/admin/state", headers={"Authorization": "Bearer " + env["ADMIN_TOKEN"]}
                        )
                        with urllib.request.urlopen(request, timeout=1):
                            break
                    except (urllib.error.URLError, TimeoutError):
                        time.sleep(0.1)
                else:
                    print("Isolated backend did not start", file=sys.stderr)
                    return 2
                command = [
                    sys.executable,
                    "-m",
                    "eval.runner",
                    "--base-url",
                    base_url,
                    "--backend",
                    "rules",
                    "--split",
                    "dev",
                    *sys.argv[1:],
                ]
                return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == "__main__":
    raise SystemExit(main())
