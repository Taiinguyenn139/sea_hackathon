import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from harness.agent.engine import format_fact
from harness.api import create_app
from harness.config import Settings

ADMIN = {"Authorization": "Bearer admin-test"}


@pytest.fixture
def client(tmp_path):
    app = create_app(
        Settings(db_path=str(tmp_path / "test.sqlite3"), admin_token="admin-test", staff_password="staff-test", llm_backend="rules")
    )
    with TestClient(app) as c:
        yield c


def session(c, profile="hoa", mode="harness", channel=None):
    route = "/admin/sessions" if channel else "/sessions"
    body = {"demo_profile": profile, "mode": mode}
    if channel:
        body["channel"] = channel
    r = c.post(route, headers=ADMIN if channel else {}, json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    return d["session_id"], {"Authorization": "Bearer " + d["session_token"]}


def verify(c, sid, auth, profile="hoa"):
    r = c.post(
        f"/sessions/{sid}/verify", headers=auth, json={"phone": "0900000001" if profile == "hoa" else "0900000002"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["identity_status"] == "verified"


def send(c, sid, auth, text, id="one"):
    r = c.post(f"/sessions/{sid}/messages", headers=auth, json={"text": text, "client_message_id": id})
    assert r.status_code == 200, r.text
    return r.json()


def test_capabilities_and_g7(client):
    sid, a = session(client)
    _, b = session(client, "minh")
    assert client.get(f"/sessions/{sid}/turns", headers=b).status_code == 403
    assert client.post("/sessions", json={"external_id": "victim", "channel": "telegram"}).status_code == 422
    r = send(client, sid, a, "Tôi vay 2 tỷ")
    assert r["guard_events"][0]["guard_id"] == "G7" and "2" not in r["reply"]
    assert client.get("/customers/hoa/brief", headers=a).status_code == 401
    assert client.get(f"/traces/{sid}", headers=a).status_code == 401


def test_verify_limit(client):
    sid, a = session(client)
    for _ in range(5):
        assert (
            client.post(f"/sessions/{sid}/verify", headers=a, json={"phone": "0900000002"}).json()["identity_status"]
            == "unverified"
        )
    sid, b = session(client)
    assert client.post(f"/sessions/{sid}/verify", headers=b, json={"phone": "0900000001"}).status_code == 429


@pytest.mark.parametrize("verified", [False, True])
def test_new_conversation_keeps_identity_but_isolates_transcript_and_capability(client, verified):
    sid, auth = session(client)
    _, other = session(client, "minh")
    if verified:
        verify(client, sid, auth)
    send(client, sid, auth, "Tôi muốn tìm hiểu vay mua nhà.")
    assert client.post(f"/sessions/{sid}/new").status_code == 403
    assert client.post(f"/sessions/{sid}/new", headers=other).status_code == 403
    result = client.post(f"/sessions/{sid}/new", headers=auth)
    assert result.status_code == 200, result.text
    new = result.json()
    new_auth = {"Authorization": "Bearer " + new["session_token"]}
    assert new["session_id"] != sid
    assert new["identity_status"] == ("verified" if verified else "unverified")
    assert client.get(f"/sessions/{new['session_id']}/turns", headers=new_auth).json()["items"] == []
    assert len(client.get(f"/sessions/{sid}/turns", headers=auth).json()["items"]) == 1
    assert client.get(f"/sessions/{sid}", headers=auth).json()["status"] == "active"
    assert client.get(f"/sessions/{new['session_id']}/turns", headers=auth).status_code == 403
    assert client.get(f"/sessions/{sid}/turns", headers=new_auth).status_code == 403


def test_new_conversation_does_not_end_waiting_handoff(client):
    sid, auth = session(client)
    verify(client, sid, auth)
    handoff = send(client, sid, auth, "Tôi muốn gặp chuyên viên")
    assert handoff["handoff_id"]
    new = client.post(f"/sessions/{sid}/new", headers=auth).json()
    assert new["session_id"] != sid
    previous = client.get(f"/sessions/{sid}", headers=auth).json()
    assert previous["status"] == "waiting_human"
    assert previous["handoff_id"] == handoff["handoff_id"]


def test_new_conversation_preserves_phone_linked_customer(client):
    harness = client.app.state.harness
    guest = harness.telegram_customer("chat-history-guest", "Khách kiểm thử")
    original = harness._create_session(guest, phone_authenticated=True)
    auth = {"Authorization": "Bearer " + original["session_token"]}
    response = client.post(f"/sessions/{original['session_id']}/new", headers=auth)
    assert response.status_code == 200
    new = harness.session(response.json()["session_id"])
    assert new["customer_id"] == guest
    assert harness.can_chat(new)


def test_staff_cookie_csrf(client):
    assert client.post("/staff/login", json={"password": "bad"}).status_code == 401
    r = client.post("/staff/login", json={"password": "staff-test"})
    assert r.status_code == 200
    assert "HttpOnly" in r.headers["set-cookie"]
    assert client.get("/staff/me").json()["csrf_token"] == r.json()["csrf_token"]
    assert client.post("/staff/logout").status_code == 403
    assert client.post("/staff/logout", headers={"X-CSRF-Token": r.json()["csrf_token"]}).status_code == 200
    assert client.get("/staff/me").status_code == 401


@pytest.mark.parametrize(
    "value,expected",
    [
        ("20000000", "20 triệu đồng"),
        ("100000000", "100 triệu đồng"),
        ("10000000000", "10 tỷ đồng"),
        ("20000000000", "20 tỷ đồng"),
        ("12345000", "12.345.000 đồng"),
    ],
)
def test_format(value, expected):
    assert format_fact({"value": value, "unit": "đồng"}) == expected


@pytest.mark.parametrize("mode", ["harness", "no_memory", "full_history"])
def test_three_modes_common_renderer(client, mode):
    sid, a = session(client, mode=mode)
    verify(client, sid, a)
    send(client, sid, a, "Vay mua nhà 2 tỷ trong 20 năm", "a")
    r = send(client, sid, a, "Thu nhập 45 triệu mỗi tháng, không có nợ, giá nhà 3 tỷ", "b")
    assert "2 tỷ" in r["reply"] and "7,1" in r["reply"]
    trace = client.get(f"/traces/{sid}", headers=ADMIN).json()["items"][-1]
    for span in trace["source_spans"]:
        assert r["reply"][span["start"] : span["end"]]
        assert any(f["id"] == span["fact_id"] for f in trace["facts"])


def test_memory_without_old_transcript(client):
    sid, a = session(client)
    verify(client, sid, a)
    send(client, sid, a, "Vay mua nhà 2 tỷ trong 20 năm thu nhập 45 triệu, không có nợ, giá nhà 3 tỷ. SECRET_CANARY")
    client.post(f"/sessions/{sid}/end", headers=a)
    new, b = session(client)
    verify(client, new, b)
    assert "7,1" in send(client, new, b, "Tiếp tục tư vấn")["reply"]
    assert "SECRET_CANARY" not in json.dumps(client.get(f"/traces/{new}", headers=ADMIN).json(), ensure_ascii=False)


def test_conflict_confirmation(client):
    sid, a = session(client)
    verify(client, sid, a)
    send(client, sid, a, "Thu nhập 45 triệu")
    sid, b = session(client, channel="telegram")
    verify(client, sid, b)
    send(client, sid, b, "Thu nhập 40 triệu")
    sid, c = session(client)
    verify(client, sid, c)
    assert "chưa thống nhất" in send(client, sid, c, "Tôi muốn tiếp tục")["reply"]
    send(client, sid, c, "Xác nhận thu nhập chính xác 40 triệu", "confirm")
    assert not client.get("/customers/hoa/brief", headers=ADMIN).json()["conflicts"]


def test_duplicate_no_memory_gap(client):
    sid, a = session(client)
    verify(client, sid, a)
    first = send(client, sid, a, "Thu nhập 45 triệu")
    assert send(client, sid, a, "Thu nhập 45 triệu") == first
    seq = client.app.state.harness.db.one("SELECT * FROM customer_sequences WHERE customer_id=?", ("hoa",))
    assert seq["next_seq"] == seq["processed_seq"] == 1
    assert (
        client.post(
            f"/sessions/{sid}/messages", headers=a, json={"text": "different", "client_message_id": "one"}
        ).status_code
        == 409
    )


def test_fault_does_not_invent_callback(client):
    sid, a = session(client)
    verify(client, sid, a)
    client.post("/admin/faults", headers=ADMIN, json={"tool": "get_rate", "mode": "timeout", "count": 1})
    r = send(client, sid, a, "Vay mua nhà 2 tỷ trong 20 năm thu nhập 45 triệu, không có nợ, giá nhà 3 tỷ")
    assert not any(x.isdigit() for x in r["reply"])
    assert any(x["guard_id"] == "G2" for x in r["guard_events"])
    assert len(client.get("/callbacks", headers=ADMIN).json()["items"]) == 0


def test_handoff_current_turn_concurrent_complete(client):
    sid, a = session(client)
    verify(client, sid, a)
    r = send(client, sid, a, "Tôi muốn gặp chuyên viên vay mua nhà 2 tỷ trong 20 năm thu nhập 45 triệu")
    h = client.get("/handoffs/" + r["handoff_id"], headers=ADMIN).json()
    assert any(f["key"] == "income" for f in h["brief"]["facts"])
    body = {"notes": "Đã tư vấn", "commitments": ["Gọi lại ngày mai"], "idempotency_key": "c1"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: client.post("/handoffs/" + r["handoff_id"] + "/complete", headers=ADMIN, json=body), range(2)
            )
        )
    assert all(r.status_code == 200 for r in responses)
    assert responses[0].json() == responses[1].json()
    assert len(client.get("/callbacks", headers=ADMIN).json()["items"]) == 1
    assert client.get("/sessions/" + sid, headers=a).json()["status"] == "active"


def test_expired_promo(client):
    client.post("/admin/clock", headers=ADMIN, json={"as_of": "2026-09-27"})
    sid, a = session(client)
    verify(client, sid, a)
    assert "6,5" in send(client, sid, a, "Vay mua nhà 2 tỷ trong 20 năm thu nhập 45 triệu, không có nợ, giá nhà 3 tỷ")["reply"]
    client.post("/admin/clock", headers=ADMIN, json={"as_of": "2026-10-31"})
    sid, b = session(client)
    verify(client, sid, b)
    r = send(client, sid, b, "Tiếp tục tư vấn")
    assert "7,1" in r["reply"] and "hết hạn" in r["reply"]


def test_card_and_unsuitable(client):
    sid, a = session(client)
    verify(client, sid, a)
    assert "100 triệu" in send(client, sid, a, "Tôi cần thẻ tín dụng thu nhập 40 triệu, không có nợ")["reply"]
    sid, b = session(client, "minh")
    verify(client, sid, b, "minh")
    r = send(client, sid, b, "Vay tiêu dùng 100 triệu trong 24 tháng thu nhập 2 triệu, không có nợ")
    assert any(e["guard_id"] == "G4" for e in r["guard_events"])


def test_telegram_auth_private_dedupe(client):
    h = client.app.state.harness
    h.settings.telegram_secret = "test-webhook"
    payload = {
        "update_id": 1,
        "message": {"date": 100, "text": "hi", "chat": {"id": 111, "type": "private"}, "from": {"is_bot": False}},
    }
    assert client.post("/webhooks/telegram", json=payload).status_code == 403
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-webhook"}
    for _ in range(2):
        assert client.post("/webhooks/telegram", headers=headers, json=payload).status_code == 200
    assert len(h.db.all("SELECT * FROM provider_events")) == 1
    assert len(h.db.all("SELECT * FROM outbox")) == 1
    payload["update_id"] = 2
    payload["message"]["chat"]["type"] = "group"
    client.post("/webhooks/telegram", headers=headers, json=payload)
    assert len(h.db.all("SELECT * FROM provider_events")) == 1


@pytest.mark.parametrize("text,expected,intent", [
    ("Hello", "Chào anh/chị", "greeting"),
    ("Có những sản phẩm vay nào ???", "vay tiêu dùng", "loan_catalog"),
    ("Ngân hàng có những dịch vụ nào?", "thẻ tín dụng", "product_catalog"),
    ("Cảm ơn bạn!", "Rất vui", "acknowledgement"),
])
def test_web_public_conversation_answers_intent_before_collecting_quote_slots(client, text, expected, intent):
    sid, auth = session(client)
    result = send(client, sid, auth, text)
    assert expected in result["reply"]
    assert "số tiền muốn vay" not in result["reply"]
    h = client.app.state.harness
    assert not h.db.all("SELECT * FROM tool_calls")
    trace = json.loads(h.db.one("SELECT data FROM traces WHERE session_id=?", (sid,))["data"])
    assert trace["plan"]["intent"] == intent


def test_telegram_hello_and_loan_catalog_do_not_repeat_default_question(client):
    h = client.app.state.harness
    h.db.execute("INSERT INTO identity_links VALUES('telegram','111','hoa')")
    share_telegram_phone(client)
    _, greeting = telegram_message(client, 1, "Hello")
    _, catalog = telegram_message(client, 2, "Có những sản phẩm vay nào ???")
    assert "Chào anh/chị" in greeting
    assert "vay mua nhà" in catalog and "vay tiêu dùng" in catalog
    assert "số tiền muốn vay" not in greeting + catalog
    assert greeting != catalog
    assert not h.db.all("SELECT * FROM tool_calls")


def test_greeting_after_quote_does_not_trigger_another_quote(client):
    sid, auth = session(client)
    verify(client, sid, auth)
    send(client, sid, auth, "Vay mua nhà 2 tỷ trong 20 năm thu nhập 45 triệu, không có nợ, giá nhà 3 tỷ")
    h = client.app.state.harness
    before = len(h.db.all("SELECT * FROM tool_calls"))
    result = send(client, sid, auth, "Xin chào", "greeting")
    assert "Chào anh/chị" in result["reply"]
    assert len(h.db.all("SELECT * FROM tool_calls")) == before


def telegram_message(client, update_id, text, chat_id=111, stamp=None):
    h = client.app.state.harness
    h.settings.telegram_secret = "test-webhook"
    h.settings.telegram_token = ""
    response = client.post(
        "/webhooks/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-webhook"},
        json={
            "update_id": update_id,
            "message": {
                "date": stamp if stamp is not None else 100 + update_id,
                "text": text,
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": chat_id, "is_bot": False, "first_name": "Khách mới"},
            },
        },
    )
    assert response.status_code == 200
    event = h.db.one("SELECT * FROM provider_events WHERE id=?", (f"telegram:{update_id}",))
    assert event["status"] == "processed" and event["error"] is None
    customer = h.db.one(
        "SELECT customer_id FROM sessions WHERE channel='telegram' AND external_id=? ORDER BY rowid DESC LIMIT 1",
        (str(chat_id),),
    )["customer_id"]
    reply = h.db.one(
        "SELECT text FROM outbox WHERE target=? ORDER BY rowid DESC LIMIT 1", (str(chat_id),)
    )["text"]
    return customer, reply


def share_telegram_phone(client, chat_id=111, stamp=1):
    h = client.app.state.harness
    h.settings.telegram_secret = "test-webhook"
    # Contact first, independently of the numbered conversation turns.
    response = client.post("/webhooks/telegram", headers={"X-Telegram-Bot-Api-Secret-Token": "test-webhook"}, json={
        "update_id": 100000 + chat_id, "message": {
            "date": stamp, "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "first_name": "Khách mới"},
            "contact": {"phone_number": "0901234567" if chat_id == 111 else "0901234568", "user_id": chat_id},
        },
    })
    assert response.status_code == 200


def test_telegram_new_customer_can_start_and_update_needs(client):
    share_telegram_phone(client)
    customer, welcome = telegram_message(client, 1, "/start")
    assert "sản phẩm" in welcome and "quản trị" not in welcome
    assert customer not in {"hoa", "minh"}
    _, reply = telegram_message(client, 2, "Tôi cần thẻ tín dụng, thu nhập 40 triệu, không có nợ")
    assert "100 triệu" in reply
    telegram_message(client, 3, "Thu nhập chính xác 50 triệu")
    h = client.app.state.harness
    brief = client.get(f"/customers/{customer}/brief", headers=ADMIN).json()
    assert brief["cif"] is None and brief["name"] == "Khách mới"
    assert any(f["key"] == "income" and f["value"] == "50000000" for f in brief["needs"])
    assert not any(f["key"] == "income" and f["value"] == "40000000" for f in brief["needs"])
    s = h.db.one("SELECT * FROM sessions WHERE customer_id=?", (customer,))
    assert not s["verified"]  # Conversation access does not assert a bank identity.
    assert client.get(f"/sessions/{s['id']}", headers=ADMIN).json()["identity_status"] == "identified"
    telegram_message(client, 3, "Thu nhập chính xác 50 triệu")
    assert len(h.db.all("SELECT * FROM guest_customers")) == 1
    assert len(h.db.all("SELECT * FROM sessions WHERE status='active'")) == 1
    assert len(h.db.all("SELECT * FROM turns")) == 2


def test_telegram_first_message_is_processed_and_customers_are_isolated(client):
    share_telegram_phone(client)
    customer, reply = telegram_message(client, 1, "Tôi cần thẻ tín dụng, thu nhập 40 triệu, không có nợ")
    assert "100 triệu" in reply
    share_telegram_phone(client, 222)
    other, reply = telegram_message(client, 2, "Tôi cần thẻ tín dụng", chat_id=222)
    assert other != customer and "thu nhập" in reply and "100 triệu" not in reply
    memory = client.app.state.harness.engine.memory
    assert any(f["key"] == "income" for f in memory.brief(customer)["needs"])
    assert not any(f["key"] == "income" for f in memory.brief(other)["needs"])
    assert not memory.brief("hoa")["needs"]
    # Public web sessions cannot claim a Telegram prospect by guessing its ID.
    assert client.post("/sessions", json={"demo_profile": customer}).status_code == 400
    telegram_message(client, 3, "/start hoa", chat_id=222)
    h = client.app.state.harness
    assert h.db.one("SELECT * FROM identity_links WHERE external_id='222'")["customer_id"] == other
    assert h.db.one("SELECT customer_id FROM sessions WHERE external_id='222' ORDER BY rowid DESC LIMIT 1")[
        "customer_id"
    ] == other


def test_telegram_guest_memory_survives_new_session_and_process_restart(tmp_path):
    settings = Settings(db_path=str(tmp_path / "guest.sqlite3"), admin_token="admin-test", staff_password="staff-test")
    with TestClient(create_app(settings)) as client:
        share_telegram_phone(client)
        customer, _ = telegram_message(client, 1, "Tôi cần thẻ tín dụng, thu nhập 40 triệu, không có nợ")
        telegram_message(client, 2, "/start")
        _, reply = telegram_message(client, 3, "Tiếp tục tư vấn thẻ tín dụng")
        assert "100 triệu" in reply
    with TestClient(create_app(settings)) as client:
        again, reply = telegram_message(client, 4, "Tiếp tục tư vấn thẻ tín dụng")
        assert again == customer and "100 triệu" in reply
        h = client.app.state.harness
        assert len(h.db.all("SELECT * FROM guest_customers")) == 1
        assert len(h.db.all("SELECT * FROM sessions WHERE status='active'")) == 1


def test_telegram_legacy_bank_link_uses_private_onboarding(client):
    client.post("/admin/identity-links/telegram", headers=ADMIN,
                json={"external_id": "111", "customer_id": "hoa"})
    customer, reply = telegram_message(client, 1, "/start")
    assert customer != "hoa" and "Chia sẻ số điện thoại" in reply
    assert not client.app.state.harness.db.all("SELECT * FROM turns")
    share_telegram_phone(client, stamp=200)
    assert client.app.state.harness.db.one("SELECT * FROM identity_links WHERE external_id='111'")["customer_id"] != "hoa"


def test_telegram_guest_can_handoff_with_shared_bank_brief(client):
    share_telegram_phone(client)
    customer, _ = telegram_message(client, 1, "Tôi cần thẻ tín dụng, thu nhập 40 triệu, không có nợ")
    _, reply = telegram_message(client, 2, "Tôi muốn gặp chuyên viên")
    assert "chuyển yêu cầu" in reply
    handoff = client.get("/handoffs", headers=ADMIN).json()["items"][0]
    assert handoff["customer_id"] == customer and handoff["brief"]["cif"] is None
    assert any(f["key"] == "income" for f in handoff["brief"]["needs"])
    _, reply = telegram_message(client, 3, "/start")
    assert "Chuyên viên đang tiếp nhận" in reply


def test_telegram_guest_summary_keeps_private_action_locked(client):
    share_telegram_phone(client)
    customer, _ = telegram_message(client, 1, "Tôi cần thẻ tín dụng, thu nhập 40 triệu, không có nợ")
    _, reply = telegram_message(client, 2, "Gửi tóm tắt")
    h = client.app.state.harness
    calls = h.db.all("SELECT result FROM tool_calls WHERE tool='send_summary'")
    assert all(json.loads(call["result"])["error"] for call in calls)
    assert not h.db.all("SELECT * FROM outbox WHERE status='mock_sent'")
    assert "số điện thoại" in reply
    assert len(h.db.all("SELECT * FROM outbox WHERE target='111'")) == 3
    assert h.engine.memory.brief(customer)["quotes"]
    assert not h.engine.memory.brief("hoa")["needs"]


def test_telegram_late_start_does_not_reset_active_session(client):
    customer, _ = telegram_message(client, 1, "Thu nhập 40 triệu", stamp=200)
    h = client.app.state.harness
    sid = h.db.one("SELECT id FROM sessions WHERE customer_id=?", (customer,))["id"]
    _, reply = telegram_message(client, 2, "/start", stamp=100)
    assert "đến trễ" in reply
    assert h.session(sid)["status"] == "active"
    assert len(h.db.all("SELECT * FROM sessions")) == 1


def test_admin_reset(client):
    sid, a = session(client)
    verify(client, sid, a)
    send(client, sid, a, "Vay mua nhà 2 tỷ trong 20 năm thu nhập 45 triệu, không có nợ, giá nhà 3 tỷ")
    assert client.post("/admin/reset", headers=ADMIN, json={}).status_code == 200
    assert client.get("/sessions/" + sid, headers=a).status_code == 404


def test_health_revision_and_secure_cookie(tmp_path):
    app = create_app(
        Settings(
            db_path=str(tmp_path / "production.sqlite3"),
            staff_password="staff-test",
            cookie_secure=True,
            revision="test-revision",
        )
    )
    with TestClient(app) as c:
        assert c.get("/healthz").json()["revision"] == "test-revision"
        assert "Secure" in c.post("/staff/login", json={"password": "staff-test"}).headers["set-cookie"]


def test_memory_worker_crash_rolls_back_and_replays(client, monkeypatch):
    h = client.app.state.harness
    sid, a = session(client)
    verify(client, sid, a)
    original = h.engine.memory._write

    def crash(*args):
        original(*args)
        raise RuntimeError("simulated process crash before commit")

    with h.db.transaction() as c:
        h.engine.memory.enqueue(c, "crash-turn", "hoa", sid, "web", "Thu nhập 45 triệu")
    monkeypatch.setattr(h.engine.memory, "_write", crash)
    with pytest.raises(RuntimeError):
        h.engine.memory.flush("hoa")
    assert h.db.one("SELECT processed_seq FROM customer_sequences WHERE customer_id=?", ("hoa",))["processed_seq"] == 0
    assert not h.db.all("SELECT * FROM facts WHERE source_ref='crash-turn'")
    monkeypatch.setattr(h.engine.memory, "_write", original)
    h.engine.memory.flush("hoa")
    h.engine.memory.flush("hoa")
    assert len(h.db.all("SELECT * FROM facts WHERE source_ref='crash-turn'")) == 1


def test_memory_two_workers_contiguous(client):
    h = client.app.state.harness
    sid, a = session(client)
    verify(client, sid, a)
    with h.db.transaction() as c:
        h.engine.memory.enqueue(c, "worker-a", "hoa", sid, "web", "Thu nhập 45 triệu")
        h.engine.memory.enqueue(c, "worker-b", "hoa", sid, "telegram", "Thu nhập 40 triệu")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: h.engine.memory.flush("hoa"), range(2)))
    row = h.db.one("SELECT * FROM customer_sequences WHERE customer_id=?", ("hoa",))
    assert row["processed_seq"] == row["next_seq"] == 2
    assert len(h.engine.memory.brief("hoa")["conflicts"]) == 2


