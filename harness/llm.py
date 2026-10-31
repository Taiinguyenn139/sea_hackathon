"""Optional JSON LLM boundary; deterministic callers validate every returned field."""

import asyncio
import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import from_json

from bank_tools.service import canonical

DEFAULT_REPLY = "Hiện tại hệ thống tư vấn đang tạm gián đoạn. Anh/chị vui lòng thử lại sau ít phút."
logger = logging.getLogger(__name__)
_turn_deadline: ContextVar[float | None] = ContextVar("llm_turn_deadline", default=None)
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())


class LLMUnavailable(RuntimeError):
    """Sanitized exhaustion signal; callers must not regenerate advice using rules."""

    def __init__(self, category="unavailable"):
        super().__init__(DEFAULT_REPLY)
        self.category = category


def current_deadline():
    return _turn_deadline.get()


@contextmanager
def completion_budget(seconds):
    """Share one deadline across classifier, memory, agent and handoff calls."""
    deadline = time.monotonic() + seconds
    outer = current_deadline()
    token = _turn_deadline.set(min(outer, deadline) if outer is not None else deadline)
    try:
        yield
    finally:
        _turn_deadline.reset(token)


class PlanSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: Literal["discover", "quote", "objection", "followup", "apply"]
    objection: Literal["rate_high", "need_spouse"] | None = None
    intent: Literal["advice", "greeting", "loan_catalog", "product_catalog", "acknowledgement", "profile_name", "needs_recall", "memory_recall"] = "advice"
    recall_keys: list[Literal["product", "amount", "income", "debt", "property_value", "term", "objection", "commitment", "loan_status"]] = Field(default_factory=list, max_length=9)


class MemoryFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: Literal["product", "amount", "income", "debt", "property_value", "term", "objection", "commitment", "loan_status"]
    value: str = Field(min_length=1, max_length=1000)
    unit: Literal["đồng", "tháng", ""]
    operation: Literal["replace", "conflict"]
    source_text: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def storage_types(self):
        # Validate the storage contract only; the LLM owns interpretation and resolution.
        if self.key in ("amount", "income", "debt", "property_value", "term"):
            try:
                number = int(self.value)
            except ValueError:
                raise ValueError("Numeric memory requires an integer string") from None
            if number < 0 or str(number) != self.value:
                raise ValueError("Numeric memory requires a canonical nonnegative integer")
            if self.unit != ("tháng" if self.key == "term" else "đồng"):
                raise ValueError("Invalid numeric unit")
        elif self.unit:
            raise ValueError("Text memory has no unit")
        if self.key == "product" and self.value not in ("home_loan", "personal_loan", "credit_card"):
            raise ValueError("Unsupported product")
        return self


class ExtractionSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    facts: list[MemoryFact] = Field(max_length=9)

    @model_validator(mode="after")
    def unique_fields(self):
        if len({fact.key for fact in self.facts}) != len(self.facts):
            raise ValueError("Duplicate memory key")
        return self


class DraftSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft: str = Field(min_length=1, max_length=7999)


class AgentReply(DraftSuggestion):
    handoff: bool = False


class ToolFunction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    arguments: str = Field(max_length=32000)


class FunctionCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=200)
    type: Literal["function"]
    function: ToolFunction


def _agent_result(choice):
    """Validate provider messages before any proposed function can execute."""
    message = choice["message"]
    if choice.get("finish_reason") == "tool_calls":
        calls = [FunctionCall.model_validate(call).model_dump() for call in message["tool_calls"]]
        if not calls or len(calls) > 12 or len({call["id"] for call in calls}) != len(calls):
            raise ValueError("Invalid tool batch")
        for call in calls:
            arguments = json.loads(call["function"]["arguments"])
            if not isinstance(arguments, dict):
                raise TypeError("Tool arguments must be an object")
        return {"message": {"role": "assistant", "content": None, "tool_calls": calls}}
    if choice.get("finish_reason", "stop") != "stop" or message.get("tool_calls"):
        raise ValueError("Incomplete agent response")
    reply = AgentReply.model_validate_json(message["content"]).model_dump()
    return {"message": {"role": "assistant", "content": canonical(reply)}, **reply}


def _model_family(model, families):
    return any(model == family or model.startswith(family + "-") for family in families)


