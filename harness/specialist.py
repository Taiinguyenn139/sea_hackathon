"""Durable specialist lifecycle. All entry points use the shared customer lock."""

import json

from fastapi import HTTPException

from bank_tools.service import canonical, ident, now
from harness.agent.specialist import PROMPT_VERSION, SpecialistEngine
from harness.input_guard import inspect_input
from harness.llm import LLMUnavailable
from harness.streaming import emit


def requests_human(text):
    return any(x in text.casefold() for x in ("người thật", "nhân viên thật", "khiếu nại", "nộp hồ sơ", "ký hợp đồng"))


class Specialist:
    def __init__(self, harness):
        self.harness = harness
        self.db = harness.db
        self.settings = harness.settings
        self.engine = SpecialistEngine(self.db, self.settings, shared=harness.engine)

    def current(self, sid):
        return self.db.one(
            "SELECT m.*,h.status FROM specialist_sessions s JOIN specialist_handoffs m "
            "ON m.handoff_id=s.handoff_id JOIN handoffs h ON h.id=m.handoff_id WHERE s.session_id=?", (sid,)
        )

    def metadata(self, sid):
        current = self.current(sid)
        return {"responder": "ai_specialist" if current else "assistant",
                "specialist_id": current["handoff_id"] if current else None,
                "specialist_status": current["status"] if current else None,
                "specialist_available": self.settings.ai_specialist_enabled}

    def start(self, sid, reason, key, *, input_checked=False, welcome_turn=None, resume_from_human=False):
        h = self.harness
        session = h.session(sid)
        with h.engine.lock(session["customer_id"]):
            session = h.session(sid)
            previous = self.db.one(
                "SELECT * FROM specialist_handoffs WHERE session_id=? AND start_key=?", (sid, key)
            )
            if previous:
                if previous["reason"] != reason:
                    raise HTTPException(409, "Specialist request key already used")
                if previous["start_result"]:
                    return json.loads(previous["start_result"])
            if session["status"] == "waiting_human" and resume_from_human:
                if h.identity_status(session) not in ("verified", "identified"):
                    raise HTTPException(403, "Verify identity first")
                if not self.settings.ai_specialist_enabled:
                    raise HTTPException(409, "Tư vấn chuyên sâu chưa được bật.")
                blocked = inspect_input(reason, self.settings, semantic=not input_checked)
                if blocked:
                    raise HTTPException(400, blocked["reply"])
                pending = self.db.one(
                    "SELECT * FROM handoffs WHERE session_id=? AND status IN ('pending','completing') "
                    "ORDER BY created_at DESC LIMIT 1", (sid,),
                )
                if not pending:
                    raise HTTPException(409, "Không tìm thấy yêu cầu nhân viên đang chờ.")
                h.engine.complete(pending, {
                    "notes": "Khách chọn chuyên viên AI tiếp nhận; chưa có nhân viên thật tư vấn.",
                    "commitments": [],
                })
                session = h.session(sid)
                input_checked = True
            if session["status"] != "active":
                raise HTTPException(409, "Session is waiting for staff or ended")
            if h.identity_status(session) not in ("verified", "identified"):
                raise HTTPException(403, "Verify identity first")
            current = self.current(sid)
            if current and (not previous or current["handoff_id"] != previous["handoff_id"]):
                raise HTTPException(409, "Chuyên viên đã tiếp nhận phiên này. Vui lòng tiếp tục hội thoại.")
            if not previous:
                if not self.settings.ai_specialist_enabled:
                    raise HTTPException(409, "Tư vấn chuyên sâu chưa được bật.")
                blocked = inspect_input(reason, self.settings, semantic=not input_checked)
                if blocked:
                    raise HTTPException(400, blocked["reply"])
                hid = ident("sp_", [sid, key])
                tid = welcome_turn or ident("sp_welcome_", [hid])
                snapshot = h.engine.handoff_snapshot(session, reason, hid)
                snapshot.update(target="ai_specialist", prompt_version=PROMPT_VERSION, status="initializing")
                with self.db.transaction() as c:
                    c.execute("INSERT INTO handoffs VALUES(?,?,?,?,?,?)",
                              (hid, sid, "initializing", canonical(snapshot), None, now()))
                    c.execute(
                        "INSERT INTO specialist_handoffs VALUES(?,?,?,?,?,?,NULL,NULL)",
                        (hid, sid, key, reason, PROMPT_VERSION, tid),
                    )
                    c.execute("INSERT INTO specialist_sessions VALUES(?,?)", (sid, hid))
                    # Empty customer text is an assistant-only event, not customer memory.
                    if not welcome_turn:
                        c.execute("INSERT INTO turns VALUES(?,?,?,?,?,?,?)",
                                  (tid, sid, "specialist-welcome:" + hid, "", None, None, now()))
                previous = self.db.one("SELECT * FROM specialist_handoffs WHERE handoff_id=?", (hid,))
            if self.settings.llm_backend != "openai":
                raise LLMUnavailable("specialist_provider_unavailable")
            hid, tid = previous["handoff_id"], previous["welcome_turn_id"]
            # A cancelled initialization cannot be revived by replaying its old key.
            record = self.db.one("SELECT status FROM handoffs WHERE id=?", (hid,))
            if record["status"] == "completed":
                raise HTTPException(409, "Yêu cầu chuyên viên này đã kết thúc.")
            session.update(specialist_id=hid, specialist_welcome=True)
            emit("metadata", responder="ai_specialist")
            state = self.engine.run(session, "Tiếp nhận hồ sơ và tiếp nối cuộc tư vấn.", tid)
            if state.get("llm_unavailable"):
                raise LLMUnavailable("specialist_welcome_unavailable")
            response = h.turn_response(session, state, "ai_specialist")
            response.update(specialist_id=hid, specialist_status="in_progress", status="active")
            with self.db.transaction() as c:
                h.save_turn(c, session, tid, state, response)
                c.execute("UPDATE handoffs SET status='in_progress' WHERE id=?", (hid,))
                c.execute("UPDATE specialist_handoffs SET start_result=? WHERE handoff_id=?",
                          (canonical(response), hid))
            return response

    def complete(self, sid, hid, *, next_handoff_id=None):
        h = self.harness
        session = h.session(sid)
        with h.engine.lock(session["customer_id"]):
            record = self.db.one(
                "SELECT h.*,m.welcome_turn_id FROM handoffs h JOIN specialist_handoffs m "
                "ON m.handoff_id=h.id WHERE h.id=? AND h.session_id=?", (hid, sid)
            )
            if not record:
                raise HTTPException(404, "Specialist handoff not found")
            if record["status"] == "completed":
                return json.loads(record["receipt"])
            current = self.current(sid)
            if not current or current["handoff_id"] != hid:
                raise HTTPException(409, "Specialist handoff is not active")
            # Grounded extractive receipt: no LLM dependency, invented commitments or memory writes.
            turns = self.db.all(
                "SELECT id,reply,result FROM turns WHERE session_id=? AND rowid >= "
                "(SELECT rowid FROM turns WHERE id=?) AND result IS NOT NULL ORDER BY rowid",
                (sid, record["welcome_turn_id"]),
            )
            discussed = []
            for turn in turns:
                result = json.loads(turn["result"])
                if result.get("responder") == "ai_specialist":
                    discussed.append({"turn_id": turn["id"], "reply": turn["reply"],
                                      "fact_ids": result.get("used_fact_ids", [])})
            facts = h.engine.memory.facts(session["customer_id"])
            receipt = {
                "handoff_id": hid, "status": "completed", "responder": "assistant", "specialist_id": None,
                "notes": "Đã lưu nội dung tư vấn chuyên sâu." if discussed else "Đã kết thúc yêu cầu tiếp nhận.",
                "discussed": discussed,
                "active_customer_fact_ids": [f["id"] for f in facts if f["source_type"] == "customer"
                                             and f["status"] == "active"],
                "open_questions": sorted({f["key"] for f in facts if f["status"] == "conflicted"}),
                "memory_pending": bool(self.db.one(
                    "SELECT 1 FROM memory_jobs WHERE customer_id=? AND status!='processed'",
                    (session["customer_id"],))),
                "commitments": [],
                "action_receipts": self.db.all(
                    "SELECT id,tool,result FROM tool_calls WHERE session_id=? AND tool IN "
                    "('schedule_callback','send_summary') AND created_at>=? ORDER BY rowid",
                    (sid, record["created_at"])),
                "next_handoff_id": next_handoff_id,
                "next_step": "Chờ nhân viên tiếp nhận." if next_handoff_id else "Tiếp tục với trợ lý.",
            }
            with self.db.transaction() as c:
                c.execute("UPDATE handoffs SET status='completed',receipt=? WHERE id=?", (canonical(receipt), hid))
                c.execute("UPDATE specialist_handoffs SET next_handoff_id=? WHERE handoff_id=?",
                          (next_handoff_id, hid))
                c.execute("DELETE FROM specialist_sessions WHERE session_id=? AND handoff_id=?", (sid, hid))
            return receipt

    def recover(self):
        for row in self.db.all("SELECT * FROM specialist_sessions"):
            session = self.harness.session(row["session_id"])
            if session["status"] == "waiting_human":
                human = self.db.one(
                    "SELECT id FROM handoffs WHERE session_id=? AND status IN ('pending','completing')",
                    (session["id"],),
                )
                if not human:
                    raise RuntimeError("Human handoff missing during specialist transfer")
                self.complete(session["id"], row["handoff_id"], next_handoff_id=human["id"])
            elif session["status"] == "ended":
                self.complete(session["id"], row["handoff_id"])
            # initializing is retried on the next start/message; no provider call at startup.
