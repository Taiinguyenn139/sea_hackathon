"""Exercise actual function-call envelopes with a mocked provider, not a regex agent."""

import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from harness.api import create_app
from harness.config import Settings
from harness.llm import DEFAULT_REPLY, tool_completion


def settings(tmp_path, **overrides):
    return Settings(
        db_path=str(tmp_path / "agent.sqlite3"), admin_token="agent-test-admin", staff_password="agent-test-staff",
        llm_backend="openai", openai_key="test-only", openai_model="primary", openai_backup_model="backup",
        environment="development", release_gate="", telegram_token="", telegram_secret="", **overrides,
    )


def answer(draft="Anh/chị muốn tiếp tục nhu cầu nào?", **kwargs):
    return {"finish_reason": "stop", "message": {"content": json.dumps({"draft": draft, **kwargs})}}


def calls(*items):
    return {"finish_reason": "tool_calls", "message": {"tool_calls": [
        {"id": key, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        for key, name, args in items
    ]}}


class Provider:
    def __init__(self, steps):
        self.steps = list(steps)
        self.requests = []

    async def post(self, _client, url, **kwargs):
        payload = kwargs["json"]
        instruction = payload["messages"][0]["content"]
        if "INPUT_SECURITY_CLASSIFIER" in instruction:
            value = {"category": "safe"}
        elif "tools" not in payload:
            value = {"spans": []}
        else:
            self.requests.append(payload)
            assert self.steps, "Unexpected extra agent request"
            step = self.steps.pop(0)
            if callable(step):
                step = step(payload)
            if isinstance(step, httpx.Response):
                return step
            return httpx.Response(200, json={"choices": [step]}, request=httpx.Request("POST", url))
        return httpx.Response(200, json={"choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps(value)}}
        ]}, request=httpx.Request("POST", url))

    def install(self, monkeypatch):
        async def post(client, url, **kwargs):
            return await self.post(client, url, **kwargs)
        monkeypatch.setattr(httpx.AsyncClient, "post", post)


def new_session(client):
    response = client.post("/sessions", json={"mode": "harness"})
    assert response.status_code == 200, response.text
    session = response.json()
    assert session["identity_status"] == "unverified" and session["user_id"] is None
    return session, {"Authorization": "Bearer " + session["session_token"]}


def message(client, session, auth, text, key="first"):
    response = client.post(f"/sessions/{session['session_id']}/messages", headers=auth, json={
        "text": text, "client_message_id": key,
    })
    assert response.status_code == 200, response.text
    return response.json()


def context(payload):
    return json.loads(payload["messages"][0]["content"].split("BACKEND_CONTEXT_JSON:\n", 1)[1])


def test_model_calls_identity_then_receives_an_and_sourced_history(tmp_path, monkeypatch):
    def finish(payload):
        data = context(payload)
        assert data["brief"]["name"] == "Trần Minh An"
        assert data["brief"]["user_id"] == "0900000002"
        assert len(data["brief"]["history"]) == 9
        names = {tool["function"]["name"] for tool in payload["tools"]}
        assert "send_summary" in names and "schedule_callback" in names and "crm_lookup" not in names
        values = {fact["key"]: fact for fact in data["facts"].values()}
        assert values["amount"]["value"] == "4000000000"
        assert values["term"]["value"] == "300"
        return answer(f"Chào anh An. Anh muốn tiếp tục khoản vay {{fact:{values['amount']['id']}}} chứ?")

    provider = Provider([calls(("identify", "verify_identity", {"phone": "0900000002"})), finish])
    provider.install(monkeypatch)
    with TestClient(create_app(settings(tmp_path))) as client:
        session, auth = new_session(client)
        reply = message(client, session, auth, "Số điện thoại của tôi là 0900000002")
        assert reply["identity_status"] == "verified" and reply["user_id"] == "0900000002"
        assert "4 tỷ" in reply["reply"] and "An" in reply["reply"]
        assert message(client, session, auth, "Số điện thoại của tôi là 0900000002") == reply
        rows = client.app.state.harness.db.all("SELECT * FROM tool_calls WHERE tool='verify_identity'")
        assert len(rows) == 1 and "0900000002" not in rows[0]["args"]
        trace = client.app.state.harness.db.one("SELECT data FROM traces WHERE session_id=?", (session["session_id"],))
        assert json.loads(trace["data"])["tool_calls"][0]["tool"] == "verify_identity"
        assert len(provider.requests) == 2