def _responses_input(messages, model):
    items = []
    for message in messages:
        replay = message.get("_response_items", {})
        if message["role"] == "assistant" and replay.get("model") == model:
            # Keep encrypted reasoning and its matching function-call items
            # together when the same reasoning model resumes after a tool.
            items.extend(replay["items"])
        elif message["role"] == "tool":
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                          "output": message["content"]})
        else:
            if message.get("content") is not None:
                items.append({"role": message["role"], "content": message["content"]})
            for call in message.get("tool_calls", []):
                items.append({"type": "function_call", "call_id": call["id"],
                              "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
    return items


def _responses_result(body, model):
    if not isinstance(body, dict) or body.get("status") != "completed" or body.get("error") or body.get("incomplete_details"):
        raise ValueError("Incomplete agent response")
    output = body["output"]
    if not isinstance(output, list) or any(not isinstance(item, dict) or item.get("type") not in ("reasoning", "function_call", "message") for item in output):
        raise ValueError("Unsupported agent response item")
    if any(item["type"] != "reasoning" and item.get("status", "completed") != "completed" for item in output):
        raise ValueError("Incomplete agent response item")
    calls = [{"id": item["call_id"], "type": "function",
              "function": {"name": item["name"], "arguments": item["arguments"]}}
             for item in output if item["type"] == "function_call"]
    if calls:
        result = _agent_result({"finish_reason": "tool_calls", "message": {"tool_calls": calls}})
        result["message"]["_response_items"] = {"model": model, "items": output}
        return result
    content = []
    for item in output:
        if item["type"] == "message":
            for part in item["content"]:
                if part["type"] != "output_text":
                    raise ValueError("Agent did not return text")
                content.append(part["text"])
    return _agent_result({"finish_reason": "stop", "message": {"content": "".join(content)}})


def tool_completion(settings, messages, tools, *, deadline):
    if settings.llm_backend != "openai":
        raise LLMUnavailable("agent_requires_openai")
    return asyncio.run(_completion(
        settings, None, None, None, "agent_tools", messages=messages, tools=tools, turn_deadline=deadline,
    ))


def json_completion(settings, instruction, data, schema=None, *, purpose, on_delta=None):
    if not settings or settings.llm_backend != "openai":
        return None
    return asyncio.run(_completion(settings, instruction, data, schema, purpose, on_delta=on_delta))


def _record(purpose, model, attempt, status, started, category=None, usage=None, streamed=False):
    metadata = {
        "purpose": purpose,
        "model": model,
        "attempt": attempt,
        "status": status,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
    if category:
        metadata["category"] = category
    if streamed:
        metadata["streamed"] = True
    if isinstance(usage, dict):
        metadata["usage"] = {
            k: v for k, v in usage.items()
            if k in ("prompt_tokens", "completion_tokens", "total_tokens") and isinstance(v, int)
        }
    logger.info("llm_completion %s", canonical(metadata))


def _http_failure(response):
    try:
        code = response.json().get("error", {}).get("code")
    except (ValueError, AttributeError):
        code = None
    billing = {
        "insufficient_quota", "credit_balance_exhausted", "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded", "organization_usage_limit_exceeded", "billing_hard_limit_reached",
    }
    if response.status_code == 401 or code in billing:
        return "authentication_or_quota", False
    return "http_error", response.status_code in (403, 404, 408, 429) or response.status_code >= 500


async def _read_stream(response, on_delta):
    raw = ""
    finish = None
    done = False
    usage = None
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            done = True
            break
        if not data:
            continue
        chunk = json.loads(data)
        if chunk.get("usage"):
            usage = chunk["usage"]
        choices = chunk.get("choices", [])
        if not choices:
            continue
        choice = choices[0]
        finish = choice.get("finish_reason") or finish
        delta = choice.get("delta", {}).get("content") or ""
        if not isinstance(delta, str):
            raise TypeError("Invalid streamed delta")
        raw += delta
        if len(raw) > 64000:
            raise ValueError("Streamed JSON exceeds size limit")
        try:
            partial = from_json(raw, allow_partial="trailing-strings")
        except ValueError:
            continue
        if isinstance(partial, dict) and isinstance(partial.get("draft"), str) and partial["draft"]:
            on_delta(partial["draft"])
    if not done or finish != "stop":
        raise ValueError("Incomplete streamed completion")
    return {"choices": [{"message": {"content": raw}, "finish_reason": finish}], "usage": usage}


async def _completion(
    settings, instruction, data, schema, purpose, messages=None, tools=None, turn_deadline=None, on_delta=None,
):
    started = time.monotonic()
    deadline = started + settings.llm_call_budget_seconds
    shared = current_deadline()
    if shared is not None:
        deadline = min(deadline, shared)
    if turn_deadline is not None:
        deadline = min(deadline, turn_deadline)
    models = (settings.openai_model, settings.openai_backup_model)
    category = "deadline"
    async with httpx.AsyncClient() as client:
        for index, model in enumerate(models):
            attempt = "primary" if index == 0 else "backup"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            retryable = True
            delay = 0.0
            attempt_started = time.monotonic()
            # Reserve half the remaining budget for backup. asyncio.timeout cancels
            # the actual async I/O, unlike a per-read socket timeout or orphaned thread.
            limit = remaining / (2 - index)
            try:
                async with asyncio.timeout(limit):
                    responses = tools is not None and _model_family(model, ("gpt-6-astra", "gpt-6.1-sol", "gpt-6.1-terra"))
                    payload: dict = {
                        "model": model,
                        "messages": [
                            {key: value for key, value in message.items() if not key.startswith("_")}
                            for message in messages
                        ] if messages is not None else [
                            {"role": "system", "content": instruction}, {"role": "user", "content": canonical(data)},
                        ],
                        "response_format": {"type": "json_object"},
                    }
                    endpoint = "https://api.openai.com/v1/chat/completions"
                    if responses:
                        endpoint = "https://api.openai.com/v1/responses"
                        payload = {
                            "model": model, "input": _responses_input(messages, model), "store": False,
                            "include": ["reasoning.encrypted_content"], "reasoning": {"effort": "low"},
                            "text": {"format": {"type": "json_object"}},
                        }
                    elif tools is not None and _model_family(model, ("gpt-6-luna", "gpt-6-sol")):
                        payload["reasoning_effort"] = "none"
                    if tools:
                        native_tools = ([{"type": "function", **tool, "strict": False} for tool in tools] if responses
                                        else [{"type": "function", "function": tool} for tool in tools])
                        payload.update(tools=native_tools, tool_choice="auto")
                    headers = {"Authorization": "Bearer " + settings.openai_key}
                    if on_delta:
                        payload.update(stream=True, stream_options={"include_usage": True})
                        async with client.stream(
                            "POST", endpoint,
                            headers=headers, json=payload, timeout=limit,
                        ) as response:
                            if response.is_success:
                                body = await _read_stream(response, on_delta)
                            else:
                                await response.aread()
                    else:
                        response = await client.post(
                            endpoint,
                            headers=headers, json=payload, timeout=limit,
                        )
                        body = response.json() if response.is_success else None
                    if not response.is_success:
                        category, retryable = _http_failure(response)
                        if response.status_code in (429, 503):
                            try:
                                delay = max(0.0, float(response.headers.get("retry-after", "0")))
                            except ValueError:
                                delay = 0.0
                    else:
                        usage = body.get("usage")
                        if responses:
                            result = _responses_result(body, model)
                            if isinstance(usage, dict):
                                usage = {"prompt_tokens": usage.get("input_tokens"),
                                         "completion_tokens": usage.get("output_tokens"), "total_tokens": usage.get("total_tokens")}
                        elif tools is not None:
                            choice = body["choices"][0]
                            result = _agent_result(choice)
                        else:
                            choice = body["choices"][0]
                            if choice.get("finish_reason", "stop") != "stop":
                                raise ValueError("Incomplete completion")
                            result = json.loads(choice["message"]["content"])
                            if not isinstance(result, dict):
                                raise ValueError("JSON object required")
                            result = schema.model_validate(result).model_dump() if schema else result
                        _record(
                            purpose, model, attempt, "success", attempt_started,
                            usage=usage, streamed=on_delta is not None,
                        )
                        return result
            except (TimeoutError, httpx.TimeoutException):
                category = "timeout"
            except httpx.HTTPError:
                category = "connection_error"
            except ValidationError:
                category = "schema_error"
            except (KeyError, ValueError, IndexError, TypeError):
                category = "invalid_response"
            _record(purpose, model, attempt, "error", attempt_started, category)
            if not retryable or index == 1 or delay >= deadline - time.monotonic():
                break
            _record(purpose, model, attempt, "failover", started, category)
            if on_delta:
                on_delta("")
            if delay:
                await asyncio.sleep(delay)
    _record(purpose, models[-1], "none", "exhausted", started, category)
    raise LLMUnavailable(category)
