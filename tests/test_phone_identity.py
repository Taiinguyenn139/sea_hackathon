import json
import re
import sqlite3

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from harness.api import create_app
from harness.config import Settings
from harness.db import Database
from harness.phone_identity import normalize_phone


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        db_path=str(tmp_path / "phone.sqlite3"),
        admin_token="test-admin",
        staff_password="test-staff",
        telegram_token="",
        telegram_secret="test-webhook",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def update(client, number, text=None, contact=None, target=111):
    message = {
        "date": 100 + number,
        "chat": {"id": target, "type": "private"},
        "from": {"id": target, "is_bot": False, "first_name": "Khách điện thoại"},
    }
    if text is not None:
        message["text"] = text
    if contact is not None:
        message["contact"] = contact
    response = client.post(
        "/webhooks/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-webhook"},
        json={"update_id": number, "message": message},
    )
    assert response.status_code == 200


def register(client):
    update(client, 1, contact={"phone_number": "0901234567", "user_id": 111})
    update(client, 2, "Tôi cần thẻ tín dụng, thu nhập 40 triệu, không có nợ")
    customer = client.app.state.harness.db.one("SELECT customer_id FROM sessions WHERE external_id='111'")[
        "customer_id"
    ]
    return customer


def challenge(client, phone="0901234567"):
    result = client.post("/auth/phone/start", json={"phone": phone})
    assert result.status_code == 200
    assert set(result.json()) == {"challenge_id", "expires_in"}
    row = client.app.state.harness.db.one("SELECT text FROM outbox ORDER BY rowid DESC LIMIT 1")
    code = re.search(r"\b(\d{6})\b", row["text"])[1]
    return {"challenge_id": result.json()["challenge_id"], "code": code}


@pytest.mark.parametrize("raw", ["0901234567", "+84 90 123 4567", "84901234567"])
def test_phone_variants_share_one_key(raw):
    assert normalize_phone(raw) == "+84901234567"


@pytest.mark.parametrize("raw", ["123", "not-a-phone", "+84901234567 ext 99"])
def test_invalid_numbers_cannot_be_customer_keys(raw):
    with pytest.raises(HTTPException):
        normalize_phone(raw)


def test_telegram_phone_identity_resumes_web_and_updates_shared_memory(client):
    customer = register(client)
    db = client.app.state.harness.db
    assert db.one("SELECT * FROM customer_profiles")["phone_e164"] == "+84901234567"
    assert db.one("SELECT * FROM customer_profiles")["id"] == customer
    assert not db.one("SELECT * FROM sessions")["verified"]
    proof = challenge(client, "+84 90 123 4567")
    response = client.post("/auth/phone/confirm", json=proof)
    assert response.status_code == 200
    session = response.json()
    assert session["identity_status"] == "identified"
    assert session["brief"]["customer_id"] == customer
    assert any(f["key"] == "income" and f["value"] == "40000000" for f in session["brief"]["needs"])
    auth = {"Authorization": "Bearer " + session["session_token"]}
    sid = session["session_id"]
    response = client.post(
        f"/sessions/{sid}/messages",
        headers=auth,
        json={"text": "Tiếp tục tư vấn thẻ tín dụng", "client_message_id": "continue"},
    )
    assert "100 triệu" in response.json()["reply"]
    changed = client.post(
        f"/sessions/{sid}/messages",
        headers=auth,
        json={"text": "Thu nhập chính xác 50 triệu", "client_message_id": "update"},
    )
    assert any(f["key"] == "income" and f["value"] == "50000000" for f in changed.json()["brief"]["needs"])
    update(client, 3, "Tiếp tục tư vấn thẻ tín dụng")
    brief = client.app.state.harness.engine.memory.brief(customer)
    assert any(f["key"] == "income" and f["value"] == "50000000" for f in brief["needs"])
    assert not brief["conflicts"]
    tg = db.one("SELECT id FROM sessions WHERE channel='telegram' AND external_id='111'")
    trace = json.loads(
        db.one("SELECT data FROM traces WHERE session_id=? ORDER BY rowid DESC LIMIT 1", (tg["id"],))["data"]
    )
    income = [f["value"] for f in trace["facts"] if f["key"] == "income" and f["source_type"] == "customer"]
    assert income == ["50000000"]
    assert client.get(f"/sessions/{sid}", headers=auth).json()["identity_status"] == "identified"
    # The public demo selector cannot be used to claim a phone-keyed customer.
    assert client.post("/sessions", json={"demo_profile": customer}).status_code == 400
    assert client.get(f"/sessions/{sid}/turns").status_code == 401