def test_phone_and_loan_in_one_turn_continue_through_real_tools(tmp_path, monkeypatch):
    def quote(payload):
        assert context(payload)["brief"]["user_id"] == "0900000003"
        return calls(
            ("suit", "check_suitability", {"product": "home_loan", "income": 45000000, "debt": 0,
                                          "amount": 2000000000, "term": 240, "property_value": 3000000000}),
            ("rates", "get_rate", {"product": "home_loan", "amount": 2000000000, "term": 240, "segment": "mass"}),
            ("promos", "get_active_promotions", {"product": "home_loan", "channel": "web", "as_of": "2000-01-01"}),
            ("disclosures", "get_disclosures", {"product": "home_loan", "topics": ["rate", "promo", "installment"]}),
        )

    def calculate(payload):
        facts = list(context(payload)["facts"].values())
        standard = next(fact for fact in facts if fact["key"] == "standard_rate")
        return calls(("calc", "calc_installment", {
            "amount": 2000000000, "term": 240, "rate": standard["value"], "rate_fact_id": standard["id"],
        }))

    def finish(payload):
        facts = list(context(payload)["facts"].values())
        rate = next(fact for fact in facts if fact["key"] == "standard_rate")
        return answer(f"Lãi suất tham khảo {{fact:{rate['id']}}}. Đây là minh họa, chưa phải phê duyệt.")

    provider = Provider([calls(("id", "verify_identity", {"phone": "+84900000003"})), quote, calculate, finish])
    provider.install(monkeypatch)
    with TestClient(create_app(settings(tmp_path))) as client:
        session, auth = new_session(client)
        reply = message(client, session, auth,
                        "Số điện thoại của tôi là 0900000003, tôi xác nhận nhu cầu mới vay mua nhà 2 tỷ trong 20 năm, "
                        "thu nhập 45 triệu, không có nợ, giá nhà 3 tỷ")
        assert reply["identity_status"] == "verified" and reply["used_fact_ids"]
        rows = client.app.state.harness.db.all("SELECT tool,result FROM tool_calls WHERE tool!='verify_identity'")
        assert {row["tool"] for row in rows} >= {"get_rate", "calc_installment", "get_disclosures", "check_suitability"}
        assert all(json.loads(row["result"])["as_of"] == "2026-10-31" for row in rows)
        assert all(not json.loads(row["result"])["error"] for row in rows)


@pytest.mark.parametrize("text,phone", [
    ("Số điện thoại của tôi là 0900000002 hoặc 0900000003", "0900000002"),
    ("Số điện thoại của vợ tôi là 0900000002", "0900000002"),
    ("Số điện thoại của tôi là 0909999999", "0909999999"),
    ("Tôi muốn tìm hiểu vay mua nhà", "0900000002"),
])
def test_model_cannot_identify_ambiguous_third_party_unknown_or_invented_phone(tmp_path, monkeypatch, text, phone):
    provider = Provider([calls(("id", "verify_identity", {"phone": phone})), answer("Anh/chị vui lòng xác nhận số của mình.")])
    provider.install(monkeypatch)
    with TestClient(create_app(settings(tmp_path))) as client:
        session, auth = new_session(client)
        reply = message(client, session, auth, text)
        assert reply["identity_status"] == "unverified" and reply["user_id"] is None
        assert not client.app.state.harness.db.all("SELECT * FROM callbacks")


def test_provider_cannot_bypass_catalog_or_supply_customer_identity(tmp_path, monkeypatch):
    provider = Provider([calls(("private", "crm_lookup", {"customer_id": "cus_002", "logged_in": True})), answer()])
    provider.install(monkeypatch)
    with TestClient(create_app(settings(tmp_path))) as client:
        session, auth = new_session(client)
        reply = message(client, session, auth, "Thông tin vay mua nhà công khai")
        assert reply["identity_status"] == "unverified"
        assert not client.app.state.harness.db.all("SELECT * FROM tool_calls WHERE tool='crm_lookup'")


def test_failure_after_callback_does_not_repeat_it_after_message_retry_or_restart(tmp_path, monkeypatch):
    provider = Provider([
        calls(("id", "verify_identity", {"phone": "0900000002"})),
        calls(("callback", "schedule_callback", {"time": "2026-11-02T09:00:00+07:00"})),
        httpx.Response(503), httpx.Response(503),
    ])
    provider.install(monkeypatch)
    config = settings(tmp_path)
    text = "Số điện thoại của tôi là 0900000002. Hẹn gọi lại sau 2 ngày lúc 09:00."
    with TestClient(create_app(config)) as client:
        session, auth = new_session(client)
        first = message(client, session, auth, text)
        assert first["reply"] == DEFAULT_REPLY
        assert message(client, session, auth, text) == first
        assert len(client.app.state.harness.db.all("SELECT * FROM callbacks")) == 1
    with TestClient(create_app(config)) as restored:
        assert message(restored, session, auth, text) == first
        assert len(restored.app.state.harness.db.all("SELECT * FROM callbacks")) == 1


def test_tool_transport_validates_before_failing_over(tmp_path, monkeypatch):
    models = []

    async def post(_client, _url, **kwargs):
        models.append(kwargs["json"]["model"])
        value = calls(("one", "verify_identity", {"phone": "0900000002"}))
        if len(models) == 1:
            value["message"]["tool_calls"][0]["function"]["arguments"] = "[1]"
        return httpx.Response(200, json={"choices": [value]})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    result = tool_completion(settings(tmp_path), [{"role": "user", "content": "JSON"}], [], deadline=time.monotonic() + 2)
    assert result["message"]["tool_calls"][0]["function"]["name"] == "verify_identity"
    assert models == ["primary", "backup"]
