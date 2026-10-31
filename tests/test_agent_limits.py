"""A model's tool loop must stop without executing unapproved extra work."""

import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_agent_tools import Provider, answer, calls, message, new_session, settings

from harness.agent import tool_loop
from harness.api import create_app
from harness.llm import DEFAULT_REPLY


def promotions(key, product="home_loan"):
    return calls((key, "get_active_promotions", {"product": product, "channel": "web"}))


def bank_audits(client):
    return client.app.state.harness.db.all("SELECT tool,result FROM tool_calls WHERE tool!='verify_identity'")


def assert_stopped(reply, reason):
    assert reply["reply"] == DEFAULT_REPLY
    assert reply["used_fact_ids"] == []
    assert any(event["guard_id"] == "G2" and event["reason"] == reason for event in reply["guard_events"])


def test_agent_round_limit_stops_further_provider_and_tool_calls(tmp_path, monkeypatch):
    provider = Provider([promotions("round-one"), promotions("round-two")])
    provider.install(monkeypatch)
    config = settings(tmp_path, identity_history_enabled=False, agent_max_rounds=2)
    with TestClient(create_app(config)) as client:
        session, auth = new_session(client)
        reply = message(client, session, auth, "Cho tôi xem chương trình công khai")
        assert_stopped(reply, "agent_round_limit")
        assert len(provider.requests) == 2
        assert len(bank_audits(client)) == 2
        assert not client.app.state.harness.db.all("SELECT * FROM callbacks")


def test_agent_tool_limit_stops_inside_a_proposed_batch(tmp_path, monkeypatch):
    provider = Provider([calls(
        ("one", "get_active_promotions", {"product": "home_loan", "channel": "web"}),
        ("two", "get_active_promotions", {"product": "personal_loan", "channel": "web"}),
    )])
    provider.install(monkeypatch)
    config = settings(tmp_path, identity_history_enabled=False, agent_max_tool_calls=1)
    with TestClient(create_app(config)) as client:
        session, auth = new_session(client)
        reply = message(client, session, auth, "Các chương trình công khai nào còn hiệu lực?")
        assert_stopped(reply, "agent_tool_limit")
        assert len(provider.requests) == 1
        assert len(bank_audits(client)) == 1


@pytest.mark.parametrize("second_product", ["home_loan", "personal_loan"])
def test_provider_call_ids_cannot_be_reused_across_rounds(tmp_path, monkeypatch, second_product):
    provider = Provider([promotions("duplicate"), promotions("duplicate", second_product)])
    provider.install(monkeypatch)
    with TestClient(create_app(settings(tmp_path, identity_history_enabled=False))) as client:
        session, auth = new_session(client)
        reply = message(client, session, auth, "Tôi muốn hỏi thông tin công khai")
        assert_stopped(reply, "agent_repeated_call_id")
        assert len(provider.requests) == 2
        assert len(bank_audits(client)) == 1


def test_turn_deadline_is_checked_between_tools_in_the_same_batch(tmp_path, monkeypatch):
    provider = Provider([calls(
        ("one", "get_active_promotions", {"product": "home_loan", "channel": "web"}),
        ("two", "get_active_promotions", {"product": "personal_loan", "channel": "web"}),
    )])
    provider.install(monkeypatch)
    clock = {"now": time.monotonic()}
    monkeypatch.setattr(tool_loop, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    config = settings(tmp_path, identity_history_enabled=False, agent_turn_budget_seconds=1)
    with TestClient(create_app(config)) as client:
        original = client.app.state.harness.engine.bank.call

        def consume_budget(*args, **kwargs):
            result = original(*args, **kwargs)
            clock["now"] += 2
            return result

        monkeypatch.setattr(client.app.state.harness.engine.bank, "call", consume_budget)
        session, auth = new_session(client)
        reply = message(client, session, auth, "Tôi muốn xem ưu đãi công khai")
        assert_stopped(reply, "agent_turn_deadline")
        assert len(provider.requests) == 1
        assert len(bank_audits(client)) == 1


@pytest.mark.parametrize("decline", [
    "Không đặt lịch gọi lại ngày 2026-11-02 lúc 09:00.",
    "Đừng hẹn gọi lại ngày 2026-11-02 lúc 09:00.",
])
def test_current_negative_callback_request_cannot_be_treated_as_consent(tmp_path, monkeypatch, decline):
    provider = Provider([
        calls(("identity", "verify_identity", {"phone": "0900000002"})),
        calls(("callback", "schedule_callback", {"time": "2026-11-02T09:00:00+07:00"})),
        answer(),
    ])
    provider.install(monkeypatch)
    with TestClient(create_app(settings(tmp_path, identity_history_enabled=False))) as client:
        session, auth = new_session(client)
        reply = message(client, session, auth, "Số điện thoại của tôi là 0900000002. " + decline)
        assert reply["identity_status"] == "verified"
        assert not client.app.state.harness.db.all("SELECT * FROM callbacks")
        trace = json.loads(client.app.state.harness.db.one(
            "SELECT data FROM traces WHERE session_id=? ORDER BY rowid DESC LIMIT 1", (session["session_id"],),
        )["data"])
        callback = next(record for record in trace["tool_calls"] if record["tool"] == "schedule_callback")
        assert callback["result"]["error"]


def test_cancellation_removes_a_previous_pending_callback_request(tmp_path, monkeypatch):
    provider = Provider([
        calls(("identity", "verify_identity", {"phone": "0900000002"})), answer(),
        calls(("callback", "schedule_callback", {"time": "2026-11-02T09:00:00+07:00"})), answer(),
    ])
    provider.install(monkeypatch)
    with TestClient(create_app(settings(tmp_path, identity_history_enabled=False))) as client:
        session, auth = new_session(client)
        message(client, session, auth, "Số điện thoại của tôi là 0900000002. Hẹn gọi lại sau 2 ngày lúc 09:00.", "request")
        assert not client.app.state.harness.db.all("SELECT * FROM callbacks")
        message(client, session, auth, "Tôi hủy yêu cầu gọi lại; không đặt lịch nữa.", "cancel")
        assert not client.app.state.harness.db.all("SELECT * FROM callbacks")
        assert len(provider.requests) == 4