def test_only_own_shared_contact_can_bind_profile(client):
    update(client, 1, "Số điện thoại của tôi là 0901234567")
    db = client.app.state.harness.db
    assert not db.all("SELECT * FROM customer_profiles")
    update(client, 2, contact={"phone_number": "0901234567", "user_id": 222})
    assert not db.all("SELECT * FROM customer_profiles")
    update(client, 3, contact={"phone_number": "0901234567"})
    assert not db.all("SELECT * FROM customer_profiles")
    update(client, 4, contact={"phone_number": "0901234567", "user_id": 111})
    update(client, 5, contact={"phone_number": "+84901234567", "user_id": 111})
    assert len(db.all("SELECT * FROM customer_profiles")) == 1
    update(client, 6, contact={"phone_number": "+84901234567", "user_id": 222}, target=222)
    assert len(db.all("SELECT * FROM customer_profiles")) == 1
    assert db.one("SELECT * FROM identity_links WHERE external_id='222'") is None


def test_unknown_phone_cannot_load_a_profile(client):
    response = client.post("/auth/phone/start", json={"phone": "0901234567"})
    assert response.status_code == 200
    assert not client.app.state.harness.db.all("SELECT * FROM outbox")
    assert (
        client.post(
            "/auth/phone/confirm",
            json={
                "challenge_id": response.json()["challenge_id"],
                "code": "000000",
            },
        ).status_code
        == 400
    )
    assert not client.app.state.harness.db.all("SELECT * FROM customer_profiles")


def test_confirmation_codes_are_one_use_expire_and_have_attempt_limits(client):
    register(client)
    proof = challenge(client)
    wrong = {**proof, "code": "000000" if proof["code"] != "000000" else "111111"}
    for _ in range(5):
        assert client.post("/auth/phone/confirm", json=wrong).status_code == 400
    assert client.post("/auth/phone/confirm", json=proof).status_code == 400
    db = client.app.state.harness.db
    assert db.one("SELECT attempts FROM phone_challenges WHERE id=?", (proof["challenge_id"],))["attempts"] == 5
    expired = challenge(client)
    db.execute("UPDATE phone_challenges SET expires=0 WHERE id=?", (expired["challenge_id"],))
    assert client.post("/auth/phone/confirm", json=expired).status_code == 400
    valid = challenge(client)
    assert client.post("/auth/phone/confirm", json=valid).status_code == 200
    assert client.post("/auth/phone/confirm", json=valid).status_code == 400
    assert client.post("/auth/phone/start", json={"phone": "0901234567"}).status_code == 429


def test_resending_invalidates_previous_code(client):
    register(client)
    first = challenge(client)
    second = challenge(client)
    assert client.post("/auth/phone/confirm", json=first).status_code == 400
    assert client.post("/auth/phone/confirm", json=second).status_code == 200


def test_code_cannot_survive_a_change_to_the_verified_channel_link(client):
    register(client)
    proof = challenge(client)
    client.app.state.harness.db.execute("UPDATE identity_links SET external_id='222' WHERE external_id='111'")
    assert client.post("/auth/phone/confirm", json=proof).status_code == 400


def test_code_is_sent_to_phone_owner_when_bank_profile_has_multiple_chats(client):
    client.app.state.harness.db.execute("INSERT INTO identity_links VALUES('telegram','111','hoa')")
    update(client, 1, "0900000001")
    update(client, 2, contact={"phone_number": "0901234567", "user_id": 111})
    client.app.state.harness.db.execute("INSERT INTO identity_links VALUES('telegram','222','hoa')")
    proof = challenge(client)
    row = client.app.state.harness.db.one("SELECT target FROM outbox ORDER BY rowid DESC LIMIT 1")
    assert row["target"] == "111"
    assert client.post("/auth/phone/confirm", json=proof).status_code == 200


def test_phone_profile_and_context_survive_process_restart(tmp_path):
    settings = Settings(
        db_path=str(tmp_path / "persistent.sqlite3"),
        admin_token="test-admin",
        staff_password="test-staff",
        telegram_secret="test-webhook",
        telegram_token="",
    )
    with TestClient(create_app(settings)) as first:
        customer = register(first)
        proof = challenge(first)
    with TestClient(create_app(settings)) as second:
        session = second.post("/auth/phone/confirm", json=proof).json()
        assert session["identity_status"] == "identified"
        assert session["brief"]["customer_id"] == customer
        assert session["brief"]["phone"] == "+84901234567"
        assert any(f["key"] == "income" for f in session["brief"]["needs"])


def test_keyboard_is_durable_and_phone_share_removes_it(client, monkeypatch):
    sent = []

    def send(url, json, timeout):
        sent.append(json)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})

    monkeypatch.setattr(httpx, "post", send)
    client.app.state.harness.settings.telegram_token = "fake-test-token"
    update(client, 1, "/start")
    assert sent[-1]["reply_markup"]["keyboard"][0][0]["request_contact"] is True
    update(client, 2, contact={"phone_number": "0901234567", "user_id": 111})
    assert sent[-1]["reply_markup"] == {"remove_keyboard": True}
    register_row = client.app.state.harness.db.one("SELECT reply_markup FROM outbox ORDER BY rowid LIMIT 1")
    assert json.loads(register_row["reply_markup"])["keyboard"]


