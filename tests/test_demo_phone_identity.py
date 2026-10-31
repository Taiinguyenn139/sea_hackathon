"""Exercise the deployed demo's typed-phone journey across both real adapters."""

import json

import pytest
from fastapi.testclient import TestClient

from harness.api import create_app
from harness.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        db_path=str(tmp_path / "demo-phone.sqlite3"),
        admin_token="test-admin",
        staff_password="test-staff",
        telegram_token="",
        telegram_secret="test-webhook",
        demo_phone_identity=True,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as app:
        yield app


def web(client, profile="hoa"):
    response = client.post("/sessions", json={"demo_profile": profile})
    assert response.status_code == 200
    return response.json()


def auth(session):
    return {"Authorization": "Bearer " + session["session_token"]}


def say(client, session, text, key):
    response = client.post(
        f"/sessions/{session['session_id']}/messages",
        headers=auth(session), json={"text": text, "client_message_id": key},
    )
    assert response.status_code == 200, response.text
    return response.json()


def telegram(client, number, text, target=111):
    response = client.post(
        "/webhooks/telegram", headers={"X-Telegram-Bot-Api-Secret-Token": "test-webhook"},
        json={"update_id": number, "message": {
            "date": 1000 + number, "chat": {"id": target, "type": "private"},
            "from": {"id": target, "is_bot": False, "first_name": "Khách demo"}, "text": text,
        }},
    )
    assert response.status_code == 200
    db = client.app.state.harness.db
    event = db.one("SELECT status,error FROM provider_events WHERE id=?", (f"telegram:{number}",))
    assert event == {"status": "processed", "error": None}
    return db.one(
        "SELECT * FROM sessions WHERE channel='telegram' AND external_id=? ORDER BY rowid DESC LIMIT 1",
        (str(target),),
    )


def income_in_trace(client, sid):
    row = client.app.state.harness.db.one(
        "SELECT data FROM traces WHERE session_id=? ORDER BY rowid DESC LIMIT 1", (sid,),
    )
    trace = json.loads(row["data"])
    return [f["value"] for f in trace["facts"] if f["key"] == "income" and f["source_type"] == "customer"]


@pytest.mark.parametrize("phone,formatted", [
    ("0901234567", "+84 90 123 4567"),
    ("0900000002", "+84 90 000 0002"),
])
def test_web_to_telegram_latest_memory_and_separate_transcripts(client, phone, formatted):
    session = web(client)  # Selecting Hoa does not constrain the typed number.
    receipt = say(client, session, "Số điện thoại của tôi là " + phone, "phone")
    customer = receipt["brief"]["customer_id"]
    assert receipt["identity_status"] in ("verified", "identified")
    assert say(client, session, "Số điện thoại của tôi là " + phone, "phone") == receipt
    say(client, session, "Tôi cần thẻ tín dụng, thu nhập 40 triệu, không có nợ", "need")
    tg = telegram(client, 1, formatted)
    assert tg["customer_id"] == customer
    telegram(client, 2, "Tiếp tục tư vấn thẻ tín dụng")
    assert income_in_trace(client, tg["id"]) == ["40000000"]
    say(client, session, "Thu nhập chính xác 50 triệu", "correct")
    telegram(client, 3, "Tiếp tục tư vấn thẻ tín dụng")
    assert income_in_trace(client, tg["id"]) == ["50000000"]
    db = client.app.state.harness.db
    tg_turns = db.all("SELECT text FROM turns WHERE session_id=?", (tg["id"],))
    assert all("Thu nhập chính xác" not in turn["text"] for turn in tg_turns)
    assert not db.all("SELECT * FROM phone_challenges")
    assert not db.all("SELECT * FROM customer_profiles")  # No contact or OTP required.


def test_telegram_to_web_preserves_guest_memory_and_new_conversations(client):
    guest = telegram(client, 1, "Tôi cần thẻ tín dụng, thu nhập 40 triệu, không có nợ")
    tg = telegram(client, 2, "0901234567")
    assert tg["customer_id"] == guest["customer_id"]
    session = web(client)
    result = say(client, session, "+84 90 123 4567", "phone")
    assert result["brief"]["customer_id"] == tg["customer_id"]
    say(client, session, "Tiếp tục tư vấn thẻ tín dụng", "continue")
    assert income_in_trace(client, session["session_id"]) == ["40000000"]
    new = client.post(f"/sessions/{session['session_id']}/new", headers=auth(session))
    assert new.status_code == 200
    sibling = new.json()
    assert sibling["brief"]["customer_id"] == tg["customer_id"]
    assert client.get(f"/sessions/{sibling['session_id']}/turns", headers=auth(sibling)).json()["items"] == []
    say(client, sibling, "Thu nhập chính xác 50 triệu", "new-income")
    restarted = telegram(client, 3, "/start")
    assert restarted["id"] != tg["id"]
    assert restarted["customer_id"] == tg["customer_id"]
    telegram(client, 4, "Tiếp tục tư vấn thẻ tín dụng")
    assert income_in_trace(client, restarted["id"]) == ["50000000"]


def test_different_numbers_do_not_share_memory(client):
    first = web(client)
    one = say(client, first, "0901234567", "phone")
    say(client, first, "Tôi cần thẻ tín dụng, thu nhập 40 triệu, không có nợ", "need")
    second = web(client)
    two = say(client, second, "0912345678", "phone")
    assert one["brief"]["customer_id"] != two["brief"]["customer_id"]
    assert two["brief"]["needs"] == []
    tg = telegram(client, 1, "0912345678")
    telegram(client, 2, "Tiếp tục tư vấn thẻ tín dụng")
    assert income_in_trace(client, tg["id"]) == []


