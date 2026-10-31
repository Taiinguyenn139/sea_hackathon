"""Application orchestration shared by web, Telegram and benchmark clients."""

import hashlib
import json
import secrets
import time

from fastapi import HTTPException

from bank_tools.service import canonical, ident, now
from harness.agent.engine import Engine, event
from harness.identity import extract_phone_claims, get_profile
from harness.phone_identity import PhoneIdentity


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Harness:
    def __init__(self, db, settings):
        self.db = db
        self.settings = settings
        self.engine = Engine(db, settings)
        self.phone_identity = PhoneIdentity(db)

    def telegram_customer(self, target, name):
        """Resolve an explicit bank link or create a private prospect identity."""
        with self.db.transaction() as c:
            link = c.execute(
                "SELECT customer_id FROM identity_links WHERE channel='telegram' AND external_id=?", (target,)
            ).fetchone()
            if link:
                return link["customer_id"]
            customer = ident("guest_", ["telegram", target])
            display_name = name.strip()[:100] if isinstance(name, str) and name.strip() else "Khách Telegram"
            c.execute(
                "INSERT OR IGNORE INTO guest_customers VALUES(?,?,?,?,?)",
                (customer, "telegram", target, display_name, now()),
            )
            return customer

    def can_chat(self, session):
        # A channel-owned prospect can discuss their own needs without claiming
        # a bank identity. Existing bank profiles still require verification.
        return bool(session["verified"]) or self.db.one(
            "SELECT id FROM guest_customers WHERE id=? AND channel=? AND external_id=?",
            (session["customer_id"], session["channel"], session["external_id"]),
        ) is not None or self.db.one(
            "SELECT session_id FROM phone_sessions WHERE session_id=? AND customer_id=?",
            (session["id"], session["customer_id"]),
        ) is not None

    def identity_status(self, session):
        if session["verified"]:
            return "verified"
        if self.can_chat(session):
            return "identified" if self.db.one(
                "SELECT id FROM customer_profiles WHERE id=?", (session["customer_id"],)
            ) else "guest"
        return "unverified"

    def new_session(self, profile, mode="harness", channel="web", external_id=None):
        identity = get_profile(profile)
        if identity:
            profile = identity["id"]
        guest = channel == "telegram" and self.db.one(
            "SELECT id FROM guest_customers WHERE id=? AND channel=? AND external_id=?",
            (profile, channel, external_id),
        ) is not None
        if not identity and not guest:
            raise HTTPException(400, "Unknown demo profile")
        return self._create_session(profile, mode, channel, external_id)

    def user_id(self, customer):
        profile = get_profile(customer)
        if profile:
            return profile["user_id"]
        linked = self.db.one("SELECT phone_e164 FROM customer_profiles WHERE id=?", (customer,))
        return linked["phone_e164"] if linked else None

    def phone_session(self, challenge, code):
        customer = self.phone_identity.confirm(challenge, code)
        return self._create_session(customer, phone_authenticated=True)

    def _create_session(self, profile, mode="harness", channel="web", external_id=None, phone_authenticated=False):
        self.engine.memory.flush(profile)
        sid = "s_" + secrets.token_hex(12)
        token = secrets.token_urlsafe(32)
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO sessions(id,customer_id,channel,external_id,mode,token_hash,created_at) VALUES(?,?,?,?,?,?,?)",
                (sid, profile, channel, external_id or sid, mode, token_hash(token), now()),
            )
            c.execute("INSERT INTO session_playbooks VALUES(?,?)", (sid, self.db.setting("playbook", "v1")))
            if phone_authenticated:
                c.execute("INSERT INTO phone_sessions VALUES(?,?)", (sid, profile))
        return {
            "session_id": sid,
            "session_token": token,
            "user_id": self.user_id(profile) if phone_authenticated or get_profile(profile) else None,
            "identity_status": self.identity_status(self.session(sid)),
            "brief": self.engine.memory.brief(profile) if phone_authenticated else {"redacted": True},
        }

    def session(self, sid):
        session = self.db.one("SELECT * FROM sessions WHERE id=?", (sid,))
        if not session:
            raise HTTPException(404, "Session not found")
        return session

    def verify(self, session, phone, source):
        # Refresh server state: a claim can never select or replace the bound
        # customer, nor reopen an ended session.
        session = self.session(session["id"])
        if session["status"] == "ended":
            raise HTTPException(409, "Session ended")
        identity = session["customer_id"]
        if session["verified"]:
            return {
                "identity_status": "verified",
                "user_id": self.user_id(identity),
                "brief": self.engine.memory.brief(identity),
            }
        current = time.time()
        # The canonical runtime key makes aliases and phone formats share one
        # customer-scoped limit, independently of the source/IP cap.
        with self.db.transaction() as c:
            n = c.execute(
                "SELECT count(*) FROM auth_attempts WHERE at>? AND (identity=? OR source=?)",
                (current - 900, identity, source),
            ).fetchone()[0]
            if n >= 5:
                raise HTTPException(429, "Vui lòng thử nhận diện lại sau ít phút.")
            c.execute("INSERT INTO auth_attempts VALUES(?,?,?)", (identity, source, current))
        result = self.engine.bank.call(
            "verify_identity", {"phone": phone, "as_of": self.db.setting("clock")}, identity, session["id"]
        )
        if not result["data"].get("verified"):
            return {
                "identity_status": "unverified",
                "user_id": None,
                "brief": {"redacted": True},
                "message": "Chưa thể nhận diện hồ sơ demo trong phiên này. Vui lòng kiểm tra số điện thoại.",
                "tool_call_id": result["tool_call_id"],
            }
        with self.db.transaction() as c:
            c.execute("DELETE FROM auth_attempts WHERE identity=? AND source=? AND at=?", (identity, source, current))
            c.execute("UPDATE sessions SET verified=1 WHERE id=?", (session["id"],))
        return {
            "identity_status": "verified",
            "user_id": self.user_id(identity),
            "brief": self.engine.memory.brief(identity),
            "tool_call_id": result["tool_call_id"],
        }

    def _phone_message(self, session, text, client_id, existing, phones, source):
        """Handle fixture claims before the text reaches extraction or an LLM."""
        tool_calls = []
        if session["verified"]:
            reply = "Phiên này đã nhận diện hồ sơ demo. Số điện thoại trong tin nhắn không thay đổi hồ sơ đang gắn."
            state = "verified"
        elif len(phones) != 1:
            reply = "Anh/chị vui lòng gửi một số điện thoại duy nhất để nhận diện hồ sơ demo của phiên này."
            state = self.identity_status(session)
        elif not get_profile(session["customer_id"]):
            # A guest's typed phone is not the channel-ownership proof used by
            # PhoneIdentity.bind or the OTP flow; preserve that separate path.
            reply = (
                "Tin nhắn số điện thoại không tự liên kết hay chuyển hồ sơ. "
                "Để tiếp tục hồ sơ trên website, hãy dùng nút chia sẻ số của chính anh/chị trên Telegram và mã xác nhận."
            )
            state = self.identity_status(session)
        else:
            result = self.verify(session, phones[0], source)
            state = result["identity_status"]
            reply = (
                "Đã nhận diện hồ sơ demo. Anh/chị có thể tiếp tục nhu cầu đã trao đổi; đây chưa phải đăng nhập khách hàng."
                if state == "verified" else result["message"]
            )
            if result.get("tool_call_id"):
                call = self.db.one("SELECT result FROM tool_calls WHERE id=?", (result["tool_call_id"],))
                if call:
                    tool_calls.append(json.loads(call["result"]))
        turn = existing["id"] if existing else ident("t_", [session["id"], client_id])
        response = {
            "session_id": session["id"],
            "reply": reply,
            "identity_status": state,
            "user_id": self.user_id(session["customer_id"]) if state in ("verified", "identified") else None,
            "used_fact_ids": [],
            "guard_events": [event("G7", "demo_phone_identified" if state == "verified" else "identity_unverified", "identify")],
            "handoff_id": None,
        }
        if state == "verified":
            # Return the same bound profile as the direct verification endpoint,
            # and persist its snapshot so message retries return one receipt.
            response["brief"] = self.engine.memory.brief(session["customer_id"])
        trace = {
            "turn_id": turn,
            "context_parts": [],
            "facts": [],
            "source_spans": [],
            "guard_events": response["guard_events"],
            "tool_calls": tool_calls,
            "plan": {"stage": "identity", "action": "phone_claim"},
            "business_as_of": self.db.setting("clock"),
            "backend": self.settings.llm_backend,
        }
        # Persist the receipt before returning; retries with the same message ID
        # reuse it and do not consume another verification attempt.
        with self.db.transaction() as c:
            if not existing:
                c.execute(
                    "INSERT INTO turns VALUES(?,?,?,?,?,?,?)",
                    (turn, session["id"], client_id, text, reply, canonical(response), now()),
                )
            else:
                c.execute("UPDATE turns SET reply=?,result=? WHERE id=?", (reply, canonical(response), turn))
            c.execute("INSERT OR REPLACE INTO traces VALUES(?,?,?)", (turn, session["id"], canonical(trace)))
        return response

    def message(self, sid, text, client_id, source="web"):
        session = self.session(sid)
        with self.engine.lock(session["customer_id"]):
            session = self.session(sid)
            existing = self.db.one("SELECT * FROM turns WHERE session_id=? AND client_id=?", (sid, client_id))
            if existing and existing["text"] != text:
                raise HTTPException(409, "Message id already used for another message")
            if existing and existing["result"]:
                return json.loads(existing["result"])
            if session["status"] != "active":
                raise HTTPException(409, "Session is waiting for staff or ended")
            phones = extract_phone_claims(text)
            if phones:
                return self._phone_message(session, text, client_id, existing, phones, source)
            turn = existing["id"] if existing else ident("t_", [sid, client_id])
            if not existing:
                with self.db.transaction() as c:
                    c.execute(
                        "INSERT INTO turns VALUES(?,?,?,?,?,?,?)", (turn, sid, client_id, text, None, None, now())
                    )
                    if self.can_chat(session):
                        self.engine.memory.enqueue(c, turn, session["customer_id"], sid, session["channel"], text)
            state = self.engine.run(session, text, turn)
            response = {
                "session_id": sid,
                "identity_status": self.identity_status(session),
                "user_id": self.user_id(session["customer_id"]) if self.can_chat(session) else None,
                "reply": state.get("reply", "Em đã tiếp nhận yêu cầu."),
                "used_fact_ids": state.get("used_fact_ids", []),
                "guard_events": state.get("guard_events", []),
                "handoff_id": state.get("handoff_id"),
            }
            trace = {
                "turn_id": turn,
                "context_parts": state.get("context_parts", []),
                "facts": [
                    {
                        **f,
                        "customer_id": session["customer_id"],
                        "observed_at": f.get("observed_at", self.db.setting("clock")),
                    }
                    for f in state.get("facts", {}).values()
                ],
                "source_spans": state.get("source_spans", []),
                "guard_events": response["guard_events"],
                "tool_calls": state.get("tool_calls", []),
                "plan": state.get("plan", {}),
                "business_as_of": self.db.setting("clock"),
                "backend": self.settings.llm_backend,
            }
            with self.db.transaction() as c:
                c.execute(
                    "UPDATE turns SET reply=?,result=? WHERE id=?", (response["reply"], canonical(response), turn)
                )
                c.execute("INSERT OR REPLACE INTO traces VALUES(?,?,?)", (turn, sid, canonical(trace)))
            return response

    def handoff(self, sid, reason):
        session = self.session(sid)
        if not self.can_chat(session):
            raise HTTPException(403, "Verify identity first")
        previous = self.db.one(
            "SELECT * FROM handoffs WHERE session_id=? AND status!='completed' ORDER BY created_at DESC LIMIT 1", (sid,)
        )
        if previous:
            return self.handoff_detail(previous)
        self.message(
            sid,
            "Yêu cầu chuyên viên: " + reason,
            "handoff-request:" + str(self.db.one("SELECT count(*) AS n FROM handoffs WHERE session_id=?", (sid,))["n"]),
        )
        return self.handoff_detail(
            self.db.one("SELECT * FROM handoffs WHERE session_id=? ORDER BY created_at DESC LIMIT 1", (sid,))
        )

    @staticmethod
    def handoff_detail(row):
        if not row:
            raise HTTPException(404, "Handoff not found")
        data = json.loads(row["summary"])
        data["status"] = row["status"]
        if row["receipt"]:
            data["completion"] = json.loads(row["receipt"])
        return data

    def reset(self):
        self.db.backup()
        with self.engine.checkpointer.lock:
            if self.db.path != ":memory:":
                import sqlite3

                with sqlite3.connect(self.db.path + ".checkpoints.bak") as target:
                    self.engine.cp_conn.backup(target)
        ids = [r["id"] for r in self.db.all("SELECT id FROM sessions")]
        for sid in ids:
            self.engine.checkpointer.delete_thread(sid)
        tables = [
            "session_playbooks",
            "sessions",
            "phone_sessions",
            "phone_challenges",
            "turns",
            "facts",
            "tool_calls",
            "traces",
            "memory_jobs",
            "customer_sequences",
            "journey_events",
            "handoffs",
            "callbacks",
            "provider_events",
            "outbox",
            "faults",
            "auth_attempts",
        ]
        with self.db.transaction() as c:
            for table in tables:
                c.execute("DELETE FROM " + table)
        self.db.set("clock", "2026-10-31")
        self.db.set("playbook", "v1")
        return {"status": "reset"}
