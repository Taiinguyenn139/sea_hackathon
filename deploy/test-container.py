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
        webhook_secret = secrets.token_urlsafe(48)
        env = root / "runtime.env"
        env.write_text("ADMIN_TOKEN=" + secrets.token_urlsafe(32) + "\nSTAFF_PASSWORD=" + staff + "\nTELEGRAM_WEBHOOK_SECRET=" + webhook_secret + "\nHARNESS_RELEASE_GATE=/release/ready\nDEPLOY_REVISION=container-smoke\n")
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

            def request(path, body=None, token=None, extra_headers=None):
                headers = {"Content-Type": "application/json", **(extra_headers or {})}
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
            verified = request(f"/sessions/{sid}/verify", {"phone": session["user_id"]}, token)
            assert verified["identity_status"] == "verified"
            reply = request(f"/sessions/{sid}/messages", {"text": "Tôi muốn vay mua nhà 2 tỷ trong 20 năm, thu nhập 45 triệu, không có nợ, giá nhà 3 tỷ", "client_message_id": "smoke-1"}, token)
            assert "2 tỷ" in reply["reply"] and "7,1" in reply["reply"] and reply["used_fact_ids"]

            def telegram(update_id, text=None, contact=None):
                message = {"date": int(time.time()), "chat": {"id": 424242, "type": "private"},
                           "from": {"id": 424242, "is_bot": False, "first_name": "Smoke"}}
                if text is not None:
                    message["text"] = text
                if contact is not None:
                    message["contact"] = contact
                request("/webhooks/telegram", {"update_id": update_id, "message": message},
                        extra_headers={"X-Telegram-Bot-Api-Secret-Token": webhook_secret})
                # HTTP acknowledgment precedes durable inbox processing.
                for _ in range(30):
                    state = json.loads(docker("exec", name, "python", "-c", """
import json, sqlite3, sys
c = sqlite3.connect('/data/harness.sqlite3')
row = c.execute('SELECT status,error FROM provider_events WHERE id=?', (sys.argv[1],)).fetchone()
print(json.dumps(row))
""", f"telegram:{update_id}"))
                    if state and state[0] == "processed":
                        assert state[1] is None, "Telegram event was rejected"
                        return
                    time.sleep(0.2)
                raise RuntimeError("Telegram inbox processing did not complete")

            def guest_state():
                return json.loads(docker("exec", name, "python", "-c", """
import json, sqlite3
c = sqlite3.connect('/data/harness.sqlite3')
customer = c.execute("SELECT id FROM guest_customers WHERE channel='telegram' AND external_id='424242'").fetchone()[0]
reply = c.execute("SELECT text FROM outbox WHERE target='424242' ORDER BY rowid DESC LIMIT 1").fetchone()[0]
income = c.execute("SELECT value FROM facts WHERE customer_id=? AND key='income' AND status='active'", (customer,)).fetchone()[0]
print(json.dumps({'customer': customer, 'reply': reply, 'income': income}))
"""))

            telegram(100, "Tôi cần thẻ tín dụng, thu nhập 40 triệu, không có nợ")
            guest = guest_state()
            assert guest["customer"].startswith("guest_") and "100 triệu" in guest["reply"]
            docker("restart", name)
            # Docker may reassign an ephemeral host port when restarting.
            base = "http://" + docker("port", name, "8080/tcp").splitlines()[0]
            healthy()
            turns = request(f"/sessions/{sid}/turns", token=token)
            assert turns.get("turns") or turns.get("items"), "SQLite state lost on restart"
            telegram(101, "Thu nhập chính xác 50 triệu")
            restored_guest = guest_state()
            assert restored_guest["customer"] == guest["customer"] and restored_guest["income"] == "50000000"
            telegram(102, contact={"phone_number": "0901234567", "user_id": 424242})
            phone_challenge = request("/auth/phone/start", {"phone": "+84901234567"})
            code = docker("exec", name, "python", "-c", """
import re, sqlite3, sys
from bank_tools.service import ident
c = sqlite3.connect('/data/harness.sqlite3')
key = ident('out_', ['phone-login', sys.argv[1]])
text = c.execute('SELECT text FROM outbox WHERE id=?', (key,)).fetchone()[0]
print(re.search(r'\\b(\\d{6})\\b', text)[1])
""", phone_challenge["challenge_id"])
            phone_session = request("/auth/phone/confirm", {"challenge_id": phone_challenge["challenge_id"], "code": code})
            assert phone_session["identity_status"] == "identified"
            assert phone_session["brief"]["customer_id"] == guest["customer"]
            assert any(f["key"] == "income" and f["value"] == "50000000" for f in phone_session["brief"]["needs"])
            continued = request(f"/sessions/{phone_session['session_id']}/messages",
                                {"text": "Tiếp tục tư vấn thẻ tín dụng", "client_message_id": "phone-continue"},
                                phone_session["session_token"])
            assert "100 triệu" in continued["reply"]
            with urllib.request.urlopen(base + "/", timeout=10) as response:
                assert "<html" in response.read().decode().lower(), "Frontend missing"
            print("PASS: production read-only container, bank quote, Telegram onboarding/update, persistent restart, phone-keyed context on web, static frontend")
        finally:
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
