"""Demo journey inputs bootstrap needs without replacing live customer facts."""

import json

import pytest
from fastapi.testclient import TestClient

from harness.api import create_app
from harness.config import Settings


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(
        db_path=str(tmp_path / "history.sqlite3"), demo_phone_identity=True, llm_backend="rules",
    ))) as client:
        yield client


def enter(client, phone="0900000002"):
    result = client.post("/demo/phone/session", json={"phone": phone})
    assert result.status_code == 200
    return result.json()


def send(client, session, text="Nhu cầu vay của tôi là gì ?", key="recall", stream=False):
    route = f"/sessions/{session['session_id']}/messages"
    if stream:
        route += "/stream"
    response = client.post(
        route,
        headers={"Authorization": f"Bearer {session['session_token']}"},
        json={"text": text, "client_message_id": key},
    )
    assert response.status_code == 200, response.text
    if not stream:
        return response.json()
    events = [b.splitlines() for b in response.text.split("\n\n") if b.startswith("event:")]
    assert events[-1][0] == "event: result"
    return json.loads(events[-1][1][6:])


def trace(client, session):
    row = client.app.state.harness.db.one(
        "SELECT data FROM traces WHERE session_id=? ORDER BY rowid DESC LIMIT 1",
        (session["session_id"],),
    )
    return json.loads(row["data"])


@pytest.mark.parametrize("stream", [False, True])
def test_demo_loan_recall_uses_json_not_a_new_quote(client, stream):
    session = enter(client)
    result = send(client, session, stream=stream)
    assert "4 tỷ" in result["reply"] and "300 tháng" in result["reply"]
    assert "mua nhà" in result["reply"] and "demo" in result["reply"]
    evidence = trace(client, session)
    assert evidence["plan"]["intent"] == "needs_recall"
    assert not evidence["tool_calls"]
    assert not result["guard_events"]
    values = {f["key"]: f["value"] for f in evidence["facts"]}
    assert values["amount"] == "4000000000" and values["term"] == "300"
    assert values["income"] == "100000000" and values["debt"] == "10000000"
    assert values["property_value"] == "6000000000"
    assert not any("s2-zalo" in f["source_ref"] for f in evidence["facts"])
    assert any(s["source_ref"].startswith("demo_history:") for s in evidence["source_spans"])
    assert send(client, session, stream=stream) == result
    assert not client.app.state.harness.db.all("SELECT * FROM facts WHERE source_type='demo_history'")


@pytest.mark.parametrize("phone,amount,term", [
    ("0900000001", "1800000000", "240"),
    ("0900000002", "4000000000", "300"),
    ("0900000003", "200000000", "180"),
    ("0900000004", "900000000", "240"),
    ("0900000005", "2000000000", "240"),
    ("0900000006", "600000000", "240"),
    ("0900000007", "400000000", "240"),
    ("0900000008", "4500000000", "300"),
    ("0900000009", "800000000", "240"),
    ("0900000010", "150000000", "180"),
])
def test_each_demo_uses_latest_declared_needs_not_advertised_terms(client, phone, amount, term):
    session = enter(client, phone)
    values = {f["key"]: f["value"] for f in session["brief"]["needs"]}
    assert values["amount"] == amount and values["term"] == term


def test_live_update_overrides_only_its_field_across_new_sessions(client):
    first = enter(client)
    send(client, first, "Tôi giảm khoản vay còn 3 tỷ", "change")
    second = enter(client)
    result = send(client, second)
    assert "3 tỷ" in result["reply"] and "4 tỷ" not in result["reply"]
    assert "300 tháng" in result["reply"]
    values = {f["key"]: f for f in trace(client, second)["facts"]}
    assert values["amount"]["source_type"] == "customer"
    assert values["term"]["source_type"] == "demo_history"


def test_unverified_unknown_and_disabled_sessions_do_not_read_demo_history(client):
    session = client.post("/sessions", json={"demo_profile": "minh"}).json()
    assert "4 tỷ" not in send(client, session)["reply"]
    assert not any(f["source_type"] == "demo_history" for f in trace(client, session)["facts"])
    assert "chưa" in send(client, enter(client, "0901234567"))["reply"]
    known = enter(client)
    client.app.state.harness.settings.demo_phone_identity = False
    assert "4 tỷ" not in send(client, known)["reply"]


def test_live_product_change_does_not_inherit_old_loan_terms(client):
    session = enter(client)
    changed = send(client, session, "Tôi muốn thẻ tín dụng, thu nhập 50 triệu, không có nợ", "card")
    changed_facts = trace(client, session)["facts"]
    assert "4 tỷ" not in changed["reply"] and "300 tháng" not in changed["reply"]
    assert not any(f["key"] in {"amount", "term", "property_value", "loan_status"} for f in changed_facts)
    result = send(client, enter(client))
    assert "4 tỷ" not in result["reply"] and "300 tháng" not in result["reply"]
    assert "thẻ tín dụng" in result["reply"]


