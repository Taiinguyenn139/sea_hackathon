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
    streamed = client.post(
        f"/sessions/{second['session_id']}/messages/stream", headers=auth(second),
        json={"text": "Thu nhập chính xác 50 triệu", "client_message_id": "correct"},
    )
    assert streamed.status_code == 200
    assert "event: result" in streamed.text
    assert "event: error" not in streamed.text
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


@pytest.mark.parametrize("web_first", [False, True])
@pytest.mark.parametrize("phone", ["0901234567", "0900000002"])
def test_contact_and_both_web_entry_points_share_one_memory(client, web_first, phone):
    def enter(path):
        response = client.post(path, json={"phone": phone})
        assert response.status_code == 200, response.text
        return response.json()

    if web_first:
        first = enter("/demo/phone/session")
        say(client, first, "Thu nhập chính xác 40 triệu", "initial")
    telegram(client, 1, "/start")
    db = client.app.state.harness.db
    welcome = db.one("SELECT reply_markup FROM outbox ORDER BY rowid DESC LIMIT 1")
    assert json.loads(welcome["reply_markup"])["keyboard"][0][0]["request_contact"]
    response = client.post(
        "/webhooks/telegram", headers={"X-Telegram-Bot-Api-Secret-Token": "test-webhook"},
        json={"update_id": 2, "message": {
            "date": 1002, "chat": {"id": 111, "type": "private"}, "from": {"id": 111},
            "contact": {"phone_number": phone, "user_id": 111},
        }},
    )
    assert response.status_code == 200
    tg = telegram(client, 3, "Tiếp tục tư vấn thẻ tín dụng" if web_first else "Thu nhập chính xác 40 triệu")
    assert income_in_trace(client, tg["id"]) == ["40000000"]
    for path in ("/auth/phone/login", "/demo/phone/session"):
        session = enter(path)
        assert session["brief"]["customer_id"] == tg["customer_id"]
        assert session["telegram_url"].startswith("https://t.me/")
        assert any(f["key"] == "income" and f["value"] == "40000000" for f in session["brief"]["needs"])
    say(client, session, "Thu nhập chính xác 53 triệu", "correct")
    restarted = telegram(client, 4, "/start")
    telegram(client, 5, "Tiếp tục tư vấn thẻ tín dụng")
    assert restarted["customer_id"] == tg["customer_id"]
    assert income_in_trace(client, restarted["id"]) == ["53000000"]
    assert not db.all("SELECT * FROM phone_challenges")


@pytest.mark.parametrize("update_on_web", [True, False])
@pytest.mark.parametrize("text", [
    "Lương mới được tăng lên 53 triệu rồi",
    "Tôi cập nhật thu nhập thành 53 triệu/tháng",
    "Thu nhập hiện tại của tôi là 53 triệu",
    "Lương tôi bây giờ là 53 triệu",
    "Thu nhập của tôi giảm xuống 53 triệu rồi",
])
def test_natural_income_update_replaces_cross_channel_memory(client, update_on_web, text):
    session = client.post("/demo/phone/session", json={"phone": "0901234567"}).json()
    tg = telegram(client, 1, "0901234567")
    # Both transcripts contain the old figure: neither may revive it later.
    initial = "Tôi muốn vay mua nhà 2 tỷ trong 20 năm, thu nhập 47 triệu/tháng"
    if update_on_web:
        say(client, session, initial, "initial")
        telegram(client, 2, initial)
    else:
        telegram(client, 2, initial)
        say(client, session, initial, "initial")
    if update_on_web:
        response = client.post(
            f"/sessions/{session['session_id']}/messages/stream", headers=auth(session),
            json={"text": text, "client_message_id": "update"},
        )
        assert response.status_code == 200 and "event: result" in response.text
        assert "event: error" not in response.text
        telegram(client, 3, "Thu nhập và thời hạn vay mới nhất của tôi là bao nhiêu?")
        resumed_id = tg["id"]
    else:
        telegram(client, 3, text)
        say(client, session, "Thu nhập và thời hạn vay mới nhất của tôi là bao nhiêu?", "recall")
        resumed_id = session["session_id"]
    assert income_in_trace(client, resumed_id) == ["53000000"]
    db = client.app.state.harness.db
    customer = session["brief"]["customer_id"]
    assert db.all("SELECT value FROM facts WHERE customer_id=? AND key='income' AND status='active'",
                  (customer,)) == [{"value": "53000000"}]
    assert not db.all("SELECT id FROM facts WHERE customer_id=? AND status='conflicted'", (customer,))
    fresh = client.post("/demo/phone/session", json={"phone": "+84901234567"}).json()
    assert any(f["key"] == "income" and f["value"] == "53000000" for f in fresh["brief"]["needs"])


