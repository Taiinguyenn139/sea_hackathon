import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from harness.api import create_app
from harness.config import Settings
from harness.db import Database
from harness.llm import DEFAULT_REPLY, LLMUnavailable, PlanSuggestion, json_completion
from harness.memory.service import Memory


def configured(**kwargs):
    return Settings(
        llm_backend="openai", openai_key="test-only-secret", openai_model="primary",
        openai_backup_model="backup", **kwargs,
    )


def result(value):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(value)}, "finish_reason": "stop"}],
        "usage": {"total_tokens": 10},
    })


def complete(settings):
    return json_completion(settings, "Return JSON", {"private": "customer-input"}, PlanSuggestion, purpose="plan")


@pytest.mark.parametrize("missing", ["openai_key", "openai_model", "openai_backup_model"])
def test_explicit_openai_requires_config(missing):
    values = {"llm_backend": "openai", "openai_key": "secret", "openai_model": "a", "openai_backup_model": "b"}
    values[missing] = ""
    with pytest.raises(ValueError) as error:
        Settings(**values)
    assert "secret" not in str(error.value)


def test_distinct_models_and_finite_budget():
    with pytest.raises(ValueError):
        Settings(llm_backend="openai", openai_key="secret", openai_model="a", openai_backup_model="a")
    for budget in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            configured(llm_call_budget_seconds=budget)
    assert "test-only-secret" not in repr(configured())


def test_primary_success_never_calls_backup(monkeypatch, caplog):
    calls = []

    async def post(self, url, **kwargs):
        calls.append(kwargs["json"]["model"])
        return result({"stage": "quote", "objection": None})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    assert complete(configured())["stage"] == "quote"
    assert calls == ["primary"]
    assert '"status":"success"' in caplog.text
    assert "test-only-secret" not in caplog.text and "customer-input" not in caplog.text


@pytest.mark.parametrize("failure", ["timeout", "connection", "http", "schema", "json", "truncated"])
def test_backup_after_primary_failure(monkeypatch, failure):
    calls = []

    async def post(self, url, **kwargs):
        calls.append(kwargs["json"]["model"])
        if len(calls) == 1:
            if failure == "timeout":
                raise httpx.ReadTimeout("private provider error")
            if failure == "connection":
                raise httpx.ConnectError("private provider error")
            if failure == "http":
                return httpx.Response(503)
            if failure == "schema":
                return result({"stage": "not-a-stage"})
            if failure == "json":
                return httpx.Response(200, json={"choices": [{"message": {"content": "bad json"}}]})
            return httpx.Response(200, json={"choices": [{"finish_reason": "length"}]})
        return result({"stage": "quote", "objection": None})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    assert complete(configured())["stage"] == "quote"
    assert calls == ["primary", "backup"]


@pytest.mark.parametrize("status,code,attempts", [
    (401, "invalid_api_key", 1), (429, "insufficient_quota", 1),
    (429, "rate_limit_exceeded", 2), (500, None, 2),
])
def test_exhaustion_and_nonretryable_account_errors(monkeypatch, caplog, status, code, attempts):
    calls = []

    async def post(self, url, **kwargs):
        calls.append(kwargs["json"]["model"])
        return httpx.Response(status, json={"error": {"code": code, "message": "private-error"}})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    with pytest.raises(LLMUnavailable, match="tạm gián đoạn"):
        complete(configured())
    assert len(calls) == attempts
    assert '"status":"exhausted"' in caplog.text
    assert "private-error" not in caplog.text


def test_retry_after_exceeding_budget_does_not_wait(monkeypatch):
    calls = []

    async def post(self, url, **kwargs):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "60"})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    started = time.monotonic()
    with pytest.raises(LLMUnavailable):
        complete(configured(llm_call_budget_seconds=0.3))
    assert time.monotonic() - started < 1
    assert len(calls) == 1


def test_deadline_cancels_both_attempts(monkeypatch):
    cancelled = []

    async def post(self, url, **kwargs):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(kwargs["json"]["model"])

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    started = time.monotonic()
    with pytest.raises(LLMUnavailable):
        complete(configured(llm_call_budget_seconds=0.3))
    assert time.monotonic() - started < 1
    assert cancelled == ["primary", "backup"]