def test_switching_number_requires_new_conversation_and_preserves_old_memory(client):
    session = web(client)
    original = say(client, session, "0901234567", "phone")
    say(client, session, "Tôi cần thẻ tín dụng, thu nhập 40 triệu", "need")
    rejected = client.post(
        f"/sessions/{session['session_id']}/messages", headers=auth(session),
        json={"text": "0912345678", "client_message_id": "other"},
    )
    assert rejected.status_code == 409
    new = client.post(f"/sessions/{session['session_id']}/new", headers=auth(session)).json()
    changed = say(client, new, "0912345678", "phone")
    assert changed["brief"]["customer_id"] != original["brief"]["customer_id"]
    assert changed["brief"]["needs"] == []
    metadata = client.get(f"/sessions/{session['session_id']}", headers=auth(session)).json()
    assert metadata["customer_id"] == original["brief"]["customer_id"]


def test_phone_links_and_memory_survive_restart(settings):
    with TestClient(create_app(settings)) as first:
        session = web(first)
        customer = say(first, session, "0901234567", "phone")["brief"]["customer_id"]
        say(first, session, "Tôi cần thẻ tín dụng, thu nhập 40 triệu, không có nợ", "need")
        telegram(first, 1, "0901234567")
    with TestClient(create_app(settings)) as second:
        tg = telegram(second, 2, "Tiếp tục tư vấn thẻ tín dụng")
        assert tg["customer_id"] == customer
        assert income_in_trace(second, tg["id"]) == ["40000000"]
        fresh = web(second)
        result = say(second, fresh, "+84901234567", "phone")
        assert result["brief"]["customer_id"] == customer
        assert any(f["key"] == "income" and f["value"] == "40000000" for f in result["brief"]["needs"])


def test_demo_can_resume_an_existing_contact_linked_profile(client):
    guest = telegram(client, 1, "Tôi cần thẻ tín dụng, thu nhập 40 triệu")
    client.app.state.harness.phone_identity.bind(
        guest["customer_id"], "Khách", "111", {"user_id": 111, "phone_number": "0901234567"}, 111,
    )
    session = web(client)
    result = say(client, session, "0901234567", "phone")
    assert result["brief"]["customer_id"] == guest["customer_id"]


def test_demo_mode_is_explicit_and_multiple_phone_claims_do_not_bind(client):
    assert client.get("/demo/profiles").json()["demo_phone_identity"] is True
    session = web(client)
    result = say(client, session, "0901234567 và 0912345678", "ambiguous")
    assert result["identity_status"] == "unverified"
    assert not client.app.state.harness.db.all("SELECT * FROM demo_phone_links")
    client.app.state.harness.settings.demo_phone_identity = False
    assert client.get("/demo/profiles").json()["demo_phone_identity"] is False
    result = say(client, session, "0901234567", "disabled")
    assert result["identity_status"] == "unverified"
    assert not client.app.state.harness.db.all("SELECT * FROM demo_phone_links")


def test_phone_entry_box_opens_empty_session_and_shared_memory(client):
    response = client.post("/demo/phone/session", json={"phone": "0901234567"})
    assert response.status_code == 200
    first = response.json()
    assert first["identity_status"] == "identified"
    assert client.get(f"/sessions/{first['session_id']}/turns", headers=auth(first)).json()["items"] == []
    say(client, first, "Tôi cần thẻ tín dụng, thu nhập 40 triệu, không có nợ", "need")
    second = client.post("/demo/phone/session", json={"phone": "+84 90 123 4567"}).json()
    assert second["session_id"] != first["session_id"]
    assert second["brief"]["customer_id"] == first["brief"]["customer_id"]
    assert client.get(f"/sessions/{second['session_id']}/turns", headers=auth(second)).json()["items"] == []
    say(client, second, "Thu nhập chính xác 50 triệu", "correct")
    say(client, first, "Tiếp tục tư vấn thẻ tín dụng", "resume")
    assert income_in_trace(client, first["session_id"]) == ["50000000"]
    tg = telegram(client, 1, "0901234567")
    telegram(client, 2, "Tiếp tục tư vấn thẻ tín dụng")
    assert income_in_trace(client, tg["id"]) == ["50000000"]


def test_phone_entry_rejects_invalid_number_and_disabled_mode(client):
    assert client.post("/demo/phone/session", json={"phone": "123456"}).status_code == 422
    assert not client.app.state.harness.db.all("SELECT * FROM sessions")
    client.app.state.harness.settings.demo_phone_identity = False
    assert client.post("/demo/phone/session", json={"phone": "0901234567"}).status_code == 404


def test_existing_telegram_guest_can_select_web_profile_without_merging_old_facts(client):
    guest = telegram(client, 1, "Tôi cần thẻ tín dụng, thu nhập 40 triệu, không có nợ")
    session = client.post("/demo/phone/session", json={"phone": "0901234567"}).json()
    say(client, session, "Tôi cần thẻ tín dụng, thu nhập 50 triệu, không có nợ", "need")
    tg = telegram(client, 2, "0901234567")
    assert tg["customer_id"] != guest["customer_id"]
    assert tg["customer_id"] == session["brief"]["customer_id"]
    telegram(client, 3, "Tiếp tục tư vấn thẻ tín dụng")
    assert income_in_trace(client, tg["id"]) == ["50000000"]