def test_bank_fact_canonical_ids_and_fault_count(client):
    h = client.app.state.harness
    args = {"product": "home_loan", "amount": 2000000000, "term": 240, "segment": "mass", "as_of": "2026-10-31"}
    a = h.engine.bank.call("get_rate", args, "hoa", "test-session")
    b = h.engine.bank.call("get_rate", dict(reversed(list(args.items()))), "hoa", "test-session")
    assert a["facts"] and not a["error"]
    assert [f["fact_id"] for f in a["facts"]] == [f["fact_id"] for f in b["facts"]]
    client.post("/admin/faults", headers=ADMIN, json={"tool": "get_rate", "mode": "error", "count": 1})
    assert "as_of" in h.engine.bank.call("get_rate", {}, "hoa", "test-session")["error"]
    assert h.engine.bank.call("get_rate", args, "hoa", "test-session")["error"] == "error"
    assert h.engine.bank.call("get_rate", args, "hoa", "test-session")["error"] is None


@pytest.mark.parametrize("replacement", [
    "Lãi {fact:missing}", "Lãi suất six percent mỗi năm.", "Khoản vay chắc chắn được duyệt.",
])
def test_guard_revalidates_second_draft(client, monkeypatch, replacement):
    engine = client.app.state.harness.engine
    state = {
        "session": {"mode": "harness", "customer_id": "hoa", "verified": True},
        "draft": "Lãi {fact:missing}",
        "facts": {},
        "tool_error": False,
        "unsuitable": False,
        "disclosures": [],
        "plan": {"missing": []},
    }
    monkeypatch.setattr(engine, "rules_draft", lambda _: replacement)
    result = engine.guard(state)
    assert "{fact:" not in result["draft"]
    assert [e["attempt"] for e in result["guard_events"] if e["guard_id"] == "G1"] == [1, 2]


