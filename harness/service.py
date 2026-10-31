"""Application orchestration shared by web, Telegram and benchmark clients."""

import hashlib
import json
import logging
import secrets
import time

from fastapi import HTTPException

from bank_tools.service import canonical, ident, now
from harness.agent.engine import Engine, conversation_intent, event
from harness.agent.specialist import PROMPT_VERSION
from harness.customer_profile import can_access_memory
from harness.identity import extract_phone_claims, get_profile
from harness.input_guard import inspect_input
from harness.phone_identity import PhoneIdentity, normalize_phone
from harness.specialist import Specialist, requests_human
from harness.streaming import emit

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Harness:
    def __init__(self, db, settings):
        self.db = db
        self.settings = settings
        self.engine = Engine(db, settings)
        self.phone_identity = PhoneIdentity(db)
        self.specialist = Specialist(self)

    def telegram_customer(self, target, name):
        """Use a private customer even when an old admin link points at a demo."""
        with self.db.transaction() as c:
            link = c.execute(
                "SELECT customer_id FROM identity_links WHERE channel='telegram' AND external_id=?", (target,)
            ).fetchone()
            if link and (not get_profile(link["customer_id"]) or self.settings.demo_phone_identity and c.execute(
                "SELECT customer_id FROM demo_phone_links WHERE customer_id=?", (link["customer_id"],)
            ).fetchone()):
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
        return can_access_memory(self.db, session)

    def identity_status(self, session):
        if session["verified"]:
            return "verified"
        if self.can_chat(session):
            return "identified" if self.db.one(
                "SELECT id FROM customer_profiles WHERE id=?", (session["customer_id"],)
            ) or self.db.one(
                "SELECT customer_id FROM demo_phone_links WHERE customer_id=?", (session["customer_id"],)
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
        linked = channel == "telegram" and self.db.one(
            "SELECT p.id FROM customer_profiles p JOIN identity_links l ON l.customer_id=p.id "
            "WHERE l.channel='telegram' AND l.external_id=? AND p.id=? AND p.telegram_id=?",
            (external_id, profile, external_id),
        ) is not None
        demo_link = self.settings.demo_phone_identity and channel == "telegram" and self.db.one(
            "SELECT customer_id FROM identity_links WHERE channel='telegram' AND external_id=? AND customer_id=?",
            (external_id, profile),
        ) is not None and self.db.one("SELECT customer_id FROM demo_phone_links WHERE customer_id=?", (profile,)) is not None
        if not identity and not guest and not linked and not demo_link:
            raise HTTPException(400, "Unknown demo profile")
        result = self._create_session(profile, mode, channel, external_id, phone_authenticated=demo_link)
        if demo_link and identity:
            self.db.execute("UPDATE sessions SET verified=1 WHERE id=?", (result["session_id"],))
            result["identity_status"] = "verified"
        return result

    def user_id(self, customer):
        profile = get_profile(customer)
        if profile:
            return profile["user_id"]
        linked = self.db.one("SELECT phone_e164 FROM customer_profiles WHERE id=?", (customer,)) or self.db.one(
            "SELECT phone_e164 FROM demo_phone_links WHERE customer_id=?", (customer,)
        )
        return linked["phone_e164"] if linked else None

    def phone_session(self, challenge, code):
        customer = self.phone_identity.confirm(challenge, code)
        return self._create_session(customer, phone_authenticated=True)

    def demo_phone_session(self, raw_phone):
        if not self.settings.demo_phone_identity:
            raise HTTPException(404, "Demo phone entry is disabled")
        phone = normalize_phone(raw_phone)
        result = self._create_session(ident("demo_", [phone]))
        session = self._demo_phone_customer(self.session(result["session_id"]), phone)
        return {
            **result,
            "identity_status": self.identity_status(session),
            "user_id": self.user_id(session["customer_id"]),
            "brief": self.engine.memory.brief(session["customer_id"]),
            "telegram_url": "https://t.me/" + self.settings.telegram_bot_username,
        }

    def _create_session(self, profile, mode="harness", channel="web", external_id=None, phone_authenticated=False):
        if channel == "telegram" and self.db.one(
            "SELECT p.id FROM customer_profiles p JOIN identity_links l ON l.customer_id=p.id "
            "WHERE l.channel='telegram' AND l.external_id=? AND p.id=? AND p.telegram_id=?",
            (external_id, profile, external_id),
        ):
            phone_authenticated = True
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
            "status": "active",
            **self.specialist.metadata(sid),
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

    def new_conversation(self, previous):
        """Start a separate transcript without ending a conversation or handoff."""
        authenticated = bool(self.can_chat(previous))
        result = self._create_session(
            previous["customer_id"], previous["mode"], phone_authenticated=authenticated
        )
        if previous["verified"]:
            self.db.execute("UPDATE sessions SET verified=1 WHERE id=?", (result["session_id"],))
            result["identity_status"] = "verified"
        if authenticated and (self.db.one("SELECT id FROM customer_profiles WHERE id=?", (previous["customer_id"],))
                              or self.db.one("SELECT customer_id FROM demo_phone_links WHERE customer_id=?",
                                             (previous["customer_id"],))):
            result["telegram_url"] = "https://t.me/" + self.settings.telegram_bot_username
        return result

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

    def _demo_phone_customer(self, session, raw_phone):
        """Demo-only phone selection; never changes the contact/OTP ownership registry."""
        phone = normalize_phone(raw_phone)
        fixture = get_profile(phone)
        old_customer = session["customer_id"]
        with self.db.transaction() as c:
            linked = c.execute("SELECT customer_id FROM demo_phone_links WHERE phone_e164=?", (phone,)).fetchone()
            proven = c.execute("SELECT id FROM customer_profiles WHERE phone_e164=?", (phone,)).fetchone()
            if linked:
                customer = linked["customer_id"]
            elif proven:
                customer = proven["id"]
            elif fixture:
                customer = fixture["id"]
            else:
                # Preserve a prospect's memory when their first phone creates a profile.
                guest = c.execute("SELECT id FROM guest_customers WHERE id=?", (old_customer,)).fetchone()
                bound = c.execute(
                    "SELECT customer_id FROM demo_phone_links WHERE customer_id=? UNION "
                    "SELECT id FROM customer_profiles WHERE id=?", (old_customer, old_customer),
                ).fetchone()
                customer = old_customer if guest and not bound else ident("demo_", [phone])
                c.execute(
                    "INSERT OR IGNORE INTO guest_customers VALUES(?,?,?,?,?)",
                    (customer, "demo_phone", phone, "Khách demo", now()),
                )
            # A conversation with remembered content must not silently move to another person.
            already_linked = session["verified"] or c.execute(
                "SELECT customer_id FROM demo_phone_links WHERE customer_id=? UNION "
                "SELECT id FROM customer_profiles WHERE id=?", (old_customer, old_customer),
            ).fetchone()
            if customer != old_customer and already_linked and c.execute(
                "SELECT 1 FROM turns WHERE session_id=? LIMIT 1", (session["id"],)
            ).fetchone():
                raise HTTPException(409, "Vui lòng mở phiên chat mới rồi nhập số điện thoại khác.")
            c.execute("INSERT OR IGNORE INTO demo_phone_links VALUES(?,?)", (phone, customer))
            c.execute(
                "UPDATE sessions SET customer_id=?,verified=? WHERE id=?",
                (customer, int(get_profile(customer) is not None), session["id"]),
            )
            c.execute("INSERT OR REPLACE INTO phone_sessions VALUES(?,?)", (session["id"], customer))
            if session["channel"] == "telegram":
                c.execute(
                    "INSERT OR REPLACE INTO identity_links VALUES('telegram',?,?)", (session["external_id"], customer)
                )
        if customer != old_customer:
            self.engine.checkpointer.delete_thread(session["id"])
        return self.session(session["id"])

    def _phone_message(self, session, text, client_id, existing, phones, source):
        """Handle screened fixture claims before financial extraction or the agent."""
        tool_calls = []
        if self.settings.demo_phone_identity and len(phones) == 1:
            session = self._demo_phone_customer(session, phones[0])
            state = self.identity_status(session)
            reply = (
                "Đã kết nối hồ sơ demo theo số điện thoại. Nhập cùng số này trong chat web hoặc Telegram "
                "để dùng chung thông tin đã ghi nhớ. Anh/chị muốn tiếp tục nhu cầu nào?"
            )
        elif session["verified"]:
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
            "guard_events": [event(
                "G7", "demo_phone_identified" if state in ("verified", "identified") else "identity_unverified", "identify"
            )],
            "handoff_id": None,
        }
        if state in ("verified", "identified"):
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

    def message(self, sid, text, client_id, source="web", *, input_checked=False):
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
            blocked = inspect_input(text, self.settings, semantic=not input_checked)
            if blocked:
                return {
                    "session_id": sid,
                    "identity_status": self.identity_status(session),
                    "user_id": self.user_id(session["customer_id"]) if self.can_chat(session) else None,
                    **blocked,
                    "used_fact_ids": [],
                    "handoff_id": None,
                    **self.specialist.metadata(sid),
                    "status": session["status"],
                }
            phones = extract_phone_claims(text)
            if phones:
                return self._phone_message(session, text, client_id, existing, phones, source)
            turn = existing["id"] if existing else ident("t_", [sid, client_id])
            if not existing:
                with self.db.transaction() as c:
                    c.execute(
                        "INSERT INTO turns VALUES(?,?,?,?,?,?,?)", (turn, sid, client_id, text, None, None, now())
                    )
                    if self.can_chat(session) and conversation_intent(text) != "profile_name":
                        self.engine.memory.enqueue(c, turn, session["customer_id"], sid, session["channel"], text)
            current = self.specialist.current(sid)
            human = requests_human(text)
            if not human and current and current["status"] == "initializing":
                welcome = self.specialist.start(sid, current["reason"], current["start_key"], input_checked=True)
                if current["welcome_turn_id"] == turn:
                    return welcome
            if (not current and not human and self.settings.ai_specialist_enabled
                    and self.can_chat(session) and self.engine.handoff_requested(text)):
                return self.specialist.start(sid, text[:1000], "message:" + turn,
                                             input_checked=True, welcome_turn=turn)
            current = self.specialist.current(sid)
            engine = self.engine
            if current and not human:
                if self.settings.llm_backend != "openai":
                    from harness.llm import LLMUnavailable
                    raise LLMUnavailable("specialist_provider_unavailable")
                session["specialist_id"] = current["handoff_id"]
                engine = self.specialist.engine
            emit("metadata", responder=engine.responder)
            state = engine.run(session, ("Gặp người thật: " + text) if human else text, turn)
            if current and state.get("handoff_id"):
                self.specialist.complete(sid, current["handoff_id"], next_handoff_id=state["handoff_id"])
            response = self.turn_response(self.session(sid), state, engine.responder)
            with self.db.transaction() as c:
                self.save_turn(c, session, turn, state, response)
            return response

    def turn_response(self, session, state, responder):
        return {
            "session_id": session["id"],
            "identity_status": self.identity_status(session),
            "user_id": self.user_id(session["customer_id"]) if self.can_chat(session) else None,
            "reply": state.get("reply", "Em đã tiếp nhận yêu cầu."),
            "used_fact_ids": state.get("used_fact_ids", []),
            "guard_events": state.get("guard_events", []),
            "handoff_id": state.get("handoff_id"),
            **self.specialist.metadata(session["id"]),
            "responder": responder,
            "status": session["status"],
        }

    def save_turn(self, conn, session, turn, state, response):
        trace = {
            "turn_id": turn,
            "responder": response["responder"],
            "specialist_id": response.get("specialist_id"),
            "specialist_prompt_version": PROMPT_VERSION if response["responder"] == "ai_specialist" else None,
            "context_parts": state.get("context_parts", []),
            "facts": [{**f, "customer_id": session["customer_id"],
                       "observed_at": f.get("observed_at", self.db.setting("clock"))}
                      for f in state.get("facts", {}).values()],
            "source_spans": state.get("source_spans", []),
            "guard_events": response["guard_events"],
            "tool_calls": state.get("tool_calls", []),
            "plan": state.get("plan", {}),
            "business_as_of": self.db.setting("clock"),
            "backend": self.settings.llm_backend,
            "revision": self.settings.revision,
            "memory_result": state.get("memory_result", {}),
            "profile_context": {k: v for k, v in state.get("profile_context", {}).items() if k != "name"},
        }
        logger.info("profile_context %s", canonical({
            "turn_id": turn, "revision": self.settings.revision,
            "identity_status": response["identity_status"], "intent": trace["plan"].get("intent"),
            "profile_status": trace["profile_context"].get("status"),
            "name_source": trace["profile_context"].get("source"),
            "has_name_fact": any(f["key"] == "profile_name" for f in trace["facts"]),
        }))
        conn.execute("UPDATE turns SET reply=?,result=? WHERE id=?", (response["reply"], canonical(response), turn))
        conn.execute("INSERT OR REPLACE INTO traces VALUES(?,?,?)", (turn, session["id"], canonical(trace)))

    def handoff(self, sid, reason):
        with self.engine.lock(self.session(sid)["customer_id"]):
            return self._handoff(sid, reason)

    def _handoff(self, sid, reason):
        session = self.session(sid)
        if not self.can_chat(session):
            raise HTTPException(403, "Verify identity first")
        previous = self.db.one(
            "SELECT * FROM handoffs WHERE session_id=? AND status IN ('pending','completing') ORDER BY created_at DESC LIMIT 1", (sid,)
        )
        if previous:
            return self.handoff_detail(previous)
        response = self.message(
            sid,
            "Yêu cầu gặp người thật: " + reason,
            "handoff-request:" + str(self.db.one("SELECT count(*) AS n FROM handoffs WHERE session_id=?", (sid,))["n"]),
        )
        if any(e.get("stage") == "input" and e["action"] == "block" for e in response["guard_events"]):
            raise HTTPException(400, response["reply"])
        if not response.get("handoff_id"):
            raise HTTPException(503, response["reply"])
        return self.handoff_detail(
            self.db.one("SELECT * FROM handoffs WHERE id=?", (response["handoff_id"],))
        )

    @staticmethod
    def handoff_detail(row):
        if not row:
            raise HTTPException(404, "Handoff not found")
        data = json.loads(row["summary"])
        data["status"] = row["status"]
        data.setdefault("target", "human")
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
        for row in self.db.all("SELECT session_id,handoff_id FROM specialist_handoffs"):
            self.engine.checkpointer.delete_thread(f"specialist:{row['session_id']}:{row['handoff_id']}")
        tables = [
            "specialist_sessions",
            "specialist_handoffs",
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
