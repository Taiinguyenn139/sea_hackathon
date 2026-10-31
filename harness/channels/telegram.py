"""Telegram inbox/outbox: acknowledge durable events before running the agent."""

import json
import re
import sqlite3
import threading
import time

import httpx
from fastapi import HTTPException

from bank_tools.service import canonical, ident


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
        if not isinstance(message.get("text"), str) or len(message["text"]) > 8000:
            return
        if not isinstance(chat.get("id"), int):
            raise HTTPException(400, "Invalid chat")
        key = "telegram:" + str(payload["update_id"])
        with self.db.transaction() as c:
            c.execute(
                "INSERT OR IGNORE INTO provider_events VALUES(?,?,?,?,?,?)",
                (key, "telegram", canonical(payload), "pending", time.time(), None),
            )

    def reply(self, key, target, text):
        self.db.execute(
            "INSERT OR IGNORE INTO outbox(id,channel,target,text,status) VALUES(?,?,?,?,?)",
            (ident("out_", [key]), "telegram", target, text, "pending"),
        )

    def replay(self):
        if not self.settings.release_ready:
            return
        with self.lock:
            for row in self.db.all("SELECT * FROM provider_events WHERE status='pending' ORDER BY received_at,id"):
                payload = json.loads(row["payload"])
                message = payload["message"]
                target = str(message["chat"]["id"])
                text = message["text"]
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
                    elif session["status"] == "waiting_human":
                        reply = "Chuyên viên đang tiếp nhận yêu cầu. Em sẽ tiếp tục khi chuyên viên hoàn tất."
                    elif start:
                        reply = (
                            "Chào anh/chị! Em có thể tư vấn vay mua nhà, vay tiêu dùng và thẻ tín dụng. "
                            "Anh/chị muốn tìm hiểu sản phẩm nào?"
                        )
                    else:
                        reply = self.harness.message(session["id"], text, row["id"])["reply"]
                    self.reply(row["id"], target, reply)
                    self.db.set("telegram_last:" + target, max(previous, stamp))
                    self.db.execute("UPDATE provider_events SET status='processed',error=NULL WHERE id=?", (row["id"],))
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
                self.harness.engine.memory.flush(row["customer_id"])
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
                    response = httpx.post(
                        "https://api.telegram.org/bot" + self.settings.telegram_token + "/sendMessage",
                        json={"chat_id": row["target"], "text": row["text"]},
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