def test_telegram_timeout_stays_unknown(client, monkeypatch):
    import httpx

    h = client.app.state.harness
    h.settings.telegram_token = "test-token"
    dispatcher = client.app.state.dispatcher
    dispatcher.reply("intent", "123", "Hello")

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("network timeout")

    monkeypatch.setattr(httpx, "post", timeout)
    dispatcher.send_pending()
    dispatcher.send_pending()
    row = h.db.one("SELECT * FROM outbox")
    assert row["status"] == "unknown" and row["attempts"] == 1


def test_eval_manifest_allowlist(client, tmp_path):
    h = client.app.state.harness
    h.settings.results_dir = tmp_path
    (tmp_path / "index.json").write_text(json.dumps({"schema_version": 1, "runs": [{"run_id": "valid"}]}))
    (tmp_path / "valid.json").write_text(json.dumps({"run_id": "valid", "status": "complete"}))
    (tmp_path / "private.json").write_text(json.dumps({"private": True}))
    assert client.get("/eval/runs/valid", headers=ADMIN).status_code == 200
    assert client.get("/eval/runs/private", headers=ADMIN).status_code == 404
    assert client.get("/eval/runs", headers={}).status_code == 401


@pytest.mark.parametrize(
    "draft",
    ["Lãi sáu phần trăm mỗi năm", "Khoản vay hai tỷ đồng", "Lãi 6.9%", "Lãi {fact:missing}", "Lãi {fact:broken",
     "Lãi suất six percent mỗi năm.", "Interest is six point nine percent.",
     "Repayment over twenty-one months.", "Borrow two million dollars.",
     "Khoản vay của anh/chị chắc chắn được duyệt.", "Ngân hàng cam kết phê duyệt khoản vay.",
     "Your loan is guaranteed to be approved.",
     "Không thể cam kết phê duyệt. Tuy nhiên khoản vay chắc chắn được duyệt.",
     "Không thể cam kết phê duyệt nhưng khoản vay chắc chắn được duyệt.",
     "We cannot guarantee approval but your loan will definitely be approved."],
)
def test_unbacked_draft_variants(client, monkeypatch, draft):
    engine = client.app.state.harness.engine
    state = {
        "session": {"mode": "harness", "customer_id": "hoa", "verified": True},
        "draft": draft,
        "facts": {},
        "tool_error": False,
        "unsuitable": False,
        "disclosures": [],
        "plan": {"missing": []},
    }
    monkeypatch.setattr(engine, "rules_draft", lambda _: "Em cần xác nhận lại thông tin.")
    result = engine.guard(state)
    assert result["draft"] == "Em cần xác nhận lại thông tin."
    assert result["guard_events"][0]["guard_id"] == "G1"