def test_memory_network_does_not_hold_database_lock(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "memory.sqlite3"))
    memory = Memory(db, configured())
    with db.transaction() as conn:
        memory.enqueue(conn, "turn-a", "hoa", "session-a", "web", "Thu nhập 45 triệu")
    entered = threading.Event()
    release = threading.Event()

    async def post(self, url, **kwargs):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.005)
        return result({"spans": ["Thu nhập 45 triệu", "thu nhập 999 triệu"]})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    with ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(memory.flush, "hoa")
        assert entered.wait(2)
        try:
            other = pool.submit(db.one, "SELECT value FROM settings WHERE key='clock'")
            assert other.result(timeout=0.5)
            same_customer = pool.submit(memory.flush, "hoa")
            assert not same_customer.done()
        finally:
            release.set()
        worker.result(timeout=3)
        same_customer.result(timeout=3)
    assert db.one("SELECT processed_seq FROM customer_sequences WHERE customer_id='hoa'")["processed_seq"] == 1
    assert db.one("SELECT count(*) AS n FROM facts WHERE source_ref='turn-a'")["n"] == 1
    assert db.one("SELECT value FROM facts WHERE source_ref='turn-a'")["value"] == "45000000"


def test_memory_exhaustion_keeps_job_and_cooldown(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "retry.sqlite3"))
    memory = Memory(db, configured())
    with db.transaction() as conn:
        memory.enqueue(conn, "turn", "hoa", "session", "web", "Thu nhập 45 triệu")
    calls = []

    async def post(self, url, **kwargs):
        calls.append(1)
        return httpx.Response(503)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    for _ in range(2):
        with pytest.raises(LLMUnavailable):
            memory.flush("hoa")
    assert len(calls) == 2  # Cooldown prevents a hot loop across repeated flushes.
    assert db.one("SELECT status FROM memory_jobs")["status"] == "pending"
    assert db.one("SELECT processed_seq FROM customer_sequences")["processed_seq"] == 0
    memory.flush("hoa", allow_llm=False)
    assert db.one("SELECT status FROM memory_jobs")["status"] == "processed"


def test_memory_wait_for_active_claim_has_deadline(tmp_path):
    db = Database(str(tmp_path / "busy.sqlite3"))
    memory = Memory(db, configured(llm_call_budget_seconds=0.05))
    with db.transaction() as conn:
        memory.enqueue(conn, "turn", "hoa", "session", "web", "Thu nhập 45 triệu")
        conn.execute("UPDATE memory_jobs SET status='processing',lease_until=?", (time.time() + 30,))
    started = time.monotonic()
    with pytest.raises(LLMUnavailable):
        memory.flush("hoa")
    assert time.monotonic() - started < 0.5
    assert db.one("SELECT processed_seq FROM customer_sequences")["processed_seq"] == 0
    assert db.one("SELECT status FROM memory_jobs")["status"] == "processing"


def test_default_reply_preserves_tool_idempotency(tmp_path, monkeypatch):
    settings = Settings(db_path=str(tmp_path / "chat.sqlite3"), llm_backend="rules", admin_token="test-admin")
    app = create_app(settings)
    with TestClient(app) as client:
        session = client.post("/sessions", json={"demo_profile": "hoa", "mode": "harness"}).json()
        sid = session["session_id"]
        auth = {"Authorization": "Bearer " + session["session_token"]}
        assert client.post(f"/sessions/{sid}/verify", headers=auth, json={"dob": "1988-05-12"}).status_code == 200
        settings.llm_backend = "openai"
        settings.openai_key = "test-only"
        settings.openai_model = "primary"
        settings.openai_backup_model = "backup"

        async def post(self, url, **kwargs):
            if "Phân loại" in kwargs["json"]["messages"][0]["content"]:
                return result({"stage": "quote", "objection": None})
            return httpx.Response(503)

        monkeypatch.setattr(httpx.AsyncClient, "post", post)
        body = {"text": "Vay mua nhà 2 tỷ trong 20 năm. Thu nhập 45 triệu. Gọi lại giúp tôi", "client_message_id": "same"}
        first = client.post(f"/sessions/{sid}/messages", headers=auth, json=body)
        second = client.post(f"/sessions/{sid}/messages", headers=auth, json=body)
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert first.json()["reply"] == DEFAULT_REPLY
        assert first.json()["used_fact_ids"] == []
        assert app.state.harness.db.one("SELECT count(*) AS n FROM callbacks")["n"] == 1
