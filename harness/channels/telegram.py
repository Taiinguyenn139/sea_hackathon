"""Telegram inbox/outbox: acknowledge durable events before running the agent."""

import json
import re
import sqlite3
import threading
import time

import httpx
from fastapi import HTTPException

from bank_tools.service import canonical, ident
from harness.input_guard import VERSION, inspect_input
from harness.llm import DEFAULT_REPLY, LLMUnavailable

PHONE_KEYBOARD = {
    "keyboard": [[{"text": "Chia sẻ số điện thoại", "request_contact": True}]],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}


class Dispatcher:
    def __init__(self, harness):
        self.harness = harness
        self.db = harness.db
        self.settings = harness.settings
        self.lock = threading.RLock()

    def accept(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("update_id"), int):
            raise HTTPException(400, "Invalid Telegram update")
        message = payload.get("message")
        if not message or not isinstance(message, dict):
            return
        chat = message.get("chat", {})
        if chat.get("type") != "private" or message.get("from", {}).get("is_bot"):
            return
        text = message.get("text")
        contact = message.get("contact")
        if text is not None and (not isinstance(text, str) or len(text) > 8000):
            return
        if not (isinstance(text, str) and len(text) <= 8000) and not (
            isinstance(contact, dict) and isinstance(contact.get("phone_number"), str)
            and len(contact["phone_number"]) <= 64
        ):
            return
        if not isinstance(chat.get("id"), int):
            raise HTTPException(400, "Invalid chat")
        key = "telegram:" + str(payload["update_id"])
        with self.lock:
            if self.db.one("SELECT id FROM provider_events WHERE id=?", (key,)):
                return
            blocked = inspect_input(text, self.settings) if isinstance(text, str) else None
            # Build our own envelope. Never trust client-supplied approval markers
            # or persist extra Telegram fields that can contain sensitive text.
            envelope = {
                "update_id": payload["update_id"],
                "input_guard_version": VERSION,
                "message": {
                    "date": message.get("date", 0),
                    "text": "[blocked]" if blocked else text or "",
                    "chat": {"id": chat["id"], "type": "private"},
                },
            }
            if blocked:
                envelope["guard_events"] = blocked["guard_events"]
            else:
                # Dedicated contact consent is handled by PhoneIdentity.bind,
                # outside agent prompts. Keep only the fields that flow needs.
                sender = message.get("from", {})
                name = sender.get("first_name")
                envelope["message"]["from"] = {
                    "id": sender.get("id"), "first_name": name[:100] if isinstance(name, str) else "",
                }
                if isinstance(contact, dict):
                    envelope["message"]["contact"] = {
                        "phone_number": contact.get("phone_number"), "user_id": contact.get("user_id"),
                    }
            with self.db.transaction() as c:
                c.execute(
                    "INSERT INTO provider_events VALUES(?,?,?,?,?,?)",
                    (key, "telegram", canonical(envelope), "processed" if blocked else "pending", time.time(), None),
                )
                if blocked:
                    self.reply(key, str(chat["id"]), blocked["reply"])

    def reply(self, key, target, text, reply_markup=None):
        self.db.execute(
            "INSERT OR IGNORE INTO outbox(id,channel,target,text,status,reply_markup) VALUES(?,?,?,?,?,?)",
            (ident("out_", [key]), "telegram", target, text, "pending",
             canonical(reply_markup) if reply_markup else None),
        )

    def replay(self):
        if not self.settings.release_ready:
            return
        with self.lock:
            for row in self.db.all("SELECT * FROM provider_events WHERE status='pending' ORDER BY received_at,id"):
                payload = json.loads(row["payload"])
                message = payload["message"]
                target = str(message["chat"]["id"])
                text = message.get("text", "")
                contact = message.get("contact")
                markup = None
                try:
                    customer = self.harness.telegram_customer(target, message.get("from", {}).get("first_name"))
                    session = self.db.one(
                        "SELECT * FROM sessions WHERE channel='telegram' AND external_id=? AND customer_id=? "
                        "AND status!='ended' ORDER BY created_at DESC,rowid DESC LIMIT 1",
                        (target, customer),
                    )
                    command = text.strip().split(maxsplit=1)[0].split("@", 1)[0] if text.strip() else ""
                    start = command == "/start"
                    previous = self.db.setting("telegram_last:" + target, 0)
                    stamp = message.get("date", 0)
                    late = bool(stamp and stamp < previous)
                    if start and not late and session and session["status"] == "active":
                        self.harness.engine.memory.flush(customer)
                        self.db.execute("UPDATE sessions SET status='ended' WHERE id=?", (session["id"],))
                        session = None
                    new_session = not session
                    if not session:
                        created = self.harness.new_session(customer, "harness", "telegram", target)
                        session = self.harness.session(created["session_id"])
                    if late:
                        reply = "Tin nhắn đến trễ so với cuộc trao đổi hiện tại. Anh/chị vui lòng gửi lại thông tin cần cập nhật để em xác nhận."
                    elif not self.harness.can_chat(session):
                        dob = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
                        if dob:
                            result = self.harness.verify(session, dob[1], "telegram:" + target)
                            reply = (
                                "Đã xác minh. Anh/chị cần tư vấn sản phẩm nào?"
                                if result["identity_status"] == "verified"
                                else "Chưa thể xác minh. Anh/chị vui lòng kiểm tra lại thông tin."
                            )
                        else:
                            reply = "Để tiếp tục demo, vui lòng xác minh ngày sinh theo định dạng năm-tháng-ngày."
                    elif isinstance(contact, dict):
                        profile = self.harness.engine.memory.profile(customer)
                        self.harness.phone_identity.bind(
                            customer, profile["name"], target, contact, message.get("from", {}).get("id")
                        )
                        reply = (
                            "Đã lưu số điện thoại và giữ lại thông tin anh/chị đã chia sẻ. "
                            "Anh/chị có thể dùng số này để tiếp tục hội thoại trên website."
                        )
                        markup = {"remove_keyboard": True}
                    elif session["status"] == "waiting_human":
                        reply = "Chuyên viên đang tiếp nhận yêu cầu. Em sẽ tiếp tục khi chuyên viên hoàn tất."
                    elif start or command == "/phone":
                        reply = (
                            "Chào anh/chị! Em có thể tư vấn vay mua nhà, vay tiêu dùng và thẻ tín dụng. "
                            "Anh/chị muốn tìm hiểu sản phẩm nào?"
                        )
                    else:
                        reply = self.harness.message(
                            session["id"], text, row["id"],
                            input_checked=payload.get("input_guard_version") == VERSION,
                        )["reply"]
                    if self.harness.can_chat(session) and session["status"] == "active" and not contact and not late and (
                        start or command == "/phone" or new_session
                    ) and not self.db.one(
                        "SELECT id FROM customer_profiles WHERE id=?", (customer,)
                    ):
                        reply += (
                            "\n\nAnh/chị có thể chia sẻ số điện thoại để tiếp tục hội thoại trên website "
                            "và giữ các thông tin đã trao đổi."
                        )
                        markup = PHONE_KEYBOARD
                    self.reply(row["id"], target, reply, markup)
                    self.db.set("telegram_last:" + target, max(previous, stamp))
                    self.db.execute("UPDATE provider_events SET status='processed',error=NULL WHERE id=?", (row["id"],))
                except LLMUnavailable:
                    self.reply(row["id"], target, DEFAULT_REPLY)
                    self.db.execute(
                        "UPDATE provider_events SET status='processed',error=? WHERE id=?",
                        ("llm_unavailable", row["id"]),
                    )
                except HTTPException as e:
                    self.reply(row["id"], target, str(e.detail))
                    self.db.execute(
                        "UPDATE provider_events SET status='processed',error=? WHERE id=?",
                        ("request_rejected", row["id"]),
                    )
                except (RuntimeError, sqlite3.Error, ValueError, KeyError):
                    self.db.execute("UPDATE provider_events SET error='processing_failed' WHERE id=?", (row["id"],))
                    break
            for row in self.db.all("SELECT customer_id FROM customer_sequences WHERE processed_seq<next_seq"):
                try:
                    self.harness.engine.memory.flush(row["customer_id"])
                except LLMUnavailable:
                    continue
            self.send_pending()

    def send_pending(self):
        if not self.settings.release_ready:
            return
        with self.lock:
            for row in self.db.all("SELECT * FROM outbox WHERE status='pending' ORDER BY rowid"):
                if row["target"].startswith("eval:"):
                    self.db.execute(
                        "UPDATE outbox SET status='sent',receipt=? WHERE id=?", (canonical({"sink": True}), row["id"])
                    )
                    continue
                if not self.settings.telegram_token:
                    continue
                # Before network I/O, record intent; crash/retry becomes unknown, never automatic resend.
                self.db.execute("UPDATE outbox SET status='unknown',attempts=attempts+1 WHERE id=?", (row["id"],))
                try:
                    body = {"chat_id": row["target"], "text": row["text"]}
                    if row["reply_markup"]:
                        body["reply_markup"] = json.loads(row["reply_markup"])
                    response = httpx.post(
                        "https://api.telegram.org/bot" + self.settings.telegram_token + "/sendMessage",
                        json=body,
                        timeout=15,
                    )
                    body = response.json()
                    if response.is_success and body.get("ok"):
                        self.db.execute(
                            "UPDATE outbox SET status='sent',receipt=? WHERE id=?",
                            (canonical({"message_id": body["result"]["message_id"]}), row["id"]),
                        )
                    else:
                        self.db.execute(
                            "UPDATE outbox SET status='failed',receipt=? WHERE id=?",
                            (
                                canonical(
                                    {
                                        "error_code": body.get("error_code", response.status_code),
                                        "retry_after": body.get("parameters", {}).get("retry_after"),
                                    }
                                ),
                                row["id"],
                            ),
                        )
                except (httpx.HTTPError, ValueError):
                    pass