def test_existing_outbox_migrates_without_losing_pending_messages(tmp_path):
    path = str(tmp_path / "old.sqlite3")
    with sqlite3.connect(path) as old:
        old.execute(
            "CREATE TABLE outbox(id TEXT PRIMARY KEY,channel TEXT,target TEXT,text TEXT,status TEXT,receipt TEXT,attempts INTEGER DEFAULT 0)"
        )
        old.execute("INSERT INTO outbox(id,text,status) VALUES('existing','preserved','pending')")
    db = Database(path)
    row = db.one("SELECT * FROM outbox WHERE id='existing'")
    assert row["text"] == "preserved" and row["status"] == "pending" and row["reply_markup"] is None
    assert db.setting("schema_version") == 4


def demo_login(client, phone="0901234567"):
    response = client.post("/auth/phone/login", json={"phone": phone})
    assert response.status_code == 200, response.text
    session = response.json()
    assert session["identity_status"] == "identified"
    assert session["telegram_url"].startswith("https://t.me/")
    return session


def web_say(client, session, text, message_id):
    response = client.post(
        f"/sessions/{session['session_id']}/messages",
        headers={"Authorization": "Bearer " + session["session_token"]},
        json={"text": text, "client_message_id": message_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_telegram_first_phone_only_web_login_shares_memory(client):
    update(client, 1, "/start")
    db = client.app.state.harness.db
    welcome = db.one("SELECT text,reply_markup FROM outbox ORDER BY rowid DESC LIMIT 1")
    assert "Chia sẻ số điện thoại" in welcome["text"]
    assert json.loads(welcome["reply_markup"])["keyboard"][0][0]["request_contact"]
    update(client, 2, "Thu nhập 999 triệu")
    assert not db.all("SELECT * FROM turns")
    update(client, 3, contact={"phone_number": "0901234567", "user_id": 111})
    update(client, 4, "Tôi cần thẻ tín dụng, thu nhập 47 triệu, không có nợ")
    session = demo_login(client, "+84 90 123 4567")
    assert any(f["key"] == "income" and f["value"] == "47000000" for f in session["brief"]["needs"])
    assert not db.all("SELECT * FROM phone_challenges")
    web_say(client, session, "Thu nhập chính xác 53 triệu", "change")
    update(client, 5, "Tiếp tục tư vấn thẻ tín dụng")
    row = db.one("SELECT data FROM traces WHERE session_id IN "
                 "(SELECT id FROM sessions WHERE channel='telegram') ORDER BY rowid DESC LIMIT 1")
    assert any(f["key"] == "income" and f["value"] == "53000000" for f in json.loads(row["data"])["facts"])


def test_web_first_contact_attaches_to_existing_phone_memory(client):
    session = demo_login(client)
    customer = session["brief"]["customer_id"]
    web_say(client, session, "Tôi cần thẻ tín dụng, thu nhập 47 triệu, không có nợ", "initial")
    update(client, 1, "/start")
    update(client, 2, contact={"phone_number": "+84901234567", "user_id": 111})
    update(client, 3, "Tiếp tục tư vấn thẻ tín dụng")
    db = client.app.state.harness.db
    active = db.one("SELECT * FROM sessions WHERE channel='telegram' AND status='active'")
    assert active["customer_id"] == customer
    assert "100 triệu" in db.one("SELECT text FROM outbox ORDER BY rowid DESC LIMIT 1")["text"]
    update(client, 4, "Thu nhập chính xác 53 triệu")
    update(client, 5, "/start")
    update(client, 6, "Tiếp tục tư vấn thẻ tín dụng")
    again = demo_login(client)
    assert again["brief"]["customer_id"] == customer
    assert any(f["key"] == "income" and f["value"] == "53000000" for f in again["brief"]["needs"])
    assert len(db.all("SELECT * FROM customer_profiles")) == 1
    assert not db.all("SELECT * FROM phone_challenges")


def test_demo_phone_login_rejects_invalid_and_keeps_different_phones_separate(client):
    assert client.post("/auth/phone/login", json={"phone": "invalid"}).status_code == 422
    one = demo_login(client)
    web_say(client, one, "Thu nhập 47 triệu", "income")
    two = demo_login(client, "0901234568")
    assert one["brief"]["customer_id"] != two["brief"]["customer_id"]
    assert not two["brief"]["needs"]


def test_phone_only_web_profile_survives_restart(tmp_path):
    settings = Settings(db_path=str(tmp_path / "demo-phone.sqlite"), llm_backend="rules")
    with TestClient(create_app(settings)) as first:
        session = demo_login(first)
        web_say(first, session, "Thu nhập 47 triệu", "income")
    with TestClient(create_app(settings)) as second:
        again = demo_login(second)
        assert again["brief"]["customer_id"] == session["brief"]["customer_id"]
        assert any(f["key"] == "income" and f["value"] == "47000000" for f in again["brief"]["needs"])
