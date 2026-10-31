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
        Settings(db_path=str(tmp_path / "test.sqlite3"), admin_token="admin-test", staff_password="staff-test")
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
        f"/sessions/{sid}/verify", headers=auth, json={"dob": "1988-05-12" if profile == "hoa" else "1992-08-20"}
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
            client.post(f"/sessions/{sid}/verify", headers=a, json={"dob": "wrong"}).json()["identity_status"]
            == "unverified"
        )
    sid, b = session(client)
    assert client.post(f"/sessions/{sid}/verify", headers=b, json={"dob": "1988-05-12"}).status_code == 429


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
    r = send(client, sid, a, "Thu nhập 45 triệu mỗi tháng", "b")
    assert "2 tỷ" in r["reply"] and "6,9" in r["reply"]
    trace = client.get(f"/traces/{sid}", headers=ADMIN).json()["items"][-1]
    for span in trace["source_spans"]:
        assert r["reply"][span["start"] : span["end"]]
        assert any(f["id"] == span["fact_id"] for f in trace["facts"])


def test_memory_without_old_transcript(client):
    sid, a = session(client)
    verify(client, sid, a)
    send(client, sid, a, "Vay mua nhà 2 tỷ trong 20 năm thu nhập 45 triệu. SECRET_CANARY")
    client.post(f"/sessions/{sid}/end", headers=a)
    new, b = session(client)
    verify(client, new, b)
    assert "6,9" in send(client, new, b, "Tiếp tục tư vấn")["reply"]
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


def test_fault_safe_callback(client):
    sid, a = session(client)
    verify(client, sid, a)
    client.post("/admin/faults", headers=ADMIN, json={"tool": "get_rate", "mode": "timeout", "count": 1})
    r = send(client, sid, a, "Vay mua nhà 2 tỷ trong 20 năm thu nhập 45 triệu")
    assert not any(x.isdigit() for x in r["reply"])
    assert any(x["guard_id"] == "G2" for x in r["guard_events"])
    assert len(client.get("/callbacks", headers=ADMIN).json()["items"]) == 1


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
    assert "6,5" in send(client, sid, a, "Vay mua nhà 2 tỷ trong 20 năm thu nhập 45 triệu")["reply"]
    client.post("/admin/clock", headers=ADMIN, json={"as_of": "2026-10-31"})
    sid, b = session(client)
    verify(client, sid, b)
    r = send(client, sid, b, "Tiếp tục tư vấn")
    assert "6,9" in r["reply"] and "hết hạn" in r["reply"]


def test_card_and_unsuitable(client):
    sid, a = session(client)
    verify(client, sid, a)
    assert "100 triệu" in send(client, sid, a, "Tôi cần thẻ tín dụng thu nhập 40 triệu")["reply"]
    sid, b = session(client, "minh")
    verify(client, sid, b, "minh")
    r = send(client, sid, b, "Vay tiêu dùng 100 triệu trong 24 tháng thu nhập 2 triệu")
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


def test_admin_reset(client):
    sid, a = session(client)
    verify(client, sid, a)
    send(client, sid, a, "Vay mua nhà 2 tỷ trong 20 năm thu nhập 45 triệu")
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
    a = h.engine.bank.call("get_rate", {"product": "home_loan", "amount": 2000000000}, "hoa", "test-session")
    b = h.engine.bank.call("get_rate", {"amount": 2000000000, "product": "home_loan"}, "hoa", "test-session")
    assert [f["fact_id"] for f in a["facts"]] == [f["fact_id"] for f in b["facts"]]
    client.post("/admin/faults", headers=ADMIN, json={"tool": "get_rate", "mode": "error", "count": 1})
    assert h.engine.bank.call("get_rate", {}, "hoa", "test-session")["error"] == "error"
    assert h.engine.bank.call("get_rate", {}, "hoa", "test-session")["error"] is None


def test_guard_revalidates_second_draft(client, monkeypatch):
    engine = client.app.state.harness.engine
    state = {
        "session": {"mode": "harness", "customer_id": "hoa"},
        "draft": "Lãi {fact:missing}",
        "facts": {},
        "tool_error": False,
        "unsuitable": False,
        "disclosures": [],
        "plan": {"missing": []},
    }
    monkeypatch.setattr(engine, "rules_draft", lambda _: "Lãi {fact:missing}")
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
    ["Lãi sáu phần trăm mỗi năm", "Khoản vay hai tỷ đồng", "Lãi 6.9%", "Lãi {fact:missing}", "Lãi {fact:broken"],
)
def test_unbacked_draft_variants(client, monkeypatch, draft):
    engine = client.app.state.harness.engine
    state = {
        "session": {"mode": "harness", "customer_id": "hoa"},
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

    def fake_post(url, **kwargs):
        instruction = kwargs["json"]["messages"][0]["content"]
        if "Phân loại" in instruction:
            result = {"stage": "quote", "objection": None}
        elif "Trích" in instruction:
            result = {"spans": ["thu nhập 999 triệu", "Thu nhập 45 triệu"]}
        else:
            result = {"draft": "Em có thể cho vay 999 tỷ đồng."}
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(result)}}]}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    sid, a = session(client)
    verify(client, sid, a)
    r = send(client, sid, a, "Vay mua nhà 2 tỷ trong 20 năm. Thu nhập 45 triệu")
    assert "999" not in r["reply"] and "6,9" in r["reply"]
    assert any(e["guard_id"] == "G1" for e in r["guard_events"])
    brief = client.get("/customers/hoa/brief", headers=ADMIN).json()
    assert not any(f["value"] == "999000000" for f in brief["facts"])

    def malformed(url, **kwargs):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"unexpected":true}'}}]}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", malformed)
    assert "6,9" in send(client, sid, a, "Tiếp tục", "next")["reply"]


def test_dob_not_persisted_in_tool_audit(client):
    sid, a = session(client)
    verify(client, sid, a)
    rows = client.app.state.harness.db.all("SELECT args FROM tool_calls WHERE tool='verify_identity'")
    assert rows and "1988-05-12" not in json.dumps(rows)


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
    send(client, sid, a, "Vay mua nhà 2 tỷ trong 20 năm thu nhập 45 triệu")
    response = client.post(f"/sessions/{sid}/handoff", headers=a, json={"reason": "Tư vấn hồ sơ"})
    assert response.status_code == 200
    assert response.json()["bot_said"][0]["tool_call_ids"]
    assert response.json()["bot_said"][0]["fact_ids"]


def test_callback_uses_business_clock(client):
    sid, a = session(client)
    verify(client, sid, a)
    send(client, sid, a, "Hẹn gọi lại sau 2 ngày")
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
    )
    app = create_app(settings)
    harness = app.state.harness
    with harness.db.transaction() as conn:
        harness.engine.memory.enqueue(
            conn, "pending-before-release", "hoa", "previous-session", "web", "Thu nhập 45 triệu"
        )

    def forbid_network(*args, **kwargs):
        raise AssertionError("Candidate preflight must not make network requests")

    monkeypatch.setattr(httpx, "post", forbid_network)
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
