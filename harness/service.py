"""Application orchestration shared by web, Telegram and benchmark clients."""

import hashlib
import json
import secrets
import time

from fastapi import HTTPException

from bank_tools.service import canonical, ident, now
from harness.agent.engine import Engine, event
from harness.identity import extract_phone_claims, get_profile, normalize_phone, validate_own_phone_claim
from harness.input_guard import inspect_input
from harness.llm import LLMUnavailable, completion_budget
from harness.phone_identity import PhoneIdentity


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Harness:
    def __init__(self, db, settings):
        self.db = db
        self.settings = settings
        self.engine = Engine(db, settings)
        self.engine.identity_handler = self.agent_verify
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

    def new_session(self, profile=None, mode="harness", channel="web", external_id=None):
        identity = get_profile(profile)
        if identity:
            profile = identity["id"]
        guest = channel == "telegram" and self.db.one(
            "SELECT id FROM guest_customers WHERE id=? AND channel=? AND external_id=?",
            (profile, channel, external_id),
        ) is not None
        if profile is not None and not identity and not guest:
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
        if profile is not None:
            self.engine.memory.flush(profile)
        sid = "s_" + secrets.token_hex(12)
        token = secrets.token_urlsafe(32)
        profile = profile or "anon_" + sid
        kind = "otp" if phone_authenticated else "anonymous" if profile.startswith("anon_") else "admin" if channel == "web" else "channel"
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO sessions(id,customer_id,channel,external_id,mode,token_hash,created_at) VALUES(?,?,?,?,?,?,?)",
                (sid, profile, channel, external_id or sid, mode, token_hash(token), now()),
            )
            c.execute("INSERT INTO session_identity_modes VALUES(?,?)", (sid, kind))
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

    def new_conversation(self, previous):
        """Create a sibling transcript without ending the previous conversation.

        A fresh public demo chat can choose another identity in the agent. A
        capability to an OTP/channel-owned customer retains that customer's
        independent continuity flow; neither path grants customer CRM login.
        """
        with self.engine.lock("session:" + previous["id"]):
            previous = self.prepare_public_session(previous["id"])
            mode = self.db.one("SELECT kind FROM session_identity_modes WHERE session_id=?", (previous["id"],))
            kind = mode["kind"] if mode else "legacy"
            if (kind == "legacy" and previous["channel"] == "web" and get_profile(previous["customer_id"])
                    and not self.db.one("SELECT session_id FROM phone_sessions WHERE session_id=?", (previous["id"],))):
                kind = "demo"
            if kind in ("anonymous", "demo") or not self.can_chat(previous) and kind != "admin":
                return self._create_session(None, previous["mode"])
            if kind == "admin":
                result = self._create_session(previous["customer_id"], previous["mode"])
                if previous["verified"]:
                    self.db.execute("UPDATE sessions SET verified=1 WHERE id=?", (result["session_id"],))
                    result["identity_status"] = "verified"
                    result["brief"] = self.engine.memory.brief(previous["customer_id"], flush=False)
                return result
            # Preserve the main branch's delegated capability for a channel or
            # OTP identity. This does not run PhoneIdentity.bind or claim a new
            # phone number, and cannot change customer_id.
            return self._create_session(
                previous["customer_id"], previous["mode"], phone_authenticated=bool(self.can_chat(previous)),
            )

    def prepare_public_session(self, sid):
        """Detach legacy web preselection, retaining only its public transcript."""
        with self.engine.lock("session:" + sid):
            session = self.session(sid)
            mode = self.db.one("SELECT kind FROM session_identity_modes WHERE session_id=?", (sid,))
            otp = self.db.one("SELECT session_id FROM phone_sessions WHERE session_id=?", (sid,))
            if (mode or otp or session["channel"] != "web" or session["verified"]
                    or session["status"] != "active" or not get_profile(session["customer_id"])):
                return session
            with self.db.transaction() as c:
                c.execute(
                    "UPDATE sessions SET customer_id=? WHERE id=? AND verified=0 AND status='active'",
                    ("anon_" + sid, sid),
                )
                c.execute("INSERT INTO session_identity_modes VALUES(?,?)", (sid, "anonymous"))
                c.execute("DELETE FROM agent_runs WHERE session_id=?", (sid,))
            self.engine.checkpointer.delete_thread(sid)
            with self.engine.bank.lock:
                for key in list(self.engine.bank.clients):
                    if key[0] == sid:
                        del self.engine.bank.clients[key]
            return self.session(sid)

    def _identity_result(self, operation_id, *, error=None):
        return {
            "tool_call_id": operation_id, "tool": "verify_identity", "as_of": self.db.setting("clock"),
            "data": {"verified": False, "identity_status": "unverified", "user_id": None, "brief": {"redacted": True}},
            "facts": [], "error": error,
        }

    def _complete_identity_brief(self, session_id, result):
        if result["data"].get("verified") and result["data"].get("brief", {}).get("pending"):
            session = self.session(session_id)
            with self.engine.lock(session["customer_id"]):
                result["data"]["brief"] = self.engine.memory.brief(session["customer_id"], flush=False)
            self.db.execute("UPDATE tool_calls SET result=? WHERE id=?", (canonical(result), result["tool_call_id"]))
        return result

    def agent_verify(self, session_id, phone, *, text, source, turn_id, operation_id):
        """Tool handler: bind an anonymous fixture session from the current own claim."""
        turn = self.db.one("SELECT session_id,text FROM turns WHERE id=?", (turn_id,))
        if not turn or turn["session_id"] != session_id or turn["text"] != text:
            return self._identity_result(operation_id, error="forbidden: identity claim must belong to this turn")
        return self._identity_operation(session_id, phone, text, source, turn_id, operation_id)

    def _identity_operation(self, sid, phone, text, source, turn_id, operation_id):
        with self.engine.lock("session:" + sid):
            # The normalized phone and current-message hash make a retry receipt
            # specific to the original request, without storing plaintext phone arguments.
            try:
                canonical_phone = normalize_phone(phone)
            except (TypeError, ValueError):
                canonical_phone = str(phone)
            request_hash = ident("identity_", [canonical_phone, text, turn_id])
            previous = self.db.one("SELECT session_id,tool,args,result FROM tool_calls WHERE id=?", (operation_id,))
            if previous:
                args = json.loads(previous["args"])
                if previous["session_id"] != sid or previous["tool"] != "verify_identity" or args.get("request_hash") != request_hash:
                    return self._identity_result(operation_id, error="conflict: identity operation reused for another request")
                return self._complete_identity_brief(sid, json.loads(previous["result"]))
            session = self.session(sid)
            result = self._identity_result(operation_id)
            target = None
            try:
                if session["status"] != "active":
                    raise ValueError("forbidden: identity session must be active")
                normalized = validate_own_phone_claim(text, phone)
                target = get_profile(normalized)
                if not target:
                    raise ValueError("identity_not_found: Chưa tìm thấy hồ sơ demo khớp số điện thoại.")
                mode = self.db.one("SELECT kind FROM session_identity_modes WHERE session_id=?", (sid,))
                mode = mode["kind"] if mode else "legacy"
                bound = get_profile(session["customer_id"])
                anonymous = mode == "anonymous" and session["customer_id"].startswith("anon_")
                pinned = mode in ("admin", "channel", "legacy") and bound is not None
                if session["verified"]:
                    if not bound or bound["id"] != target["id"]:
                        raise ValueError("identity_locked: Hồ sơ đã được nhận diện; hãy bắt đầu chat mới để dùng hồ sơ khác.")
                elif not anonymous and not (pinned and bound["id"] == target["id"]):
                    raise ValueError("identity_mismatch: Không chuyển hồ sơ đã gắn hoặc hồ sơ OTP bằng lời khai phone demo.")
            except (TypeError, ValueError) as exc:
                result["error"] = str(exc)
            current = time.time()
            identity_key = session["customer_id"] if get_profile(session["customer_id"]) else "demo-phone:" + token_hash(canonical_phone)
            with self.db.transaction() as c:
                n = c.execute(
                    "SELECT count(*) FROM auth_attempts WHERE at>? AND (identity=? OR source=?)",
                    (current - 900, identity_key, source),
                ).fetchone()[0]
                if n >= 5:
                    result["error"] = "rate_limited: Vui lòng thử nhận diện lại sau ít phút."
                elif not session["verified"]:
                    c.execute("INSERT INTO auth_attempts VALUES(?,?,?)", (identity_key, source, current))
                if not result["error"]:
                    changed = c.execute(
                        "UPDATE sessions SET customer_id=?,verified=1 WHERE id=? AND customer_id=? AND status='active'",
                        (target["id"], sid, session["customer_id"]),
                    ).rowcount
                    if not changed:
                        result["error"] = "identity_changed: Phiên đã thay đổi; vui lòng thử lại."
                    else:
                        c.execute("INSERT OR REPLACE INTO session_identity_modes VALUES(?,?)", (sid, "demo"))
                        c.execute("DELETE FROM auth_attempts WHERE identity=? AND source=? AND at=?", (identity_key, source, current))
                        result["data"] = {
                            "verified": True, "identity_status": "verified", "user_id": target["user_id"],
                            "brief": {"redacted": True, "pending": True},
                        }
                c.execute("INSERT INTO tool_calls VALUES(?,?,?,?,?,?)", (
                    operation_id, sid, "verify_identity",
                    canonical({"phone": "[redacted]", "as_of": result["as_of"], "request_hash": request_hash}),
                    canonical(result), now(),
                ))
            return self._complete_identity_brief(sid, result)

    def verify(self, session, phone, source):
        """Compatibility endpoint; new UI uses the agent tool inside chat."""
        with self.engine.lock("session:" + session["id"]):
            session = self.prepare_public_session(session["id"])
            if session["status"] == "ended":
                raise HTTPException(409, "Session ended")
            operation_id = ident("tc_", [session["id"], "direct-identity", now()])
            result = self._identity_operation(
                session["id"], phone, "Số điện thoại của tôi là " + phone, source, None, operation_id,
            )
            if result["error"] and result["error"].startswith("rate_limited"):
                raise HTTPException(429, "Vui lòng thử nhận diện lại sau ít phút.")
            return {**result["data"], "tool_call_id": result["tool_call_id"], "message": result["error"] or "Đã nhận diện hồ sơ demo."}

    def _phone_message(self, session, text, client_id, existing, phones, source):
        """Handle screened fixture claims before financial extraction or the agent."""
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
            receipt = self._identity_operation(
                session["id"], phones[0], text, source, None,
                ident("tc_", [session["id"], client_id, "legacy-identity"]),
            )
            if receipt["error"] and receipt["error"].startswith("rate_limited"):
                raise HTTPException(429, "Vui lòng thử nhận diện lại sau ít phút.")
            result = {**receipt["data"], "message": receipt["error"], "tool_call_id": receipt["tool_call_id"]}
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

    def message(self, sid, text, client_id, source="web", *, input_checked=False):
        with completion_budget(self.settings.agent_turn_budget_seconds):
            return self._message(sid, text, client_id, source, input_checked=input_checked)

    def _message(self, sid, text, client_id, source="web", *, input_checked=False):
        session = self.session(sid)
        with self.engine.lock("session:" + sid):
            session = self.prepare_public_session(sid)
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
                }
            phones = extract_phone_claims(text)
            origin = self.db.one("SELECT kind FROM session_identity_modes WHERE session_id=?", (sid,))
            if phones and self.settings.llm_backend == "rules" and origin and origin["kind"] in ("admin", "channel"):
                return self._phone_message(session, text, client_id, existing, phones, source)
            turn = existing["id"] if existing else ident("t_", [sid, client_id])
            if not existing:
                with self.db.transaction() as c:
                    c.execute(
                        "INSERT INTO turns VALUES(?,?,?,?,?,?,?)", (turn, sid, client_id, text, None, None, now())
                    )
                    if self.can_chat(session):
                        self.engine.memory.enqueue(c, turn, session["customer_id"], sid, session["channel"], text)
            state = self.engine.run(session, text, turn, source=source)
            session = self.session(sid)
            if self.can_chat(session):
                with self.engine.lock(session["customer_id"]), self.db.transaction() as c:
                    self.engine.memory.enqueue(c, turn, session["customer_id"], sid, session["channel"], text)
            response = {
                "session_id": sid,
                "identity_status": self.identity_status(session),
                "user_id": self.user_id(session["customer_id"]) if self.can_chat(session) else None,
                "reply": state.get("reply", "Em đã tiếp nhận yêu cầu."),
                "used_fact_ids": state.get("used_fact_ids", []),
                "guard_events": state.get("guard_events", []),
                "handoff_id": state.get("handoff_id"),
            }
            if self.can_chat(session):
                with self.engine.lock(session["customer_id"]):
                    if self.identity_status(session) == "identified":
                        try:
                            # Preserve OTP's immediate update within the shared
                            # budget; demo/channel memory remains asynchronous.
                            self.engine.memory.flush(session["customer_id"])
                        except LLMUnavailable:
                            # Persist completed tools even if extraction waits.
                            pass
                    response["brief"] = self.engine.memory.brief(session["customer_id"], flush=False)
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
        response = self.message(
            sid,
            "Yêu cầu chuyên viên: " + reason,
            "handoff-request:" + str(self.db.one("SELECT count(*) AS n FROM handoffs WHERE session_id=?", (sid,))["n"]),
        )
        if any(e.get("stage") == "input" and e["action"] == "block" for e in response["guard_events"]):
            raise HTTPException(400, response["reply"])
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
            "agent_runs",
            "agent_tool_receipts",
            "session_identity_modes",
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
