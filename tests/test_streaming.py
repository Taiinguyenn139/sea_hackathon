import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from harness.api import create_app
from harness.config import Settings
from harness.llm import DEFAULT_REPLY, DraftSuggestion, LLMUnavailable, json_completion


def frame(content="", finish=None):
    value = {"choices": [{"delta": {"content": content}, "finish_reason": finish}]}
    return ("data: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode()


class ProviderStream(httpx.AsyncByteStream):
    def __init__(self, text, *, fail=False, finished=None):
        self.text, self.fail, self.finished = text, fail, finished

    async def __aiter__(self):
        raw = json.dumps({"draft": self.text}, ensure_ascii=False)
        cut = raw.index(". ") + 2 if ". " in raw else len(raw) // 2
        # Split both JSON and multi-byte UTF-8 at arbitrary transport boundaries.
        first = frame(raw[:cut])
        for index in range(0, len(first), 7):
            yield first[index:index + 7]
        await asyncio.sleep(0.01)
        if self.fail:
            raise httpx.ReadError("private provider failure")
        yield frame(raw[cut:])
        yield frame(finish="stop")
        yield b'data: {"choices":[],"usage":{"total_tokens":12}}\n\n'
        if self.finished is not None:
            self.finished.append(True)
        yield b"data: [DONE]\n\n"


def mock_provider(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda: original(transport=httpx.MockTransport(handler)))


def settings(**kwargs):
    return Settings(llm_backend="openai", openai_key="test-only", openai_model="primary",
                    openai_backup_model="backup", **kwargs)


def test_provider_emits_before_finish_with_split_unicode(monkeypatch):
    finished, previews = [], []

    def handler(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, stream=ProviderStream("Xin chào. Tôi sẽ hỗ trợ bạn.", finished=finished))

    mock_provider(monkeypatch, handler)

    def preview(text):
        previews.append(text)
        assert not finished

    result = json_completion(settings(), "JSON", {}, DraftSuggestion, purpose="draft", on_delta=preview)
    assert result["draft"] == "Xin chào. Tôi sẽ hỗ trợ bạn."
    assert "Xin chào. " in previews


@pytest.mark.parametrize("both_fail", [False, True])
def test_partial_failure_resets_before_backup(monkeypatch, both_fail):
    calls, previews = [], []

    def handler(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        return httpx.Response(200, stream=ProviderStream(
            "Bản nháp. Nội dung tiếp theo." if model == "primary" else "Dự phòng. Hoàn tất.",
            fail=both_fail or model == "primary",
        ))

    mock_provider(monkeypatch, handler)
    if both_fail:
        with pytest.raises(LLMUnavailable):
            json_completion(settings(), "JSON", {}, DraftSuggestion, purpose="draft", on_delta=previews.append)
    else:
        result = json_completion(settings(), "JSON", {}, DraftSuggestion, purpose="draft", on_delta=previews.append)
        assert result["draft"] == "Dự phòng. Hoàn tất."
        assert previews.index("") < previews.index("Dự phòng. ")
    assert calls == ["primary", "backup"]
    assert "" in previews


def test_stream_deadline_cancels_both_models(monkeypatch):
    cancelled = []

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            try:
                await asyncio.sleep(10)
                yield b""
            finally:
                cancelled.append(True)

    mock_provider(monkeypatch, lambda request: httpx.Response(200, stream=SlowStream()))
    started = time.monotonic()
    with pytest.raises(LLMUnavailable):
        json_completion(settings(llm_call_budget_seconds=0.1), "JSON", {}, DraftSuggestion,
                        purpose="draft", on_delta=lambda text: None)
    assert time.monotonic() - started < 1
    assert len(cancelled) == 2


def events(response):
    parsed = []
    for value in response.text.split("\n\n"):
        if value.startswith("event: "):
            kind, data = value.split("\ndata: ", 1)
            parsed.append((kind[7:], json.loads(data)))
    return parsed


@pytest.mark.parametrize("draft", ["Xin chào. Tôi có thể hỗ trợ bạn.", "Lãi suất 999 phần trăm. Chắc chắn được duyệt."])
def test_sse_auth_guard_receipt_and_retry(tmp_path, monkeypatch, draft):
    config = Settings(db_path=str(tmp_path / "stream.sqlite3"))
    app = create_app(config)
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if payload.get("stream"):
            return httpx.Response(200, stream=ProviderStream(draft))
        value = {"stage": "discover", "objection": None} if "Phân loại" in payload["messages"][0]["content"] else {"spans": []}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(value)}}]})

    mock_provider(monkeypatch, handler)
    with TestClient(app) as client:
        session = client.post("/sessions", json={"demo_profile": "hoa", "mode": "harness"}).json()
        path = f"/sessions/{session['session_id']}/messages/stream"
        auth = {"Authorization": "Bearer " + session["session_token"]}
        body = {"text": "Xin chào", "client_message_id": "stream-id"}
        assert client.post(path, json=body).status_code == 403
        config.llm_backend, config.openai_key = "openai", "test-only"
        config.openai_model, config.openai_backup_model = "primary", "backup"
        response = client.post(path, headers=auth, json=body)
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "no-transform" in response.headers["cache-control"]
        received = events(response)
        assert received[0][0] == "status" and received[-1][0] == "result"
        texts = [data["text"] for kind, data in received if kind == "text"]
        if "999" in draft:
            assert not texts
            assert "999" not in received[-1][1]["reply"]
        else:
            assert texts and texts[0].startswith("Xin chào.")
        count = len(calls)
        retry = events(client.post(path, headers=auth, json=body))
        assert retry[-1] == received[-1]
        assert len(calls) == count
        assert app.state.harness.db.one("SELECT count(*) AS n FROM turns")["n"] == 1
        conflict = events(client.post(path, headers=auth, json={**body, "text": "Khác"}))
        assert conflict[-1][0] == "error" and conflict[-1][1]["status"] == 409


