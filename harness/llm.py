"""Optional JSON LLM boundary; deterministic callers validate every returned field."""

import asyncio
import json
import logging
import time
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bank_tools.service import canonical

DEFAULT_REPLY = "Hiện tại hệ thống tư vấn đang tạm gián đoạn. Anh/chị vui lòng thử lại sau ít phút."
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())


class LLMUnavailable(RuntimeError):
    """Sanitized exhaustion signal; callers must not regenerate advice using rules."""

    def __init__(self, category="unavailable"):
        super().__init__(DEFAULT_REPLY)
        self.category = category


class PlanSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: Literal["discover", "quote", "objection", "followup", "apply"]
    objection: Literal["rate_high", "need_spouse"] | None = None
    intent: Literal["advice", "greeting", "loan_catalog", "product_catalog", "acknowledgement"] = "advice"


class ExtractionSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spans: list[str] = Field(max_length=20)


class DraftSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft: str = Field(min_length=1, max_length=7999)


def json_completion(settings, instruction, data, schema=None, *, purpose):
    if not settings or settings.llm_backend != "openai":
        return None
    return asyncio.run(_completion(settings, instruction, data, schema, purpose))


def _record(purpose, model, attempt, status, started, category=None, usage=None):
    metadata = {
        "purpose": purpose,
        "model": model,
        "attempt": attempt,
        "status": status,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
    if category:
        metadata["category"] = category
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


async def _completion(settings, instruction, data, schema, purpose):
    started = time.monotonic()
    deadline = started + settings.llm_call_budget_seconds
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
                    response = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": "Bearer " + settings.openai_key},
                        json={
                            "model": model,
                            "messages": [
                                {"role": "system", "content": instruction},
                                {"role": "user", "content": canonical(data)},
                            ],
                            "response_format": {"type": "json_object"},
                        },
                        timeout=limit,
                    )
                    if not response.is_success:
                        category, retryable = _http_failure(response)
                        if response.status_code in (429, 503):
                            try:
                                delay = max(0.0, float(response.headers.get("retry-after", "0")))
                            except ValueError:
                                delay = 0.0
                    else:
                        body = response.json()
                        choice = body["choices"][0]
                        if choice.get("finish_reason", "stop") != "stop":
                            raise ValueError("Incomplete completion")
                        result = json.loads(choice["message"]["content"])
                        if not isinstance(result, dict):
                            raise ValueError("JSON object required")
                        result = schema.model_validate(result).model_dump() if schema else result
                        _record(purpose, model, attempt, "success", attempt_started, usage=body.get("usage"))
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
            if delay:
                await asyncio.sleep(delay)
    _record(purpose, models[-1], "none", "exhausted", started, category)
    raise LLMUnavailable(category)