def test_optional_llm_schema_fallback_and_extraction_source(client, monkeypatch):
    import httpx

    settings = client.app.state.harness.settings
    settings.llm_backend = "openai"
    settings.openai_key = "test-only"
    settings.openai_model = "test-model"
    settings.openai_backup_model = "test-backup"

    async def fake_post(self, url, **kwargs):
        instruction = kwargs["json"]["messages"][0]["content"]
        if "INPUT_SECURITY_CLASSIFIER" in instruction:
            result = {"category": "safe"}
        elif "Phân loại" in instruction:
            result = {"stage": "quote", "objection": None}
        elif "Trích" in instruction:
            result = {"spans": ["thu nhập 999 triệu", "Thu nhập 45 triệu"]}
        else:
            result = {"draft": "Em có thể cho vay 999 tỷ đồng."}
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(result)}}]}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    sid, a = session(client)
    verify(client, sid, a)
    r = send(client, sid, a, "Vay mua nhà 2 tỷ trong 20 năm. Thu nhập 45 triệu, không có nợ, giá nhà 3 tỷ")
    assert "999" not in r["reply"] and "7,1" in r["reply"]
    assert any(e["guard_id"] == "G1" for e in r["guard_events"])
    brief = client.get("/customers/hoa/brief", headers=ADMIN).json()
    assert not any(f["value"] == "999000000" for f in brief["facts"])

    async def malformed(self, url, **kwargs):
        if "INPUT_SECURITY_CLASSIFIER" in kwargs["json"]["messages"][0]["content"]:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": '{"category":"safe"}'}}]},
                request=httpx.Request("POST", url),
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"unexpected":true}'}}]}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", malformed)
    from harness.llm import DEFAULT_REPLY

    assert send(client, sid, a, "Tiếp tục", "next")["reply"] == DEFAULT_REPLY


