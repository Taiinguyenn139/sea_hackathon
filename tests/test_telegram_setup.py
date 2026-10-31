"""Setup must validate backend credentials before modifying Telegram registration."""

import pytest

from scripts import setup_telegram as setup

TOKEN = "123456:example-token"
SECRET = "s" * 48
APP_URL = "https://demo.example.com"


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def request(url, payload=None, headers=None):
        seen.append((url, payload, headers))
        if url.endswith("/healthz"):
            return {"status": "ok"}
        if url.endswith("/webhooks/telegram"):
            assert headers == {"X-Telegram-Bot-Api-Secret-Token": SECRET}
            assert payload == {"update_id": 0}
            return {"ok": True}
        if url.endswith("/getMe"):
            return {"ok": True, "result": {"username": "demo_bot"}}
        if url.endswith("/getWebhookInfo"):
            return {"ok": True, "result": {"url": APP_URL + "/webhooks/telegram", "pending_update_count": 0}}
        if url.endswith("/setWebhook"):
            return {"ok": True, "result": True}
        if url.endswith("/admin/identity-links/telegram"):
            assert headers == {"Authorization": "Bearer admin-test"}
            return {"status": "linked"}
        raise AssertionError("Unexpected request")

    monkeypatch.setattr(setup, "request_json", request)
    return seen


def test_setup_preserves_pending_messages_and_links_explicit_chat(calls):
    result = setup.setup(APP_URL, TOKEN, SECRET, chat_id="111", admin_token="admin-test")
    assert result["bot_username"] == "demo_bot"
    registration = next(payload for url, payload, _ in calls if url.endswith("/setWebhook"))
    assert registration == {
        "url": APP_URL + "/webhooks/telegram",
        "secret_token": SECRET,
        "allowed_updates": ["message"],
        "max_connections": 1,
        "drop_pending_updates": False,
    }
    assert calls[-1][1] == {"external_id": "111", "customer_id": "hoa"}


def test_check_only_does_not_change_registration_or_identity(calls):
    setup.setup(APP_URL, TOKEN, SECRET, check_only=True)
    assert not any(url.endswith(("/setWebhook", "/admin/identity-links/telegram")) for url, _, _ in calls)


def test_wrong_backend_secret_stops_before_telegram(monkeypatch):
    seen = []

    def request(url, payload=None, headers=None):
        seen.append(url)
        if url.endswith("/healthz"):
            return {"status": "ok"}
        raise setup.SetupError("Request failed (HTTP 403)")

    monkeypatch.setattr(setup, "request_json", request)
    with pytest.raises(setup.SetupError, match="403"):
        setup.setup(APP_URL, TOKEN, SECRET)
    assert not any("api.telegram.org" in url for url in seen)


def test_existing_other_webhook_is_preserved(calls, monkeypatch):
    original = setup.request_json

    def request(url, payload=None, headers=None):
        if url.endswith("/getWebhookInfo"):
            return {"ok": True, "result": {"url": "https://other.example.com/hook"}}
        return original(url, payload, headers)

    monkeypatch.setattr(setup, "request_json", request)
    with pytest.raises(setup.SetupError, match="different webhook"):
        setup.setup(APP_URL, TOKEN, SECRET)
    assert not any(url.endswith("/setWebhook") for url, _, _ in calls)


def test_http_errors_do_not_disclose_bot_token(monkeypatch):
    def fail(request, timeout):
        raise setup.HTTPError(request.full_url, 401, "secret error", {}, None)

    monkeypatch.setattr(setup, "urlopen", fail)
    with pytest.raises(setup.SetupError) as error:
        setup.telegram(TOKEN, "getMe")
    assert "401" in str(error.value)
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize("url", ["http://demo.example.com", "https://user:password@demo.example.com", APP_URL + "/path"])
def test_invalid_backend_origin_is_rejected(calls, url):
    with pytest.raises(setup.SetupError, match="HTTPS origin"):
        setup.setup(url, TOKEN, SECRET)
    assert calls == []
