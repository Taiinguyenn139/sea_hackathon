"""Optional JSON LLM boundary; deterministic callers validate every returned field."""

import json
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bank_tools.service import canonical


class PlanSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: Literal["discover", "quote", "objection", "followup", "apply"]
    objection: Literal["rate_high", "need_spouse"] | None = None


class ExtractionSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spans: list[str] = Field(max_length=20)


class DraftSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft: str = Field(min_length=1, max_length=7999)


def json_completion(settings, instruction, data, schema=None):
    if not settings or settings.llm_backend != "openai" or not settings.openai_key or not settings.openai_model:
        return None
    try:
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": "Bearer " + settings.openai_key},
            json={
                "model": settings.openai_model,
                "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": canonical(data)}],
                "response_format": {"type": "json_object"},
            },
            timeout=20,
        )
        response.raise_for_status()
        result = json.loads(response.json()["choices"][0]["message"]["content"])
        if not isinstance(result, dict):
            return None
        return schema.model_validate(result).model_dump() if schema else result
    except (httpx.HTTPError, KeyError, ValueError, IndexError, TypeError, ValidationError):
        return None
