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
    if "INPUT_SECURITY_CLASSIFIER" in instruction:
        value = {"category": "safe"}
    elif "BACKEND_CONTEXT_JSON:\n" in instruction:
        # Test the actual agent envelope without making a real provider request.
        # Identity is established explicitly by this integration fixture, not by this stub.
        context = json.loads(instruction.split("BACKEND_CONTEXT_JSON:\n", 1)[1])
        amount = next((f for f in context.get("facts", {}).values() if f.get("key") == "amount"), None)
        text = next(message["content"] for message in reversed(body["messages"]) if message["role"] == "user")
        if any(word in text.casefold() for word in ("người thật", "nhân viên thật")):
            value = {"draft": "Em sẽ chuyển hồ sơ cho nhân viên tiếp nhận.", "handoff": True}
        elif context.get("specialist_welcome"):
            value = {"draft": "Em đã tiếp nhận nội dung trao đổi" + (
                " về khoản vay {fact:" + amount["id"] + "}" if amount else " của anh/chị"
            ) + ". Anh/chị muốn làm rõ điểm nào?"}
        else:
            value = {"draft": (
                "Em đã ghi nhận khoản vay {fact:" + amount["id"] + "}. Anh/chị muốn làm rõ điểm nào?"
                if amount else "Em tiếp tục hỗ trợ nhu cầu của anh/chị."
            )}
    elif "Trích xuất memory" in instruction:
        data = json.loads(body["messages"][1]["content"])
        value = {"facts": []}
        if data["text"] == "Tôi vay mua nhà 2 tỷ trong 20 năm, thu nhập 45 triệu, không có nợ, giá nhà 3 tỷ":
            value = {"facts": [
                {"key": key, "value": item, "unit": unit, "operation": "replace", "source_text": data["text"]}
                for key, item, unit in [
                    ("product", "home_loan", ""), ("amount", "2000000000", "đồng"),
                    ("term", "240", "tháng"), ("income", "45000000", "đồng"),
                    ("debt", "0", "đồng"), ("property_value", "3000000000", "đồng"),
                ]
            ]}
    elif "Phân loại hội thoại" in instruction:
        value = {"stage": "quote", "intent": "advice", "objection": None}
    else:
        data = json.loads(body["messages"][1]["content"])
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
                          AI_SPECIALIST_ENABLED="false", APP_ENV="development",
                          OPENAI_API_KEY="", OPENAI_MODEL="", OPENAI_BACKUP_MODEL="",
                          TELEGRAM_BOT_TOKEN="", TELEGRAM_WEBHOOK_SECRET="", HARNESS_RELEASE_GATE="",
                          IDENTITY_HISTORY_ENABLED="false")
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