def test_phone_not_persisted_in_verification_tool_arguments(client):
    sid, a = session(client)
    verify(client, sid, a)
    rows = client.app.state.harness.db.all("SELECT args FROM tool_calls WHERE tool='verify_identity'")
    assert rows and "0900000001" not in json.dumps(rows)
    assert all(json.loads(row["args"])["phone"] == "[redacted]" for row in rows)


def test_release_gate_blocks_mutation_and_delivery(tmp_path, monkeypatch):
    import httpx

    gate = tmp_path / "release-ready"
    app = create_app(
        Settings(db_path=str(tmp_path / "gated.sqlite3"), release_gate=str(gate), telegram_token="test-only")
    )
    calls = []

    def capture(*args, **kwargs):
        calls.append(kwargs)
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": 1}}, request=httpx.Request("POST", "https://example.test")
        )

    monkeypatch.setattr(httpx, "post", capture)
    with TestClient(app) as c:
        dispatcher = app.state.dispatcher
        dispatcher.reply("candidate-intent", "123", "Test")
        dispatcher.replay()
        dispatcher.send_pending()
        assert calls == []
        assert c.get("/healthz").status_code == 200
        assert c.post("/sessions", json={"demo_profile": "hoa"}).status_code == 503
        gate.touch()
        dispatcher.send_pending()
        assert len(calls) == 1
        assert c.post("/sessions", json={"demo_profile": "hoa"}).status_code == 200


