"""Model-specific transports retain the same validated agent-call contract."""

import time

import httpx
import pytest

from harness.config import Settings
from harness.identity import IDENTITY_TOOL_DEFINITION
from harness.llm import tool_completion

LUNA = "gpt-6-luna"
SOL = "gpt-6.1-sol"
MESSAGES = [{"role": "system", "content": "Return JSON or call the supplied function."},
            {"role": "user", "content": "Số điện thoại của tôi là 0900000002."}]


def settings(primary=LUNA, backup=SOL):
    return Settings(llm_backend="openai", openai_key="test-private-key", openai_model=primary,
                    openai_backup_model=backup, llm_call_budget_seconds=1)


def chat_call():
    return {"id": "call_owned", "type": "function", "function": {
        "name": "verify_identity", "arguments": '{"phone":"0900000002"}',
    }}


def response_call():
    call = chat_call()
    return {"type": "function_call", "id": "fc_owned", "call_id": call["id"], "status": "completed",
            **call["function"]}


def final_output():
    return {"type": "message", "role": "assistant", "status": "completed", "content": [
        {"type": "output_text", "text": '{"draft":"Anh/chị muốn tiếp tục nhu cầu nào?","handoff":false}'},
    ]}


def respond(output, status="completed"):
    return httpx.Response(200, json={"status": status, "output": output,
                                     "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}})


@pytest.mark.parametrize("offered", [[IDENTITY_TOOL_DEFINITION], []])
def test_luna_chat_agent_disables_reasoning_even_for_no_tools_welcome(monkeypatch, offered):
    async def post(_client, url, **kwargs):
        payload = kwargs["json"]
        assert url.endswith("/chat/completions")
        assert payload["reasoning_effort"] == "none"
        assert bool(payload.get("tools")) == bool(offered)
        choice = ({"finish_reason": "tool_calls", "message": {"tool_calls": [chat_call()]}} if offered else {
            "finish_reason": "stop", "message": {"content": '{"draft":"Xin chào."}'},
        })
        return httpx.Response(200, json={"choices": [choice]})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    result = tool_completion(settings(), MESSAGES, offered, deadline=time.monotonic() + 2)
    if offered:
        assert result["message"]["tool_calls"] == [chat_call()]
    else:
        assert result["draft"] == "Xin chào." and result["handoff"] is False


def test_backup_responses_replays_encrypted_reasoning_and_original_call_ids(monkeypatch, caplog):
    requests = []
    reasoning = {"type": "reasoning", "id": "rs_owned", "summary": [], "encrypted_content": "opaque-private-reasoning"}

    async def post(_client, url, **kwargs):
        payload = kwargs["json"]
        requests.append(payload)
        if payload["model"] == LUNA:
            assert url.endswith("/chat/completions")
            assert all("_response_items" not in message for message in payload["messages"])
            return httpx.Response(503)
        assert url.endswith("/responses")
        assert payload["store"] is False and payload["include"] == ["reasoning.encrypted_content"]
        assert "messages" not in payload and "response_format" not in payload
        assert payload["text"]["format"] == {"type": "json_object"}
        assert payload["tools"][0]["name"] == "verify_identity" and payload["tools"][0]["strict"] is False
        if len(requests) == 2:
            return respond([reasoning, response_call()])
        assert payload["input"][-3:] == [reasoning, response_call(), {
            "type": "function_call_output", "call_id": "call_owned", "output": '{"verified":true}',
        }]
        return respond([final_output()])

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    first = tool_completion(settings(), MESSAGES, [IDENTITY_TOOL_DEFINITION], deadline=time.monotonic() + 2)
    assert first["message"]["tool_calls"] == [chat_call()]
    conversation = [*MESSAGES, first["message"], {"role": "tool", "tool_call_id": "call_owned", "content": '{"verified":true}'}]
    final = tool_completion(settings(), conversation, [IDENTITY_TOOL_DEFINITION], deadline=time.monotonic() + 2)
    assert final["handoff"] is False and final["draft"] == "Anh/chị muốn tiếp tục nhu cầu nào?"
    assert [payload["model"] for payload in requests] == [LUNA, SOL, LUNA, SOL]
    assert '"completion_tokens":5' in caplog.text and '"prompt_tokens":10' in caplog.text
    assert "opaque-private-reasoning" not in caplog.text and "test-private-key" not in caplog.text


@pytest.mark.parametrize("invalid", ["incomplete", "invalid_arguments", "duplicate_ids", "unfinished_item", "refusal"])
def test_invalid_responses_never_escape_validation_and_can_fail_over(monkeypatch, invalid):
    requests = []

    async def post(_client, url, **kwargs):
        requests.append((url, kwargs["json"]["model"]))
        if len(requests) == 2:
            assert url.endswith("/chat/completions") and kwargs["json"]["reasoning_effort"] == "none"
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"draft":"Xin chào."}'}}]})
        call = response_call()
        if invalid == "invalid_arguments":
            call["arguments"] = "[]"
        if invalid == "unfinished_item":
            call["status"] = "in_progress"
        if invalid == "refusal":
            return respond([{"type": "message", "content": [{"type": "refusal", "refusal": "private refusal"}]}])
        return respond([call, call] if invalid == "duplicate_ids" else [call], status="incomplete" if invalid == "incomplete" else "completed")

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    result = tool_completion(settings(SOL, LUNA), MESSAGES, [IDENTITY_TOOL_DEFINITION], deadline=time.monotonic() + 2)
    assert result["draft"] == "Xin chào." and [model for _, model in requests] == [SOL, LUNA]


def test_responses_without_offered_tools_still_validates_final_json(monkeypatch):
    async def post(_client, url, **kwargs):
        assert url.endswith("/responses")
        assert "tools" not in kwargs["json"] and "tool_choice" not in kwargs["json"]
        return respond([final_output()])

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    result = tool_completion(settings(SOL, LUNA), MESSAGES, [], deadline=time.monotonic() + 2)
    assert result["handoff"] is False
