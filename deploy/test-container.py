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


def main(demo_phone=False):
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
        with env.open("a") as config:
            config.write("DEMO_PHONE_IDENTITY=" + str(demo_phone).lower() + "\n")
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

            def telegram(update_id, text=None, contact=None, chat_id=424242):
                message = {"date": int(time.time()), "chat": {"id": chat_id, "type": "private"},
                           "from": {"id": chat_id, "is_bot": False, "first_name": "Smoke"}}
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
c = sqlite3.connect('file:/data/harness.sqlite3?mode=ro', uri=True)
row = c.execute('SELECT status,error FROM provider_events WHERE id=?', (sys.argv[1],)).fetchone()
print(json.dumps(row))
""", f"telegram:{update_id}"))
                    if state and state[0] == "processed":
                        assert state[1] is None, "Telegram event was rejected"
                        return
                    time.sleep(0.2)
                raise RuntimeError(f"Telegram inbox processing did not complete: {state}")

            def guest_state(chat_id=424242):
                return json.loads(docker("exec", name, "python", "-c", """
import json, sqlite3, sys
c = sqlite3.connect('file:/data/harness.sqlite3?mode=ro', uri=True)
customer = c.execute("SELECT customer_id FROM sessions WHERE channel='telegram' AND external_id=? AND status='active' ORDER BY rowid DESC LIMIT 1", (sys.argv[1],)).fetchone()[0]
reply, markup = c.execute("SELECT text,reply_markup FROM outbox WHERE target=? ORDER BY rowid DESC LIMIT 1", (sys.argv[1],)).fetchone()
income = c.execute("SELECT value FROM facts WHERE customer_id=? AND key='income' AND status='active'", (customer,)).fetchone()
print(json.dumps({'customer': customer, 'reply': reply, 'markup': json.loads(markup) if markup else None, 'income': income[0] if income else None}))
""", str(chat_id)))

            # New onboarding requires the sender's contact before consulting.
            telegram(100, "/start")
            welcome = guest_state()
            assert welcome["markup"]["keyboard"][0][0]["request_contact"] is True
            if not demo_phone:
                telegram(101, "Thu nhập 999 triệu")
                assert guest_state()["income"] is None, "Unbound chat created financial memory"
            telegram(102, contact={"phone_number": "0901234567", "user_id": 424242})
            assert guest_state()["markup"] == {"remove_keyboard": True}
            telegram(103, "Tôi cần thẻ tín dụng, thu nhập 40 triệu, không có nợ")
            guest = guest_state()
            assert guest["customer"].startswith("guest_") and "100 triệu" in guest["reply"]
            assert guest["income"] == "40000000"
            docker("restart", name)
            # Docker may reassign an ephemeral host port when restarting.
            base = "http://" + docker("port", name, "8080/tcp").splitlines()[0]
            healthy()
            turns = request(f"/sessions/{sid}/turns", token=token)
            assert turns.get("turns") or turns.get("items"), "SQLite state lost on restart"
            telegram(104, "Thu nhập chính xác 50 triệu")
            restored_guest = guest_state()
            assert restored_guest["customer"] == guest["customer"] and restored_guest["income"] == "50000000"
            phone_session = request("/auth/phone/login", {"phone": "+84901234567"})
            assert phone_session["identity_status"] == "identified"
            assert phone_session["brief"]["customer_id"] == guest["customer"]
            assert any(f["key"] == "income" and f["value"] == "50000000" for f in phone_session["brief"]["needs"])
            continued = request(f"/sessions/{phone_session['session_id']}/messages",
                                {"text": "Tiếp tục tư vấn thẻ tín dụng", "client_message_id": "phone-continue"},
                                phone_session["session_token"])
            assert "100 triệu" in continued["reply"]
            request(f"/sessions/{phone_session['session_id']}/messages",
                    {"text": "Thu nhập chính xác 53 triệu", "client_message_id": "phone-update"},
                    phone_session["session_token"])
            telegram(105, "Tiếp tục tư vấn thẻ tín dụng")
            assert guest_state()["income"] == "53000000"

            # Reverse order: a web-first profile must retain its identity and
            # memory when Telegram later supplies the same normalized phone.
            web_first = request("/demo/phone/session" if demo_phone else "/auth/phone/login", {"phone": "0901234568"})
            request(f"/sessions/{web_first['session_id']}/messages",
                    {"text": "Tôi cần thẻ tín dụng, thu nhập 61 triệu, không có nợ", "client_message_id": "web-first"},
                    web_first["session_token"])
            telegram(106, "/start", chat_id=424243)
            telegram(107, contact={"phone_number": "+84901234568", "user_id": 424243}, chat_id=424243)
            telegram(108, "Tiếp tục tư vấn thẻ tín dụng", chat_id=424243)
            linked = guest_state(424243)
            assert linked["customer"] == web_first["brief"]["customer_id"]
            assert linked["income"] == "61000000" and "100 triệu" in linked["reply"]
            challenge_count = docker("exec", name, "python", "-c",
                                     "import sqlite3; print(sqlite3.connect('file:/data/harness.sqlite3?mode=ro', uri=True).execute('SELECT count(*) FROM phone_challenges').fetchone()[0])")
            assert challenge_count == "0", "Phone-only onboarding unexpectedly requested an OTP"
            with urllib.request.urlopen(base + "/", timeout=10) as response:
                assert "<html" in response.read().decode().lower(), "Frontend missing"
            print(f"PASS (DEMO_PHONE_IDENTITY={demo_phone}): production read-only container, bank quote, contact-first Telegram onboarding, persistent restart, phone-only web login, bidirectional memory, web-first linking, static frontend")
        finally:
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
    main(demo_phone=True)
