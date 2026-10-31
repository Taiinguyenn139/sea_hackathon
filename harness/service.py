"""Application orchestration shared by web, Telegram and benchmark clients."""

import hashlib
import json
import secrets
import time

from fastapi import HTTPException

from bank_tools.service import FIXTURES, canonical, ident, now
from harness.agent.engine import Engine, event


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Harness:
    def __init__(self, db, settings):
        self.db = db
        self.settings = settings
        self.engine = Engine(db, settings)

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
        ) is not None

    def new_session(self, profile, mode="harness", channel="web", external_id=None):
        guest = channel == "telegram" and self.db.one(
            "SELECT id FROM guest_customers WHERE id=? AND channel=? AND external_id=?",
            (profile, channel, external_id),
        ) is not None
        if profile not in {p["id"] for p in FIXTURES["customers"]} and not guest:
            raise HTTPException(400, "Unknown demo profile")
        self.engine.memory.flush(profile)
        sid = "s_" + secrets.token_hex(12)
        token = secrets.token_urlsafe(32)
        self.db.execute(
            "INSERT INTO sessions(id,customer_id,channel,external_id,mode,token_hash,created_at) VALUES(?,?,?,?,?,?,?)",
            (sid, profile, channel, external_id or sid, mode, token_hash(token), now()),
        )
        return {
            "session_id": sid,
            "session_token": token,
            "identity_status": "guest" if guest else "unverified",
            "brief": {"redacted": True},
        }

    def session(self, sid):
        session = self.db.one("SELECT * FROM sessions WHERE id=?", (sid,))
        if not session:
            raise HTTPException(404, "Session not found")
        return session

    def verify(self, session, dob, source):
        if session["status"] == "ended":
            raise HTTPException(409, "Session ended")
        current = time.time()
        identity = session["customer_id"]
        # Customer-scoped cap prevents parallel-session bypass; IP cap protects the verifier itself.
        with self.db.transaction() as c:
            n = c.execute(
                "SELECT count(*) FROM auth_attempts WHERE at>? AND (identity=? OR source=?)",
                (current - 900, identity, source),
            ).fetchone()[0]
            if n >= 5:
                raise HTTPException(429, "Vui lòng thử xác minh lại sau ít phút.")
            c.execute("INSERT INTO auth_attempts VALUES(?,?,?)", (identity, source, current))
        result = self.engine.bank.call("verify_identity", {"dob": dob}, identity, session["id"])
        if not result["data"].get("verified"):
            return {
                "identity_status": "unverified",
                "brief": {"redacted": True},
                "message": "Chưa thể xác minh thông tin.",
            }
        with self.db.transaction() as c:
            c.execute("DELETE FROM auth_attempts WHERE identity=? AND source=? AND at=?", (identity, source, current))
            c.execute("UPDATE sessions SET verified=1 WHERE id=?", (session["id"],))
        return {"identity_status": "verified", "brief": self.engine.memory.brief(identity)}

    def message(self, sid, text, client_id):
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
            if not self.can_chat(session):
                return {
                    "session_id": sid,
                    "reply": "Để tiếp tục, anh/chị vui lòng xác minh ngày sinh trong bước xác minh.",
                    "used_fact_ids": [],
                    "guard_events": [event("G7", "identity_unverified", "verify")],
                    "handoff_id": None,
                }
            turn = existing["id"] if existing else ident("t_", [sid, client_id])
            if not existing:
                with self.db.transaction() as c:
                    c.execute(
                        "INSERT INTO turns VALUES(?,?,?,?,?,?,?)", (turn, sid, client_id, text, None, None, now())
                    )
                    self.engine.memory.enqueue(c, turn, session["customer_id"], sid, session["channel"], text)
            state = self.engine.run(session, text, turn)
            response = {
                "session_id": sid,
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
            "sessions",
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
