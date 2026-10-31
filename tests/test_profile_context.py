"""Profile answers through the deployed phone entry, JSON and SSE routes."""

import json
import logging
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from harness.api import create_app
from harness.config import Settings
from harness.identity import profiles


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(
        db_path=str(tmp_path / "profiles.sqlite3"), llm_backend="rules",
        demo_phone_identity=True, telegram_secret="test-webhook", revision="profile-test",
    ))) as client:
        yield client


def enter(client, phone="0900000002", path="/demo/phone/session"):
    response = client.post(path, json={"phone": phone})
    assert response.status_code == 200, response.text
    return response.json()


def auth(session):
    return {"Authorization": "Bearer " + session["session_token"]}


def send(client, session, text="Tôi tên gì ?", key="name", stream=False):
    route = f"/sessions/{session['session_id']}/messages"
    if stream:
        route += "/stream"
    response = client.post(
        route,
        headers=auth(session),
        json={"text": text, "client_message_id": key},
    )
    assert response.status_code == 200, response.text
    if not stream:
        return response.json()
    events = []
    for block in response.text.split("\n\n"):
        if block.startswith("event:"):
            event, data = block.splitlines()[:2]
            events.append((event, json.loads(data[6:])))
    assert events[-1][0] == "event: result", events
    # No incorrect identity may escape in an intermediate snapshot.
    for event, data in events:
        if event == "event: text" and data["text"]:
            assert data["text"] in events[-1][1]["reply"]
    return events[-1][1]


def trace(client, session):
    db = client.app.state.harness.db
    row = db.one(
        "SELECT data FROM traces WHERE session_id=? ORDER BY rowid DESC LIMIT 1",
        (session["session_id"],),
    )
    return json.loads(row["data"])


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("path", ["/demo/phone/session", "/auth/phone/login"])
def test_phone_entry_name_is_sourced_and_retry_is_identical(client, stream, path, caplog):
    session = enter(client, path=path)
    with caplog.at_level(logging.INFO):
        result = send(client, session, stream=stream)
    assert "Trần Minh An" in result["reply"]
    assert send(client, session, stream=stream) == result
    evidence = trace(client, session)
    assert evidence["plan"]["intent"] == "profile_name"
    assert any(f["key"] == "profile_name" and f["value"] == "Trần Minh An" for f in evidence["facts"])
    assert evidence["source_spans"] and not evidence["tool_calls"]
    assert "profile-test" in caplog.text
    assert "0900000002" not in caplog.text and "Trần Minh An" not in caplog.text


@pytest.mark.parametrize("profile", profiles(), ids=lambda p: p["phone"])
@pytest.mark.parametrize("mode", ["harness", "no_memory", "full_history"])
def test_names_across_profiles_and_modes(client, profile, mode):
    session = client.post("/sessions", json={"demo_profile": profile["phone"], "mode": mode}).json()
    send(client, session, profile["phone"], "identify")
    assert profile["name"] in send(client, session)["reply"]


def test_unverified_session_does_not_inherit_profile_access(client):
    enter(client)
    session = client.post("/sessions", json={"demo_profile": "minh"}).json()
    result = send(client, session)
    assert "nhận diện" in result["reply"]
    assert "Trần Minh An" not in json.dumps(trace(client, session), ensure_ascii=False)


@pytest.mark.parametrize("question", [
    "Tên tôi là gì?", "Bạn còn nhớ tôi tên gì không?", "toi ten gi?", "Tôi là ai?",
])
def test_name_question_variants(client, question):
    assert "Trần Minh An" in send(client, enter(client), question)["reply"]


def test_mixed_name_and_loan_request_answers_both(client):
    session = enter(client)
    result = send(client, session, "Tôi tên gì, và muốn vay mua nhà?")
    assert "Trần Minh An" in result["reply"] and "số tiền muốn vay" in result["reply"]
    assert trace(client, session)["plan"]["intent"] == "advice"


def test_other_person_and_nickname_do_not_replace_profile(client):
    session = enter(client)
    assert "Trần Minh An" not in send(client, session, "Vợ tôi tên gì?", "family")["reply"]
    send(client, session, "Gọi tôi là Nam nhé", "nickname")
    assert "Trần Minh An" in send(client, session)["reply"]


def test_unknown_phone_has_missing_name_not_a_fake_identity(client):
    result = send(client, enter(client, "0901234567"))
    assert "chưa có tên" in result["reply"]
    assert "Khách demo" not in result["reply"]


@pytest.mark.parametrize("phone", ["0900000002", "0901234567"])
def test_profile_lookup_error_is_distinct_from_missing_name(client, monkeypatch, phone):
    session = enter(client, phone)

    def unavailable(*args):
        raise sqlite3.OperationalError("private database details")

    monkeypatch.setattr(client.app.state.harness.engine.memory, "profile", unavailable)
    result = send(client, session, stream=True)
    assert "tạm thời" in result["reply"] and "private database details" not in result["reply"]


