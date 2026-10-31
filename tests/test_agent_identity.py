"""Anonymous sessions bind once through an owned, attributable tool operation."""

import json

import pytest
from fastapi.testclient import TestClient

from bank_tools.service import ident, now
from harness.api import create_app
from harness.config import Settings
from harness.identity import profiles

ADMIN = {"Authorization": "Bearer agent-identity-admin"}


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(
        db_path=str(tmp_path / "agent-identity.sqlite3"), admin_token="agent-identity-admin",
        staff_password="agent-staff", environment="development", llm_backend="rules",
        release_gate="", telegram_token="", telegram_secret="",
    ))
    with TestClient(app) as connection:
        yield connection


def anonymous(client, **body):
    response = client.post("/sessions", json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    return data, {"Authorization": "Bearer " + data["session_token"]}


def operation(client, session, phone, *, text=None, key="identify", source="test-ip"):
    harness = client.app.state.harness
    sid = session["session_id"]
    text = text if text is not None else "Số điện thoại của tôi là " + phone
    turn_id = ident("t_", [sid, key])
    harness.db.execute("INSERT OR IGNORE INTO turns VALUES(?,?,?,?,?,?,?)", (
        turn_id, sid, key, text, None, None, now(),
    ))
    return harness.agent_verify(
        sid, phone, text=text, source=source, turn_id=turn_id, operation_id=ident("op_", [turn_id, key]),
    )


def test_public_creation_never_preselects_a_fixture_even_for_old_clients(client):
    first, _ = anonymous(client)
    second, auth = anonymous(client, demo_profile="hoa")
    harness = client.app.state.harness
    for session in (first, second):
        row = harness.session(session["session_id"])
        assert row["customer_id"] == "anon_" + session["session_id"]
        assert row["verified"] == 0
        assert session["user_id"] is None
        assert session["brief"] == {"redacted": True}
    names = {tool["name"] for tool in client.get(f"/sessions/{second['session_id']}/tools", headers=auth).json()["tools"]}
    assert "verify_identity" in names and "crm_lookup" not in names and "send_summary" not in names


@pytest.mark.parametrize("profile", profiles(), ids=lambda p: p["phone"])
def test_any_of_ten_demo_phones_can_bind_a_neutral_session_once(client, profile):
    session, auth = anonymous(client)
    phone = "+84 " + profile["phone"][1:]
    result = operation(client, session, phone)
    assert result["error"] is None
    assert result["data"]["verified"] is True
    assert result["data"]["user_id"] == profile["phone"]
    assert result["data"]["brief"]["name"] == profile["name"]
    assert result["data"]["brief"]["redacted"] is False
    harness = client.app.state.harness
    row = harness.session(session["session_id"])
    assert row["customer_id"] == profile["id"] and row["verified"] == 1
    names = {tool["name"] for tool in client.get(f"/sessions/{row['id']}/tools", headers=auth).json()["tools"]}
    assert "send_summary" in names and "schedule_callback" in names and "crm_lookup" not in names
    audit = harness.db.one("SELECT args FROM tool_calls WHERE id=?", (result["tool_call_id"],))
    assert json.loads(audit["args"])["phone"] == "[redacted]"
    assert "dob" not in audit["args"]


def test_identity_operation_retry_reuses_receipt_and_never_switches_a_bound_customer(client):
    session, _ = anonymous(client)
    first, other = profiles()[1:3]
    receipt = operation(client, session, first["phone"])
    assert operation(client, session, "+84" + first["phone"][1:]) != receipt  # Different current text is a different request.
    assert operation(client, session, first["phone"]) == receipt
    denied = operation(client, session, other["phone"], key="switch")
    assert denied["error"].startswith("identity_locked")
    harness = client.app.state.harness
    assert harness.session(session["session_id"])["customer_id"] == first["id"]
    assert harness.db.one("SELECT count(*) AS n FROM tool_calls WHERE session_id=?", (session["session_id"],))["n"] == 2


@pytest.mark.parametrize("text,phone", [
    ("Tôi muốn hỏi ưu đãi", "0900000002"),
    ("Số điện thoại của vợ tôi là 0900000002", "0900000002"),
    ("Số của tôi là 0900000002 hoặc 0900000003", "0900000002"),
    ("Số điện thoại của tôi là 0900000003", "0900000002"),
])
def test_model_phone_must_be_one_current_explicit_own_claim(client, text, phone):
    session, _ = anonymous(client)
    result = operation(client, session, phone, text=text)
    assert result["error"]
    assert result["data"]["brief"] == {"redacted": True}
    row = client.app.state.harness.session(session["session_id"])
    assert row["customer_id"].startswith("anon_") and row["verified"] == 0


@pytest.mark.parametrize("text", ["Số điện thoại của em là +84 900 000 002", "+84 900-000-002"])
def test_own_pronouns_and_standalone_normalized_phone_are_accepted(client, text):
    session, _ = anonymous(client)
    result = operation(client, session, "0900000002", text=text)
    assert result["error"] is None and result["data"]["user_id"] == "0900000002"


def test_another_turn_or_session_cannot_supply_identity_evidence(client):
    own, _ = anonymous(client)
    other, _ = anonymous(client)
    result = operation(client, other, "0900000002")
    harness = client.app.state.harness
    turn = harness.db.one("SELECT id,text FROM turns WHERE session_id=?", (other["session_id"],))
    denied = harness.agent_verify(
        own["session_id"], "0900000002", text=turn["text"], source="test-ip",
        turn_id=turn["id"], operation_id=result["tool_call_id"],
    )
    assert denied["error"].startswith("forbidden")
    assert harness.session(own["session_id"])["verified"] == 0


def test_binding_and_receipt_survive_brief_failure_without_second_authentication(client, monkeypatch):
    session, _ = anonymous(client)
    harness = client.app.state.harness
    original = harness.engine.memory.brief

    def fail(*args, **kwargs):
        raise RuntimeError("brief interrupted")

    monkeypatch.setattr(harness.engine.memory, "brief", fail)
    with pytest.raises(RuntimeError, match="brief interrupted"):
        operation(client, session, "0900000002")
    assert harness.session(session["session_id"])["customer_id"] == "minh"
    stored = harness.db.one("SELECT result FROM tool_calls WHERE session_id=?", (session["session_id"],))
    assert json.loads(stored["result"])["data"]["brief"]["pending"] is True
    monkeypatch.setattr(harness.engine.memory, "brief", original)
    completed = operation(client, session, "0900000002")
    assert completed["data"]["brief"]["name"] == "Trần Minh An"
    assert harness.db.one("SELECT count(*) AS n FROM tool_calls WHERE session_id=?", (session["session_id"],))["n"] == 1
    assert not harness.db.all("SELECT * FROM auth_attempts")


def test_legacy_unverified_web_preselection_migrates_but_admin_pinning_survives(client, monkeypatch):
    harness = client.app.state.harness
    legacy = harness.new_session("hoa")
    sid = legacy["session_id"]
    harness.db.execute("DELETE FROM session_identity_modes WHERE session_id=?", (sid,))
    harness.db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?,?)", (
        "legacy-public", sid, "public", "Tôi muốn xem lãi suất công khai", "Xin chào", "{}", now(),
    ))
    cleared = []
    monkeypatch.setattr(harness.engine.checkpointer, "delete_thread", cleared.append)
    migrated = harness.prepare_public_session(sid)
    assert migrated["customer_id"] == "anon_" + sid and migrated["verified"] == 0
    assert harness.db.one("SELECT text FROM turns WHERE id='legacy-public'")["text"] == "Tôi muốn xem lãi suất công khai"
    assert cleared == [sid]
    admin = client.post("/admin/sessions", headers=ADMIN, json={"demo_profile": "hoa"}).json()
    assert harness.prepare_public_session(admin["session_id"])["customer_id"] == "hoa"
    denied = operation(client, admin, "0900000002")
    assert denied["error"].startswith("identity_mismatch")


