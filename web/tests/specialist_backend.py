"""Isolated real API for browser tests; only the external LLM transport is stubbed."""

import json
import os
import sys
import tempfile
from pathlib import Path

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def provider(request):
    body = json.loads(request.content)
    instruction = body["messages"][0]["content"]
    data = json.loads(body["messages"][1]["content"])
    if "INPUT_SECURITY_CLASSIFIER" in instruction:
        value = {"category": "safe"}
    elif "Trích các đoạn" in instruction:
        value = {"spans": []}
    elif "Phân loại hội thoại" in instruction:
        value = {"stage": "quote", "intent": "advice", "objection": None}
    else:
        value = {"draft": data["rules_draft"]}
    if body.get("stream"):
        chunks = [
            {"choices": [{"delta": {"content": json.dumps(value, ensure_ascii=False)}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
        raw = "".join("data: " + json.dumps(c, ensure_ascii=False) + "\n\n" for c in chunks)
        return httpx.Response(200, content=(raw + "data: [DONE]\n\n").encode())
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(value)}, "finish_reason": "stop"}]})


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="bankwise-specialist-e2e-") as folder:
        os.environ.update(HARNESS_DB=str(Path(folder) / "test.sqlite3"), LLM_BACKEND="rules",
                          AI_SPECIALIST_ENABLED="false", APP_ENV="development")
        from harness.api import create_app
        from harness.config import Settings

        original = httpx.AsyncClient
        httpx.AsyncClient = lambda: original(transport=httpx.MockTransport(provider))
        settings = Settings(
            db_path=os.environ["HARNESS_DB"], llm_backend="openai", ai_specialist_enabled=True,
            openai_key="test-only", openai_model="test-primary", openai_backup_model="test-backup",
            staff_password="specialist-e2e-staff", admin_token="specialist-e2e-admin",
            telegram_token="", telegram_secret="", cookie_secure=False, release_gate="",
        )
        uvicorn.run(create_app(settings), host="127.0.0.1", port=int(sys.argv[1]), log_level="error")