def test_conflicted_income_is_not_revived_from_either_transcript(client):
    session = client.post("/demo/phone/session", json={"phone": "0901234567"}).json()
    tg = telegram(client, 1, "0901234567")
    telegram(client, 2, "Thu nhập 47 triệu")
    say(client, session, "Thu nhập 53 triệu", "ambiguous")
    telegram(client, 3, "Thu nhập của tôi là bao nhiêu?")
    response = say(client, session, "Thu nhập của tôi là bao nhiêu?", "recall")
    assert income_in_trace(client, tg["id"]) == []
    assert income_in_trace(client, session["session_id"]) == []
    assert "chưa thống nhất" in response["reply"]
    # Confirmation still clears genuine ambiguity and is visible from the other channel.
    say(client, session, "Xác nhận thu nhập chính xác 53 triệu", "confirm")
    telegram(client, 4, "Thu nhập của tôi là bao nhiêu?")
    assert income_in_trace(client, tg["id"]) == ["53000000"]


@pytest.mark.parametrize("update,expected", [
    ("Lương mới được tăng lên 53 triệu rồi", "53000000"),
    ("Thu nhập 53 triệu", None),
])
def test_openai_draft_cannot_reuse_old_income_after_cross_channel_update(settings, monkeypatch, update, expected):
    from dataclasses import replace

    import httpx

    drafts = []
    old_income_id = None

    async def completion(self, url, **kwargs):
        nonlocal old_income_id
        messages = kwargs["json"]["messages"]
        instruction = messages[0]["content"]
        payload = json.loads(messages[1]["content"])
        if "INPUT_SECURITY_CLASSIFIER" in instruction:
            value = {"category": "safe"}
        elif "Phân loại hội thoại" in instruction:
            value = {"stage": "discover", "objection": None, "intent": "advice"}
        elif "Trích xuất memory" in instruction:
            text = payload["text"]
            value = {"facts": []}
            if text == "Thu nhập 47 triệu" or text == update:
                value = {"facts": [{"key": "income", "value": "47000000" if text == "Thu nhập 47 triệu" else "53000000",
                                    "unit": "đồng", "operation": "conflict" if text == update and expected is None else "replace",
                                    "source_text": text}]}
        else:
            drafts.append(payload)
            income = next((f for f in payload["facts"].values() if f["key"] == "income"), None)
            if income and income["value"] == "47000000":
                old_income_id = income["id"]
            # In the conflict case simulate a model trying to cite the old
            # transcript; the output guard must reject that fact identifier.
            fid = income["id"] if income else old_income_id
            draft = "Thu nhập hiện tại là {fact:" + fid + "}." if fid else payload["rules_draft"]
            value = {"draft": draft}
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(value)}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 10},
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", completion)
    settings = replace(settings, llm_backend="openai", openai_key="test-only-key",
                       openai_model="test-primary", openai_backup_model="test-backup")
    with TestClient(create_app(settings)) as client:
        tg = telegram(client, 1, "0901234567")
        telegram(client, 2, "Thu nhập 47 triệu")
        session = client.post("/demo/phone/session", json={"phone": "0901234567"}).json()
        say(client, session, update, "update")
        telegram(client, 3, "Thu nhập của tôi là bao nhiêu?")
        assert income_in_trace(client, tg["id"]) == ([expected] if expected else [])
        assert drafts[-1]["known"].get("income") == expected
        db = client.app.state.harness.db
        reply = db.one("SELECT reply FROM turns WHERE session_id=? ORDER BY rowid DESC LIMIT 1", (tg["id"],))["reply"]
        assert "47 triệu" not in reply
        assert ("53 triệu" if expected else "chưa thống nhất") in reply
