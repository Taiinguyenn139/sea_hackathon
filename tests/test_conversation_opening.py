"""Bot-first entry, durable greetings, and authorized cross-channel recall."""

import pytest
from fastapi.testclient import TestClient

from harness.api import create_app
from harness.config import Settings
from harness.service import CONVERSATION_OPENING


@pytest.fixture(params=[False, True], ids=["phone-contact", "demo-phone"])
def client(tmp_path, request):
    settings = Settings(db_path=str(tmp_path / "opening.sqlite3"), demo_phone_identity=request.param,
                        telegram_secret="opening-test", telegram_token="", llm_backend="rules")
    with TestClient(create_app(settings)) as client:
        yield client


def login(client, phone="0901234567"):
    response = client.post("/auth/phone/login", json={"phone": phone})
    assert response.status_code == 200, response.text
    return response.json()


def auth(session):
    return {"Authorization": "Bearer " + session["session_token"]}


def opening(client, session):
    response = client.get(f"/sessions/{session['session_id']}/turns", headers=auth(session))
    assert response.status_code == 200
    rows = response.json()["items"]
    assert len(rows) == 1 and rows[0]["text"] == "" and rows[0]["responder"] == "assistant"
    return rows[0]


def say(client, session, text, key):
    response = client.post(f"/sessions/{session['session_id']}/messages", headers=auth(session),
                           json={"text": text, "client_message_id": key})
    assert response.status_code == 200, response.text


def telegram(client, update_id, text=None, contact=None):
    message = {"date": 1000 + update_id, "chat": {"id": 111, "type": "private"},
               "from": {"id": 111, "first_name": "Khách kiểm thử"}}
    message.update({"contact": contact} if contact else {"text": text})
    response = client.post("/webhooks/telegram", headers={"X-Telegram-Bot-Api-Secret-Token": "opening-test"},
                           json={"update_id": update_id, "message": message})
    assert response.status_code == 200
    db = client.app.state.harness.db
    assert db.one("SELECT error FROM provider_events WHERE id=?", (f"telegram:{update_id}",))["error"] is None
    return db.one("SELECT * FROM outbox ORDER BY rowid DESC LIMIT 1")["text"]


def test_fresh_session_greets_once_without_customer_memory(client):
    session = login(client)
    first = opening(client, session)
    assert first["reply"] == CONVERSATION_OPENING
    harness = client.app.state.harness
    harness.welcome(session)
    assert opening(client, session) == first
    for table in ("memory_jobs", "facts", "tool_calls", "callbacks", "handoffs"):
        assert not harness.db.all("SELECT * FROM " + table)
    other = login(client, "0912345678")
    assert client.get(f"/sessions/{session['session_id']}/turns", headers=auth(other)).status_code == 403


def test_returning_customer_gets_latest_needs_on_both_channels(client):
    first = login(client)
    say(client, first, "Tôi vay mua nhà 2 tỷ trong 20 năm, thu nhập 45 triệu, không có nợ", "needs")
    say(client, first, "Thu nhập chính xác 50 triệu", "correction")
    harness = client.app.state.harness
    before = len(harness.db.all("SELECT * FROM memory_jobs"))
    returned = login(client)
    greeting = opening(client, returned)["reply"]
    for expected in ("quay lại", "vay mua nhà", "2 tỷ đồng", "240 tháng", "50 triệu đồng", "cập nhật"):
        assert expected in greeting
    assert "45 triệu" not in greeting and "lãi suất" not in greeting
    assert len(harness.db.all("SELECT * FROM memory_jobs")) == before
    assert "50 triệu" not in telegram(client, 1, "/start")  # No phone identity yet.
    shared = telegram(client, 2, contact={"phone_number": "0901234567", "user_id": 111})
    assert greeting in shared
    telegram(client, 3, "Thu nhập chính xác 55 triệu")
    restarted = telegram(client, 4, "/start")
    assert "55 triệu" in restarted and "50 triệu" not in restarted
    assert "chia sẻ số điện thoại" not in restarted.lower()
    count = len(harness.db.all("SELECT * FROM outbox"))
    turns = harness.db.all("SELECT * FROM turns")
    assert telegram(client, 4, "/start") == restarted
    assert len(harness.db.all("SELECT * FROM outbox")) == count
    assert harness.db.all("SELECT * FROM turns") == turns
    assert "55 triệu" in opening(client, login(client))["reply"]
    assert opening(client, login(client, "0912345678"))["reply"] == CONVERSATION_OPENING


def test_unverified_profile_and_no_memory_do_not_disclose_history(client):
    original = client.post("/sessions", json={"demo_profile": "hoa"}).json()
    client.post(f"/sessions/{original['session_id']}/verify", headers=auth(original), json={"phone": "0900000001"})
    say(client, original, "Tôi vay mua nhà 2 tỷ, thu nhập 45 triệu", "needs")
    unverified = client.post("/sessions", json={"demo_profile": "hoa"}).json()
    assert opening(client, unverified)["reply"] == CONVERSATION_OPENING
    h = client.app.state.harness
    no_memory = h._create_session("hoa", mode="no_memory", phone_authenticated=True)
    h.welcome(no_memory)
    assert opening(client, no_memory)["reply"] == CONVERSATION_OPENING


def test_opening_ignores_stale_and_conflicted_facts(client):
    first = login(client)
    say(client, first, "Tôi vay mua nhà 2 tỷ trong 20 năm, thu nhập 45 triệu", "needs")
    h = client.app.state.harness
    customer = h.session(first["session_id"])["customer_id"]
    h.db.execute("UPDATE facts SET status='conflicted' WHERE customer_id=? AND key='amount'", (customer,))
    h.db.execute("UPDATE facts SET valid_until='2000-01-01' WHERE customer_id=? AND key='income'", (customer,))
    greeting = opening(client, login(client))["reply"]
    assert "vay mua nhà" in greeting and "xác nhận lại" in greeting
    assert "2 tỷ" not in greeting and "45 triệu" not in greeting


def test_demo_history_is_labelled_and_not_written_as_customer_memory(client):
    if not client.app.state.harness.settings.demo_phone_identity:
        return
    session = login(client, "0900000002")
    assert "dữ liệu lịch sử demo" in opening(client, session)["reply"]
    h = client.app.state.harness
    assert not h.db.all("SELECT * FROM facts")
    assert not h.db.all("SELECT * FROM memory_jobs")