def test_rules_anonymous_phone_text_goes_through_engine_without_service_shortcut(client, monkeypatch):
    session, auth = anonymous(client)
    harness = client.app.state.harness
    seen = []

    def run(server_session, text, turn, *, source):
        seen.append((text, source))
        return {"reply": "Engine đã tiếp nhận"}

    monkeypatch.setattr(harness.engine, "run", run)
    response = client.post(f"/sessions/{session['session_id']}/messages", headers=auth, json={
        "text": "Số điện thoại của tôi là 0900000002", "client_message_id": "identity-text",
    })
    assert response.status_code == 200
    assert seen == [("Số điện thoại của tôi là 0900000002", "testclient")]
    assert not harness.db.all("SELECT * FROM tool_calls")
    assert harness.session(session["session_id"])["verified"] == 0


def test_foreign_capability_cannot_bind_neutral_session_using_verify_endpoint(client):
    own, _ = anonymous(client)
    _, foreign_auth = anonymous(client)
    response = client.post(f"/sessions/{own['session_id']}/verify", headers=foreign_auth, json={"phone": "0900000002"})
    assert response.status_code == 403
    assert client.app.state.harness.session(own["session_id"])["verified"] == 0


def test_failed_phone_format_variants_share_limit_across_new_sessions_and_sources(client):
    variants = ["0909999999", "+84909999999", "090-999-9999", "+84 90 999 9999", "0909999999"]
    for index, phone in enumerate(variants):
        session, _ = anonymous(client)
        result = operation(client, session, phone, source=f"source-{index}")
        assert result["error"].startswith("identity_not_found")
    session, _ = anonymous(client)
    limited = operation(client, session, "+84909999999", source="new-source")
    assert limited["error"].startswith("rate_limited")
    assert client.app.state.harness.session(session["session_id"])["verified"] == 0


