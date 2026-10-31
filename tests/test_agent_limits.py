"""A model's tool loop must stop without executing unapproved extra work."""

import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_agent_tools import Provider, answer, calls, context, message, new_session, settings

from harness import llm
from harness.agent import tool_loop
from harness.api import create_app
from harness.llm import DEFAULT_REPLY
from harness.memory import service as memory_service


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


def test_input_classifier_consumes_the_shared_whole_message_budget(tmp_path, monkeypatch):
    clock = {"now": time.monotonic()}
    fake_time = SimpleNamespace(monotonic=lambda: clock["now"], time=time.time, sleep=time.sleep)
    for module in (llm, tool_loop, memory_service):
        monkeypatch.setattr(module, "time", fake_time)

    class SlowClassifier(Provider):
        classifications = 0

        async def post(self, client, url, **kwargs):
            instruction = kwargs["json"]["messages"][0]["content"]
            if "INPUT_SECURITY_CLASSIFIER" in instruction:
                self.classifications += 1
                clock["now"] += 2
            return await super().post(client, url, **kwargs)

    provider = SlowClassifier([])
    provider.install(monkeypatch)
    config = settings(tmp_path, identity_history_enabled=False, agent_turn_budget_seconds=1)
    with TestClient(create_app(config)) as client:
        session, auth = new_session(client)
        reply = message(client, session, auth, "Tôi muốn xem ưu đãi công khai")
        assert_stopped(reply, "agent_turn_deadline")
        assert reply["identity_status"] == "unverified" and reply["user_id"] is None
        assert provider.classifications == 1
        assert provider.requests == []
        db = client.app.state.harness.db
        assert not db.all("SELECT * FROM tool_calls")
        assert not db.all("SELECT * FROM callbacks")
        assert not db.all("SELECT * FROM outbox")


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


@pytest.mark.parametrize("cancellation", [
    "Tôi hủy yêu cầu gọi lại; không đặt lịch nữa.",
    "Tôi hủy yêu cầu gọi lại.",
])
def test_cancellation_removes_a_previous_pending_callback_request(tmp_path, monkeypatch, cancellation):
    provider = Provider([
        calls(("identity", "verify_identity", {"phone": "0900000002"})), answer(),
        calls(("callback", "schedule_callback", {"time": "2026-11-02T09:00:00+07:00"})), answer(),
        calls(("new-callback", "schedule_callback", {"time": "2026-11-03T10:00:00+07:00"})), answer(),
    ])
    provider.install(monkeypatch)
    with TestClient(create_app(settings(tmp_path, identity_history_enabled=False))) as client:
        session, auth = new_session(client)
        message(client, session, auth, "Số điện thoại của tôi là 0900000002. Hẹn gọi lại sau 2 ngày lúc 09:00.", "request")
        assert not client.app.state.harness.db.all("SELECT * FROM callbacks")
        message(client, session, auth, cancellation, "cancel")
        assert not client.app.state.harness.db.all("SELECT * FROM callbacks")
        assert len(provider.requests) == 4
        message(client, session, auth, "Hẹn gọi lại ngày 2026-11-03 lúc 10:00.", "fresh-request")
        rows = client.app.state.harness.db.all("SELECT due_at FROM callbacks")
        assert rows == [{"due_at": "2026-11-03T10:00:00+07:00"}]
        assert len(provider.requests) == 6


def test_summary_cancellation_revokes_old_consent_but_allows_a_fresh_request(tmp_path, monkeypatch):
    fact_ids = []

    def retain_issued_fact(payload):
        fact = next(fact for fact in context(payload)["facts"].values() if fact["key"] == "promo_rate")
        fact_ids.append(fact["id"])
        return answer()

    def summary(_payload):
        return calls(("summary", "send_summary", {"channel": "web", "fact_ids": fact_ids}))

    provider = Provider([
        calls(("identity", "verify_identity", {"phone": "0900000002"})),
        promotions("promotion"), retain_issued_fact,
        summary, answer(), summary, answer(),
    ])
    provider.install(monkeypatch)
    with TestClient(create_app(settings(tmp_path, identity_history_enabled=False))) as client:
        session, auth = new_session(client)
        message(client, session, auth, "Số điện thoại của tôi là 0900000002. Gửi tóm tắt qua web giúp tôi.", "request")
        db = client.app.state.harness.db
        assert fact_ids and not db.all("SELECT * FROM outbox")
        message(client, session, auth, "Tôi hủy yêu cầu gửi tóm tắt.", "cancel")
        assert not db.all("SELECT * FROM outbox")
        assert not db.all("SELECT * FROM tool_calls WHERE tool='send_summary'")
        message(client, session, auth, "Giờ tôi muốn gửi tóm tắt qua web.", "fresh-request")
        assert len(db.all("SELECT * FROM outbox WHERE status='mock_sent'")) == 1
        assert len(db.all("SELECT * FROM tool_calls WHERE tool='send_summary'")) == 1
