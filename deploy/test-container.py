"""Exercise the production container with ephemeral credentials and data only."""
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout.strip()


def main():
    name = "harness-smoke-" + secrets.token_hex(4)
    with tempfile.TemporaryDirectory(prefix="harness-container-", dir="/private/tmp" if Path("/private/tmp").exists() else None) as folder:
        root = Path(folder)
        data, gate = root / "data", root / "release"
        data.mkdir(mode=0o777)
        data.chmod(0o777)  # Only disposable test data; production uses uid 10001.
        gate.mkdir(mode=0o755)
        staff = secrets.token_urlsafe(32)
        env = root / "runtime.env"
        env.write_text("ADMIN_TOKEN=" + secrets.token_urlsafe(32) + "\nSTAFF_PASSWORD=" + staff + "\nHARNESS_RELEASE_GATE=/release/ready\nDEPLOY_REVISION=container-smoke\n")
        env.chmod(0o600)
        try:
            docker("run", "-d", "--name", name, "--env-file", str(env), "--read-only",
                   "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges", "-p", "127.0.0.1::8080",
                   "--mount", f"type=bind,source={data},target=/data",
                   "--mount", f"type=bind,source={gate},target=/release,readonly",
                   "memory-harness:deploy-check")
            binding = docker("port", name, "8080/tcp").splitlines()[0]
            base = "http://" + binding

            def request(path, body=None, token=None):
                headers = {"Content-Type": "application/json"}
                if token:
                    headers["Authorization"] = "Bearer " + token
                req = urllib.request.Request(base + path, None if body is None else json.dumps(body).encode(), headers)
                with urllib.request.urlopen(req, timeout=10) as response:
                    return json.load(response)

            def healthy():
                last_error = None
                for _ in range(60):
                    try:
                        assert request("/healthz")["revision"] == "container-smoke"
                        return
                    except (OSError, AssertionError) as error:
                        last_error = error
                        time.sleep(1)
                raise RuntimeError("Production container failed readiness") from last_error

            healthy()
            try:
                request("/sessions", {"mode": "harness", "demo_profile": "hoa"})
                raise AssertionError("Candidate accepted mutation before promotion")
            except urllib.error.HTTPError as error:
                assert error.code == 503
            (gate / "ready").touch()
            session = request("/sessions", {"mode": "harness", "demo_profile": "hoa"})
            sid, token = session["session_id"], session["session_token"]
            request(f"/sessions/{sid}/verify", {"dob": "1988-05-12"}, token)
            reply = request(f"/sessions/{sid}/messages", {"text": "Tôi muốn vay mua nhà 2 tỷ trong 20 năm, thu nhập 45 triệu, không có nợ, giá nhà 3 tỷ", "client_message_id": "smoke-1"}, token)
            assert "2 tỷ" in reply["reply"] and "7,1" in reply["reply"] and reply["used_fact_ids"]
            docker("restart", name)
            # Docker may reassign an ephemeral host port when restarting.
            base = "http://" + docker("port", name, "8080/tcp").splitlines()[0]
            healthy()
            turns = request(f"/sessions/{sid}/turns", token=token)
            assert turns.get("turns") or turns.get("items"), "SQLite state lost on restart"
            with urllib.request.urlopen(base + "/", timeout=10) as response:
                assert "<html" in response.read().decode().lower(), "Frontend missing"
            print("PASS: production read-only container, health/revision, promotion gate, sourced quote, persistent restart, static frontend")
        finally:
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
