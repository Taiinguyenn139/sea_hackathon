"""Telegram inbox/outbox: acknowledge durable events before running the agent."""

import json
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
                        specialist = self.harness.specialist.current(session["id"])
                        if specialist:
                            self.harness.specialist.complete(session["id"], specialist["handoff_id"])
                        self.db.execute("UPDATE sessions SET status='ended' WHERE id=?", (session["id"],))
                        session = None
                    new_session = not session
                    if not session:
                        created = self.harness.new_session(customer, "harness", "telegram", target)
                        session = self.harness.session(created["session_id"])
                    if late:
                        reply = "Tin nhắn đến trễ so với cuộc trao đổi hiện tại. Anh/chị vui lòng gửi lại thông tin cần cập nhật để em xác nhận."
                    elif isinstance(contact, dict):
                        if self.settings.demo_phone_identity:
                            sender_id = message.get("from", {}).get("id")
                            if not isinstance(sender_id, int) or contact.get("user_id") != sender_id or str(sender_id) != target:
                                raise HTTPException(400, "Vui lòng bấm Chia sẻ số điện thoại để gửi số của chính anh/chị.")
                            session = self.harness._demo_phone_customer(session, contact.get("phone_number", ""))
                            linked_customer = customer = session["customer_id"]
                        else:
                            profile = self.harness.engine.memory.profile(customer)
                            linked_customer = self.harness.phone_identity.bind(
                                customer, profile["name"], target, contact, message.get("from", {}).get("id")
                            )
                        if linked_customer != customer:
                            self.db.execute("UPDATE sessions SET status='ended' WHERE id=?", (session["id"],))
                            customer = linked_customer
                            created = self.harness._create_session(customer, channel="telegram", external_id=target,
                                                                   phone_authenticated=True)
                            session = self.harness.session(created["session_id"])
                        reply = (
                            "Đã lưu số điện thoại và giữ lại thông tin anh/chị đã chia sẻ. "
                            "Anh/chị có thể bắt đầu chat ngay: muốn tìm hiểu vay mua nhà, vay tiêu dùng hay thẻ tín dụng? "
                            "Khi mở website, dùng cùng số điện thoại để tiếp tục ngữ cảnh này."
                        )
                        markup = {"remove_keyboard": True}
                    elif session["status"] == "waiting_human":
                        reply = "Chuyên viên đang tiếp nhận yêu cầu. Em sẽ tiếp tục khi chuyên viên hoàn tất."
                    elif self.settings.llm_backend != "openai" and not self.settings.demo_phone_identity and not self.db.one(
                        "SELECT id FROM customer_profiles WHERE id=?", (customer,)
                    ):
                        reply = (
                            "Chào anh/chị! Để bắt đầu tư vấn sản phẩm và giữ ngữ cảnh giữa Telegram với website, "
                            "anh/chị vui lòng bấm Chia sẻ số điện thoại bên dưới. "
                            "Hồ sơ cá nhân sẽ được tạo tự động, không cần chọn hồ sơ demo."
                        )
                        markup = PHONE_KEYBOARD
                    elif command == "/specialist":
                        result = self.harness.specialist.start(
                            session["id"], "Khách yêu cầu tư vấn chuyên sâu", row["id"]
                        )
                        reply = "Chuyên viên tư vấn\n" + result["reply"]
                    elif command == "/assistant":
                        # Resolve the target once so a replay cannot close a later handoff.
                        receipt_key = "telegram_specialist_complete:" + row["id"]
                        target_handoff = self.db.setting(receipt_key)
                        if target_handoff is None:
                            specialist = self.harness.specialist.current(session["id"])
                            target_handoff = specialist["handoff_id"] if specialist else ""
                            self.db.set(receipt_key, target_handoff)
                        if target_handoff:
                            self.harness.specialist.complete(session["id"], target_handoff)
                        reply = "Đã quay lại trợ lý. Nội dung tư vấn đã được lưu."
                    elif command == "/human":
                        self.harness.handoff(session["id"], "Khách Telegram yêu cầu nhân viên thật")
                        reply = "Đã chuyển hồ sơ cho nhân viên tiếp nhận. Vui lòng chờ phản hồi."
                    elif start or command == "/phone":
                        reply = (
                            "Chào anh/chị! Em có thể tư vấn vay mua nhà, vay tiêu dùng và thẻ tín dụng. "
                            "Anh/chị muốn tìm hiểu sản phẩm nào?"
                        )
                    else:
                        result = self.harness.message(
                            session["id"], text, row["id"], "telegram:" + target,
                            input_checked=payload.get("input_guard_version") == VERSION,
                        )
                        reply = result["reply"]
                        if result.get("responder") == "ai_specialist":
                            reply = "Chuyên viên tư vấn\n" + reply
                        session = self.harness.session(session["id"])
                        customer = session["customer_id"]
                    if self.settings.demo_phone_identity and not late and not contact and session["status"] == "active" and (
                        start or command == "/phone" or new_session
                    ):
                        reply += (
                            "\n\nBản demo: chia sẻ số điện thoại bằng nút bên dưới hoặc nhập số ngay trong chat. "
                            "Dùng cùng số trên web và Telegram để tiếp tục thông tin đã ghi nhớ, không cần mã xác nhận."
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