def test_declaration_is_saved_instead_of_treated_as_a_recall_question(client):
    session = enter(client)
    send(client, session, "Nhu cầu vay của tôi là vay tiêu dùng", "new-product")
    assert trace(client, session)["plan"]["intent"] == "advice"
    result = send(client, enter(client))
    assert "vay tiêu dùng" in result["reply"] and "4 tỷ" not in result["reply"]


def test_mixed_name_and_needs_recall_answers_both(client):
    session = enter(client)
    result = send(client, session, "Tôi tên gì và nhu cầu vay của tôi là gì?")
    assert "Trần Minh An" in result["reply"] and "4 tỷ" in result["reply"]
    assert not trace(client, session)["tool_calls"]


def test_recall_does_not_call_llm_planner_or_drafter(client, monkeypatch):
    session = enter(client)

    def unexpected(*args, **kwargs):
        pytest.fail("Recall must render recorded facts without model rewriting")

    monkeypatch.setattr("harness.agent.engine.json_completion", unexpected)
    assert "4 tỷ" in send(client, session, stream=True)["reply"]
    db = client.app.state.harness.db
    for table in ("tool_calls", "callbacks", "handoffs", "outbox", "memory_jobs"):
        assert not db.all("SELECT * FROM " + table)


def test_initial_history_obeys_clock_and_never_uses_future_sessions(client):
    db = client.app.state.harness.db
    db.backup()
    db.set("clock", "2026-09-28")
    assert enter(client)["brief"]["needs"] == []
    db.set("clock", "2026-09-29")
    session = enter(client, "0900000001")
    assert {f["key"]: f["value"] for f in session["brief"]["needs"]}["amount"] == "2000000000"
    assert all(m["at"][:10] <= "2026-09-29" for m in session["brief"]["demo_history"])


def test_no_memory_mode_does_not_recall_demo_history(client):
    session = client.post("/sessions", json={"demo_profile": "minh", "mode": "no_memory"}).json()
    send(client, session, "0900000002", "identify")
    assert "4 tỷ" not in send(client, session)["reply"]
    assert not any(p["type"] == "demo_history" for p in trace(client, session)["context_parts"])


def test_full_history_mode_reads_seed_and_latest_live_update(client):
    first = enter(client)
    send(client, first, "Tôi giảm khoản vay còn 3 tỷ", "change")
    session = client.post("/sessions", json={"demo_profile": "minh", "mode": "full_history"}).json()
    send(client, session, "0900000002", "identify")
    result = send(client, session)
    assert "3 tỷ" in result["reply"] and "4 tỷ" not in result["reply"]
    amounts = [f for f in trace(client, session)["facts"] if f["key"] == "amount"]
    assert len(amounts) == 1 and amounts[0]["source_type"] == "customer"


def test_negated_example_does_not_raise_income_or_create_actions(client):
    session = enter(client, "0900000010")
    values = {f["key"]: f["value"] for f in session["brief"]["needs"]}
    assert values["income"] == "6000000"  # "không tự đổi lương thành 15 triệu" is not an update.
    assert values["loan_status"] == "tạm hoãn kế hoạch vay"
    assert "tạm hoãn" in send(client, session)["reply"]


def test_conflicted_live_field_cannot_fall_back_to_seed(client):
    first = enter(client)
    send(client, first, "Tôi muốn vay 3 tỷ", "first")
    harness = client.app.state.harness
    other = harness.new_session("minh", channel="telegram", external_id="synthetic-test")
    harness.verify(harness.session(other["session_id"]), "0900000002", "test")
    harness.message(other["session_id"], "Tôi muốn vay 2 tỷ", "other", "test")
    harness.engine.memory.flush("minh")
    result = send(client, first)
    assert "chưa thống nhất" in result["reply"]
    assert "4 tỷ" not in result["reply"]
    assert not any(f["key"] == "amount" for f in trace(client, first)["facts"])


@pytest.mark.parametrize("text,expected", [
    (
        (
            "Tôi vay 4.000.000.000 đồng, thu nhập 100.000.000 đồng, nợ 10.000.000 đồng, "
            "căn nhà giá 6.000.000.000 đồng"
        ),
        {
            "amount": "4000000000",
            "income": "100000000",
            "debt": "10000000",
            "property_value": "6000000000",
        },
    ),
    (
        "Tôi vay 1,8 tỷ, thu nhập 80 triệu",
        {"amount": "1800000000", "income": "80000000"},
    ),
    ("Tôi vay 4000000000 VND", {"amount": "4000000000"}),
])
def test_explicit_currency_and_scaled_amounts(text, expected):
    from harness.memory.service import extract

    assert {k: v[0] for k, v in extract(text).items()} == expected