def test_production_config_rejects_empty_secrets():
    with pytest.raises(ValueError):
        Settings(environment="production", admin_token="", staff_password="")


def test_handoff_bot_said_has_tool_provenance(client):
    sid, a = session(client)
    verify(client, sid, a)
    send(client, sid, a, "Vay mua nhà 2 tỷ trong 20 năm thu nhập 45 triệu, không có nợ, giá nhà 3 tỷ")
    response = client.post(f"/sessions/{sid}/handoff", headers=a, json={"reason": "Tư vấn hồ sơ"})
    assert response.status_code == 200
    assert response.json()["bot_said"][0]["tool_call_ids"]
    assert response.json()["bot_said"][0]["fact_ids"]


def test_callback_uses_business_clock(client):
    sid, a = session(client)
    verify(client, sid, a)
    send(client, sid, a, "Hẹn gọi lại sau 2 ngày lúc 09:00")
    callbacks = client.get("/callbacks", headers=ADMIN).json()["items"]
    assert callbacks[0]["due_at"].startswith("2026-11-02")


def test_candidate_preflight_recovers_memory_without_network(tmp_path, monkeypatch):
    import httpx

    gate = tmp_path / "not-promoted"
    settings = Settings(
        db_path=str(tmp_path / "candidate.sqlite3"),
        release_gate=str(gate),
        llm_backend="openai",
        openai_key="test-only",
        openai_model="test-model",
        openai_backup_model="test-backup",
    )
    app = create_app(settings)
    harness = app.state.harness
    with harness.db.transaction() as conn:
        harness.engine.memory.enqueue(
            conn, "pending-before-release", "hoa", "previous-session", "web", "Thu nhập 45 triệu"
        )

    async def forbid_network(*args, **kwargs):
        raise AssertionError("Candidate preflight must not make network requests")

    monkeypatch.setattr(httpx.AsyncClient, "post", forbid_network)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        job = harness.db.one("SELECT status FROM memory_jobs WHERE turn_id=?", ("pending-before-release",))
        assert job["status"] == "processed"
        assert (
            harness.db.one("SELECT processed_seq FROM customer_sequences WHERE customer_id=?", ("hoa",))[
                "processed_seq"
            ]
            == 1
        )
        assert not gate.exists()