def test_phone_and_financial_need_are_enqueued_once_after_agent_binding(client, monkeypatch):
    session, _ = anonymous(client)
    harness = client.app.state.harness
    text = "Số điện thoại của tôi là 0900000002. Tôi muốn vay mua nhà 2 tỷ trong 20 năm."
    calls = []

    def run(server_session, current_text, turn_id, *, source):
        calls.append(turn_id)
        result = harness.agent_verify(
            server_session["id"], "0900000002", text=current_text, source=source,
            turn_id=turn_id, operation_id=ident("op_", [turn_id, "identity"]),
        )
        assert result["error"] is None
        return {"reply": "Đã nhận diện và tiếp nhận nhu cầu.", "tool_calls": [result]}

    monkeypatch.setattr(harness.engine, "run", run)
    response = harness.message(session["session_id"], text, "loan-and-phone", source="unit-source")
    assert response["user_id"] == "0900000002" and response["identity_status"] == "verified"
    assert response["brief"]["name"] == "Trần Minh An"
    assert harness.message(session["session_id"], text, "loan-and-phone", source="unit-source") == response
    assert len(calls) == 1
    jobs = harness.db.all("SELECT customer_id,payload FROM memory_jobs")
    assert len(jobs) == 1 and jobs[0]["customer_id"] == "minh"
    assert json.loads(jobs[0]["payload"])["text"] == text


def test_effective_configuration_exposes_history_mode(client):
    response = client.get("/admin/state", headers=ADMIN)
    assert response.status_code == 200
    assert response.json()["effective_config"]["identity_history_enabled"] is False


def test_fixture_hash_tracks_input_journeys_but_excludes_oracle_answers(tmp_path, monkeypatch):
    from bank_tools import service

    bank = tmp_path / "bank"
    bank.mkdir()
    (bank / "bank.yaml").write_text("schema_version: 2\n")
    inputs = tmp_path / "identity_v1"
    (inputs / "journeys").mkdir(parents=True)
    identities = inputs / "identities.json"
    identities.write_text('{"synthetic": true}')
    journey = inputs / "journeys/0900000002.json"
    journey.write_text('{"text": "old customer need"}')
    monkeypatch.setattr(service, "FIXTURE_PATH", bank / "bank.yaml")
    monkeypatch.setattr(service, "IDENTITY_PATH", identities)
    before = service.fixture_hash()
    journey.write_text('{"text": "changed customer need"}')
    after = service.fixture_hash()
    assert before != after
    oracle = tmp_path / "eval/oracle_fixtures/identity_v1"
    oracle.mkdir(parents=True)
    (oracle / "0900000002.json").write_text('{"reference_text": "never part of input"}')
    assert service.fixture_hash() == after