def test_stream_exhaustion_returns_default_result(tmp_path, monkeypatch):
    config = settings(db_path=str(tmp_path / "failed.sqlite3"))
    mock_provider(monkeypatch, lambda request: httpx.Response(503))
    with TestClient(create_app(config)) as client:
        session = client.post("/sessions", json={"demo_profile": "hoa", "mode": "harness"}).json()
        response = client.post(f"/sessions/{session['session_id']}/messages/stream",
                               headers={"Authorization": "Bearer " + session["session_token"]},
                               json={"text": "Xin chào", "client_message_id": "failed"})
        assert events(response)[-1][1]["reply"] == DEFAULT_REPLY


def test_socket_progress_disconnect_persists_without_replay(tmp_path, monkeypatch):
    config = settings(db_path=str(tmp_path / "disconnect.sqlite3"))
    app = create_app(config)
    drafts = []

    class PausedStream(ProviderStream):
        async def __aiter__(self):
            async for part in super().__aiter__():
                yield part
                await asyncio.sleep(0.02)

    def handler(request):
        payload = json.loads(request.content)
        if payload.get("stream"):
            drafts.append(True)
            return httpx.Response(200, stream=PausedStream("Xin chào. Tôi có thể giúp bạn."))
        value = {"stage": "discover"} if "Phân loại" in payload["messages"][0]["content"] else {"spans": []}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(value)}}]})

    mock_provider(monkeypatch, handler)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        with httpx.Client(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}", timeout=5) as client:
            session = client.post("/sessions", json={"demo_profile": "hoa", "mode": "harness"}).json()
            path = f"/sessions/{session['session_id']}/messages"
            auth = {"Authorization": "Bearer " + session["session_token"]}
            body = {"text": "Xin chào", "client_message_id": "disconnected"}
            with client.stream("POST", path + "/stream", headers=auth, json=body) as response:
                lines = response.iter_lines()
                assert next(lines) == "event: status"
                for line in lines:
                    assert line != "event: result"
                    if line == "event: text":
                        assert "Xin chào." in next(lines)
                        break
                else:
                    pytest.fail("No partial text received")
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                receipt = app.state.harness.db.one("SELECT result FROM turns WHERE result IS NOT NULL")
                if receipt:
                    break
                time.sleep(0.01)
            assert receipt
            retry = client.post(path, headers=auth, json=body)
            assert retry.json()["reply"] == "Xin chào. Tôi có thể giúp bạn."
            assert len(drafts) == 1
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
        assert not thread.is_alive()