def test_candidate_health_rejects_poison_memory_before_promotion(tmp_path):
    app = create_app(Settings(db_path=str(tmp_path / "poison.sqlite3"), release_gate=str(tmp_path / "not-promoted")))
    harness = app.state.harness
    with harness.db.transaction() as conn:
        harness.engine.memory.enqueue(conn, "poison", "hoa", "old-session", "web", "Thu nhập 45 triệu")
        conn.execute("UPDATE memory_jobs SET status='failed' WHERE turn_id='poison'")
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 503
        assert client.post("/sessions", json={"demo_profile": "hoa"}).status_code == 503
        assert harness.db.one("SELECT status FROM memory_jobs WHERE turn_id='poison'")["status"] == "failed"


def test_candidate_health_rejects_missing_handoff_checkpoint(tmp_path):
    from bank_tools.service import canonical, now

    app = create_app(
        Settings(db_path=str(tmp_path / "broken-handoff.sqlite3"), release_gate=str(tmp_path / "not-promoted"))
    )
    harness = app.state.harness
    created = harness.new_session("hoa")
    sid = created["session_id"]
    harness.db.execute("UPDATE sessions SET status='waiting_human' WHERE id=?", (sid,))
    harness.db.execute(
        "INSERT INTO handoffs VALUES(?,?,?,?,?,?)",
        (
            "broken",
            sid,
            "completing",
            canonical({"handoff_id": "broken"}),
            canonical({"pending_request": {"notes": "done", "commitments": [], "idempotency_key": "one"}}),
            now(),
        ),
    )
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 503
        assert harness.db.one("SELECT status FROM handoffs WHERE id='broken'")["status"] == "completing"


def test_candidate_preflight_recovers_valid_completing_handoff(tmp_path, monkeypatch):
    import httpx

    from bank_tools.service import canonical

    path = str(tmp_path / "recover-handoff.sqlite3")
    initial = create_app(Settings(db_path=path, admin_token="admin-test"))
    with TestClient(initial) as client:
        sid, auth = session(client)
        verify(client, sid, auth)
        result = send(client, sid, auth, "Tôi muốn gặp chuyên viên")
        hid = result["handoff_id"]
        body = {"notes": "Đã tư vấn", "commitments": ["Gọi lại"], "idempotency_key": "one"}
        initial.state.harness.db.execute(
            "UPDATE handoffs SET status='completing',receipt=? WHERE id=?", (canonical({"pending_request": body}), hid)
        )
    candidate = create_app(Settings(db_path=path, release_gate=str(tmp_path / "not-promoted")))

    def forbid_network(*args, **kwargs):
        raise AssertionError("Preflight may only recover local state")

    monkeypatch.setattr(httpx, "post", forbid_network)
    with TestClient(candidate) as client:
        assert client.get("/healthz").status_code == 200
        harness = candidate.state.harness
        assert harness.db.one("SELECT status FROM handoffs WHERE id=?", (hid,))["status"] == "completed"
        assert len(harness.db.all("SELECT * FROM callbacks")) == 1


