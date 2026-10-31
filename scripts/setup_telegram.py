"""Register and check the Telegram webhook without printing credentials."""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class SetupError(RuntimeError):
    pass


def request_json(url, payload=None, headers=None):
    request = Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as error:
        raise SetupError(f"Request failed (HTTP {error.code}); credentials and response body hidden.") from None
    except (URLError, TimeoutError, ValueError, OSError):
        raise SetupError("Request failed: check network, TLS and endpoint; credentials hidden.") from None


def telegram(token, method, payload=None):
    result = request_json(f"https://api.telegram.org/bot{token}/{method}", payload or {})
    if not isinstance(result, dict) or not result.get("ok"):
        raise SetupError(f"Telegram {method} failed; credentials and response body hidden.")
    return result["result"]


def setup(app_url, token, secret, *, check_only=False, chat_id=None, profile="hoa", admin_token=""):
    parsed = urlsplit(app_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise SetupError("APP_URL must be the backend HTTPS origin, without a path or credentials.")
    if not re.fullmatch(r"[0-9]+:[a-zA-Z0-9_-]+", token):
        raise SetupError("Missing or invalid TELEGRAM_BOT_TOKEN; use BotFather's token.")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{32,256}", secret):
        raise SetupError("TELEGRAM_WEBHOOK_SECRET must contain 32–256 letters, digits, underscores or hyphens.")
    if chat_id is not None and (not re.fullmatch(r"[1-9][0-9]*", chat_id) or not admin_token):
        raise SetupError("Linking requires a positive private chat ID and ADMIN_TOKEN.")
    if check_only and chat_id is not None:
        raise SetupError("Use --chat-id without --check-only to create an identity link.")

    app_url = app_url.rstrip("/")
    health = request_json(app_url + "/healthz")
    if health.get("status") != "ok":
        raise SetupError("Backend is not healthy; webhook was not changed.")
    # No message: validates the shared secret without creating a customer event.
    probe = request_json(
        app_url + "/webhooks/telegram",
        {"update_id": 0},
        {"X-Telegram-Bot-Api-Secret-Token": secret},
    )
    if probe.get("ok") is not True:
        raise SetupError("Backend webhook probe failed; webhook was not changed.")

    bot = telegram(token, "getMe")
    target = app_url + "/webhooks/telegram"
    before = telegram(token, "getWebhookInfo")
    if before.get("url") and before["url"] != target:
        raise SetupError("This bot already uses a different webhook. Use a dedicated demo bot.")
    if not check_only:
        telegram(
            token,
            "setWebhook",
            {
                "url": target,
                "secret_token": secret,
                "allowed_updates": ["message"],
                "max_connections": 1,
                "drop_pending_updates": False,
            },
        )
    info = telegram(token, "getWebhookInfo")
    if info.get("url") != target:
        raise SetupError("Webhook is not registered for this backend.")
    if chat_id is not None:
        linked = request_json(
            app_url + "/admin/identity-links/telegram",
            {"external_id": chat_id, "customer_id": profile},
            {"Authorization": "Bearer " + admin_token},
        )
        if linked.get("status") != "linked":
            raise SetupError("Identity link was not confirmed.")
    return {"bot_username": bot["username"], "pending_updates": info.get("pending_update_count", 0)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-url", required=True, help="Public backend HTTPS origin")
    parser.add_argument("--token-file", type=Path, help="Read bot token from a private file instead of the environment")
    parser.add_argument("--check-only", action="store_true", help="Check connectivity without changing registration")
    parser.add_argument("--chat-id", help="Link this private chat to a demo profile (requires ADMIN_TOKEN)")
    parser.add_argument("--profile", default="hoa", help="Demo profile for --chat-id, default: hoa")
    args = parser.parse_args()
    try:
        token = args.token_file.read_text().strip() if args.token_file else os.getenv("TELEGRAM_BOT_TOKEN", "")
        result = setup(
            args.app_url,
            token,
            os.getenv("TELEGRAM_WEBHOOK_SECRET", ""),
            check_only=args.check_only,
            chat_id=args.chat_id,
            profile=args.profile,
            admin_token=os.getenv("ADMIN_TOKEN", ""),
        )
    except SetupError as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError:
        print("Cannot read token file.", file=sys.stderr)
        return 1
    print(f"Bot: https://t.me/{result['bot_username']}")
    print(f"Webhook verified; pending updates: {result['pending_updates']}")
    if args.chat_id:
        print("Demo identity linked. Send /start to the bot, then verify the demo date of birth.")
    else:
        print("New private chats can start advising immediately with an individual customer profile.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