@pytest.mark.parametrize("stream", [False, True])
def test_name_lookup_does_not_depend_on_memory_or_planner(client, monkeypatch, stream):
    session = enter(client)

    def unexpected(*args, **kwargs):
        pytest.fail("A direct profile lookup must not depend on memory extraction or the LLM planner")

    monkeypatch.setattr(client.app.state.harness.engine.memory, "flush", unexpected)
    monkeypatch.setattr("harness.agent.engine.json_completion", unexpected)
    assert "Trần Minh An" in send(client, session, stream=stream)["reply"]


def telegram(client, number, text, target=111, name="Tên Telegram"):
    result = client.post(
        "/webhooks/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-webhook"},
        json={
            "update_id": number,
            "message": {
                "date": 1000 + number,
                "chat": {"id": target, "type": "private"},
                "from": {"id": target, "first_name": name},
                "text": text,
            },
        },
    )
    assert result.status_code == 200
    db = client.app.state.harness.db
    return db.one("SELECT text FROM outbox WHERE target=? ORDER BY rowid DESC LIMIT 1", (str(target),))["text"]


def test_display_name_is_labelled_until_demo_profile_is_selected(client):
    assert "Tên hiển thị Telegram" in telegram(client, 1, "Tôi tên gì?")
    telegram(client, 2, "0900000002")
    reply = telegram(client, 3, "Tôi tên gì?")
    assert "Trần Minh An" in reply and "hồ sơ demo" in reply
    assert "Tên Telegram" not in reply
    session = enter(client)
    assert "Trần Minh An" in send(client, session)["reply"]
    sibling = client.post(f"/sessions/{session['session_id']}/new", headers=auth(session)).json()
    assert "Trần Minh An" in send(client, sibling)["reply"]


def test_other_phone_cannot_change_an_existing_conversations_name(client):
    session = enter(client)
    send(client, session)
    response = client.post(
        f"/sessions/{session['session_id']}/messages",
        headers=auth(session),
        json={"text": "0900000001", "client_message_id": "other-phone"},
    )
    assert response.status_code == 409
    assert "Trần Minh An" in send(client, session, key="still-me")["reply"]
    assert "Nguyễn Thị Hoa" in send(client, enter(client, "0900000001"))["reply"]


def test_model_recognized_name_uses_authorized_fact_without_draft_rewrite(client, monkeypatch):
    session = enter(client)

    def classify(settings, instruction, data, *, purpose, **kwargs):
        assert purpose == "plan"
        return {"intent": "profile_name", "stage": "discover"}

    monkeypatch.setattr("harness.agent.engine.json_completion", classify)
    result = send(client, session, "Bạn xưng hô với mình thế nào nhỉ?")
    assert "Trần Minh An" in result["reply"]
    context = trace(client, session)
    assert "0900000002" not in json.dumps(context["context_parts"])
    assert not context["tool_calls"]


def test_wrong_model_intent_cannot_answer_family_question_with_own_name(client, monkeypatch):
    session = enter(client)

    def completion(settings, instruction, data, *, purpose, **kwargs):
        if purpose == "plan":
            return {"intent": "profile_name", "stage": "discover"}
        return {"draft": data["rules_draft"]}

    monkeypatch.setattr("harness.agent.engine.json_completion", completion)
    assert "Trần Minh An" not in send(client, session, "Vợ tôi tên gì?")["reply"]


@pytest.mark.parametrize("category", ["safe", "prompt_injection"])
def test_openai_input_guard_still_runs_for_direct_profile_questions(client, monkeypatch, category):
    session = enter(client)
    settings = client.app.state.harness.settings
    settings.llm_backend = "openai"
    settings.openai_key, settings.openai_model, settings.openai_backup_model = "test-only", "primary", "backup"
    calls = []

    def classify(settings, instruction, data, *, purpose, **kwargs):
        calls.append(purpose)
        return {"category": category}

    async def answer_profile(_client, url, **kwargs):
        calls.append("agent_tools")
        instruction = kwargs["json"]["messages"][0]["content"]
        context = json.loads(instruction.split("BACKEND_CONTEXT_JSON:\n", 1)[1])
        fact = next(fact for fact in context["facts"].values() if fact["key"] == "profile_name")
        assert fact["value"] == "Trần Minh An"
        assert all(part["type"] == "customer_profile" for part in context["context_parts"])
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"draft": f"Tên trong hồ sơ của anh/chị là {{fact:{fact['id']}}}."}),
        }}]}, request=httpx.Request("POST", url))

    # Input screening precedes the model's answer, even for authorized profile facts.
    monkeypatch.setattr("harness.input_guard.json_completion", classify)
    monkeypatch.setattr(httpx.AsyncClient, "post", answer_profile)
    result = send(client, session)
    assert ("Trần Minh An" in result["reply"]) is (category == "safe")
    assert calls == (["input_guard", "agent_tools"] if category == "safe" else ["input_guard"])