def test_tool_registry_and_trusted_verification(client):
    sid, auth = session(client)
    tools = client.get(f"/sessions/{sid}/tools", headers=auth).json()["tools"]
    assert len(tools) == 6
    assert all("as_of" in tool["parameters"]["required"] for tool in tools)
    result = send(client, sid, auth, "Ưu đãi vay mua nhà hiện tại?", "public")
    assert "7,1" in result["reply"]
    harness = client.app.state.harness
    assert harness.db.all("SELECT * FROM facts WHERE customer_id='hoa'") == []
    assert harness.db.all("SELECT * FROM memory_jobs") == []
    denied = harness.engine.bank.call("schedule_callback", {"time": "2026-11-02T09:00:00+07:00", "as_of": "2026-10-31"}, "hoa", sid)
    assert denied["error"].startswith("authentication_required:")
    verify(client, sid, auth)
    tools = client.get(f"/sessions/{sid}/tools", headers=auth).json()["tools"]
    assert len(tools) == 8
    assert "crm_lookup" not in {tool["name"] for tool in tools}
    assert all("customer_id" not in tool["parameters"]["properties"] for tool in tools)
    assert harness.engine.bank.call("crm_lookup", {"as_of": "2026-10-31"}, "hoa", sid)["error"].startswith("authentication_required:")
    client.post("/staff/login", json={"password": "staff-test"})
    assert len(client.get(f"/sessions/{sid}/tools", headers=auth).json()["tools"]) == 8


def test_verified_summary_and_callback_are_durable_mocks(client):
    sid, auth = session(client)
    verify(client, sid, auth)
    result = send(client, sid, auth, "Vay mua nhà 2 tỷ trong 20 năm, thu nhập 45 triệu, không có nợ, giá nhà 3 tỷ. Gửi tóm tắt", "summary")
    assert "tóm tắt mô phỏng" in result["reply"]
    harness = client.app.state.harness
    row = harness.db.one("SELECT * FROM outbox")
    assert row["status"] == "mock_sent" and json.loads(row["text"])
    assert all(f["observed_at"] == "2026-10-31" for f in json.loads(row["text"]))
    send(client, sid, auth, "Hẹn gọi lại sau 2 ngày", "missing-time")
    assert harness.db.all("SELECT * FROM callbacks") == []
    text = "Hẹn gọi lại sau 2 ngày lúc 14:30"
    first = send(client, sid, auth, text, "callback")
    assert send(client, sid, auth, text, "callback") == first
    callbacks = harness.db.all("SELECT * FROM callbacks")
    assert len(callbacks) == 1 and callbacks[0]["due_at"] == "2026-11-02T14:30:00+07:00"
    assert json.loads(callbacks[0]["payload"])["mock"] is True


def test_guest_cannot_read_existing_customer_memory(client):
    sid, auth = session(client)
    verify(client, sid, auth)
    send(client, sid, auth, "Thu nhập 99 triệu", "private")
    guest, token = session(client)
    reply = send(client, guest, token, "Cho xem hồ sơ của tôi lần trước", "guest")
    assert "số điện thoại" in reply["reply"] and "99" not in reply["reply"]
    trace = client.get(f"/traces/{guest}", headers=ADMIN).json()["items"][-1]
    assert not trace["tool_calls"]
    assert all(p["type"] != "call_brief" for p in trace["context_parts"])
    assert not any(f["key"] == "income" for f in trace["facts"])


def test_tool_rejects_spoofed_identity_and_context(client):
    from bank_tools.auth import AuthContext

    sid, auth = session(client)
    verify(client, sid, auth)
    bank = client.app.state.harness.engine.bank
    args = {"as_of": "2026-10-31", "time": "2026-11-02T09:00:00+07:00"}
    assert "forbidden" in bank.call("schedule_callback", {**args, "customer_id": "minh"}, "hoa", sid)["error"]
    assert "forbidden" in bank.call("schedule_callback", args, "hoa", sid, context={"identity_verified": True})["error"]
    assert "forbidden" in bank.call("schedule_callback", args, "minh", sid, context=AuthContext("minh", True))["error"]
    assert client.app.state.harness.db.all("SELECT * FROM callbacks") == []


def test_new_business_day_refreshes_sources_and_expires_policy(client):
    sid, auth = session(client)
    verify(client, sid, auth)
    text = "Vay mua nhà 2 tỷ trong 20 năm, thu nhập 45 triệu, không có nợ, giá nhà 3 tỷ"
    assert "7,1" in send(client, sid, auth, text, "day1")["reply"]
    client.post("/admin/clock", headers=ADMIN, json={"as_of": "2026-11-01"})
    assert "7,1" in send(client, sid, auth, "Tiếp tục", "day2")["reply"]
    trace = client.get(f"/traces/{sid}", headers=ADMIN).json()["items"][-1]
    assert all(call["args"]["as_of"] == "2026-11-01" for call in trace["tool_calls"])
    assert all(f["observed_at"] == "2026-11-01" for f in trace["facts"] if f["source_type"] == "bank_tool")
    client.post("/admin/clock", headers=ADMIN, json={"as_of": "2027-01-01"})
    result = send(client, sid, auth, "Tiếp tục", "expired")
    assert any(g["guard_id"] == "G2" for g in result["guard_events"])
    assert "7,1" not in result["reply"] and not client.app.state.harness.db.all("SELECT * FROM callbacks")


def test_explicit_zero_debt_and_long_income_confirmation():
    from harness.memory.service import extract

    assert extract("Không có nợ")["debt"] == ("0", "đồng")
    assert extract("Nợ 0")["debt"] == ("0", "đồng")
    assert "debt" not in extract("Vay mua nhà 2 tỷ")
    assert extract("Chị xác nhận thu nhập riêng hiện tại chính xác là 40 triệu đồng mỗi tháng.")["income"] == ("40000000", "đồng")
