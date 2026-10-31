import json
import re
import sqlite3
import threading
from decimal import Decimal
from pathlib import Path
from typing import TypedDict

import yaml
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from bank_tools.service import BankTools, canonical, ident, now
from harness.llm import DraftSuggestion, PlanSuggestion, json_completion
from harness.memory.service import Memory, extract


class TurnState(TypedDict, total=False):
    session: dict
    turn_id: str
    text: str
    known: dict
    facts: dict
    context_parts: list
    plan: dict
    draft: str
    reply: str
    guard_events: list
    disclosures: list
    source_spans: list
    used_fact_ids: list
    tool_calls: list
    handoff_id: str | None
    attempt: int
    tool_error: bool
    unsuitable: bool
    completion: dict
    confirmations: list
    cards: list


def event(guard, reason, action="fallback", attempt=0):
    return {"guard_id": guard, "reason": reason, "action": action, "stage": "guard", "attempt": attempt, "fact_ids": []}


def draft_problem(draft, facts, as_of):
    ids = re.findall(r"\{fact:([^}]+)\}", draft)
    stripped = re.sub(r"\{fact:[^}]+\}", "", draft)
    written_number = re.search(
        r"\b(?:không|một|hai|ba|bốn|năm|sáu|bảy|tám|chín|mười|trăm|nghìn)(?:\s+[a-zà-ỹ]+){0,5}\s+(?:phần trăm|tỷ|triệu|đồng|tháng|năm)\b",
        stripped.lower(),
    )
    invalid = bool(re.search(r"\d", stripped) or written_number or "{fact:" in stripped) or any(
        i not in facts for i in ids
    )
    stale = any(
        i in facts
        and (
            (facts[i].get("valid_until") and facts[i]["valid_until"] < as_of)
            or facts[i].get("status") in ("expired", "stale", "superseded", "conflicted")
        )
        for i in ids
    )
    return invalid, stale


def format_fact(f):
    def numeric(value):
        text = format(Decimal(str(value)), "f")
        return (text.rstrip("0").rstrip(".") if "." in text else text).replace(".", ",")

    v = str(f["value"])
    u = f.get("unit", "")
    if u == "đồng":
        d = Decimal(v)
        if d >= 10**9 and d % 10**8 == 0:
            return numeric(d / 10**9) + " tỷ đồng"
        if d >= 10**6 and d % 10**5 == 0:
            return numeric(d / 10**6) + " triệu đồng"
        return f"{int(d):,}".replace(",", ".") + " đồng"
    return v.replace(".", ",") + (" " + u if u else "")


class Engine:
    def __init__(self, db, settings):
        self.db = db
        self.settings = settings
        self.memory = Memory(db, settings)
        self.bank = BankTools(db)
        self.locks = {}
        self.locks_lock = threading.Lock()
        cp_path = ":memory:" if db.path == ":memory:" else db.path + ".checkpoints"
        self.cp_conn = sqlite3.connect(cp_path, check_same_thread=False)
        self.checkpointer = SqliteSaver(self.cp_conn)
        graph = StateGraph(TurnState)
        for name in [
            "load_context",
            "plan",
            "select_cards",
            "call_tools",
            "draft",
            "guard",
            "render",
            "handoff_prepare",
            "wait_human",
        ]:
            graph.add_node(name, getattr(self, name))
        graph.set_entry_point("load_context")
        graph.add_edge("load_context", "plan")
        graph.add_conditional_edges("plan", lambda s: "handoff_prepare" if s["plan"].get("handoff") else "select_cards")
        graph.add_edge("select_cards", "call_tools")
        graph.add_edge("call_tools", "draft")
        graph.add_edge("draft", "guard")
        graph.add_edge("guard", "render")
        graph.add_edge("render", END)
        graph.add_edge("handoff_prepare", "wait_human")
        graph.add_edge("wait_human", END)
        self.graph = graph.compile(checkpointer=self.checkpointer)

    def lock(self, key):
        with self.locks_lock:
            return self.locks.setdefault(key, threading.RLock())

    def run(self, session, text, turn_id):
        with self.lock(session["customer_id"]):
            return self.graph.invoke(
                {
                    "session": session,
                    "text": text,
                    "turn_id": turn_id,
                    "guard_events": [],
                    "tool_calls": [],
                    "handoff_id": None,
                    "attempt": 0,
                    "tool_error": False,
                    "unsuitable": False,
                    "source_spans": [],
                    "used_fact_ids": [],
                    "disclosures": [],
                },
                {"configurable": {"thread_id": session["id"]}},
            )

    def load_context(self, s):
        session = s["session"]
        customer = session["customer_id"]
        job = self.db.one("SELECT seq FROM memory_jobs WHERE turn_id=?", (s["turn_id"],))
        self.memory.flush(customer, job["seq"] - 1 if job else None)
        current = self.db.all(
            "SELECT id,text,reply FROM turns WHERE session_id=? ORDER BY created_at,id", (session["id"],)
        )
        known = {}
        facts = {}
        parts = []
        confirmations = []
        if session["mode"] == "harness":
            for f in self.memory.facts(customer):
                if f["status"] == "conflicted" and f["key"] not in extract(s["text"]):
                    confirmations.append(f["key"])
                if f["source_type"] == "customer" and f["status"] == "active":
                    known[f["key"]] = f["value"]
                    facts[f["id"]] = f
            parts.append({"type": "call_brief", "fact_ids": list(facts)})
        elif session["mode"] == "full_history":
            old = self.db.all(
                "SELECT t.id,t.text,t.reply FROM turns t JOIN sessions s ON s.id=t.session_id WHERE s.customer_id=? AND s.id<>? ORDER BY t.created_at,t.id",
                (customer, session["id"]),
            )
            for t in old:
                for k, (v, u) in extract(t["text"]).items():
                    known[k] = v
                    fid = ident("cf_", [t["id"], k, v])
                    facts[fid] = {
                        "id": fid,
                        "key": k,
                        "value": v,
                        "unit": u,
                        "source_type": "customer",
                        "source_ref": t["id"],
                        "status": "active",
                        "valid_until": None,
                    }
            parts.append({"type": "old_transcript", "turns": old})
        remembered_keys = set(known) if session["mode"] == "harness" else set()
        for t in current:
            for k, (v, u) in extract(t["text"]).items():
                # Shared memory includes newer updates from other channels.
                # An older turn in this session must not replace those values.
                if k in remembered_keys:
                    continue
                known[k] = v
                fid = ident("cf_", [t["id"], k, v])
                facts = {i: f for i, f in facts.items() if f["key"] != k}
                facts[fid] = {
                    "id": fid,
                    "key": k,
                    "value": v,
                    "unit": u,
                    "source_type": "customer",
                    "source_ref": t["id"],
                    "status": "active",
                    "valid_until": None,
                }
        parts.append({"type": "current_session", "turns": current})
        for k, (v, u) in extract(s["text"]).items():
            known[k] = v
            facts = {i: f for i, f in facts.items() if f["key"] != k}
            fid = ident("cf_", [s["turn_id"], k, v])
            facts[fid] = {
                "id": fid,
                "key": k,
                "value": v,
                "unit": u,
                "source_type": "customer",
                "source_ref": s["turn_id"],
                "status": "active",
                "valid_until": None,
            }
        return {"known": known, "facts": facts, "context_parts": parts, "confirmations": sorted(set(confirmations))}

    def plan(self, s):
        low = s["text"].lower()
        known = s["known"]
        product = known.get("product", "home_loan")
        handoff = any(t in low for t in ["chuyên viên", "gặp người", "khiếu nại", "nộp hồ sơ", "ký hợp đồng"])
        suggestion = json_completion(
            self.settings,
            "Phân loại hội thoại tư vấn. Trả JSON {stage:discover|quote|objection|followup|apply, objection:rate_high|need_spouse|null}. Không trả số liệu hoặc tool arguments.",
            {"text": s["text"], "known": known},
            schema=PlanSuggestion,
        )
        stage = "apply" if handoff else "discover"
        objection = known.get("objection")
        if suggestion:
            if suggestion.get("stage") in ("discover", "quote", "objection", "followup", "apply"):
                stage = suggestion["stage"]
            if suggestion.get("objection") in ("rate_high", "need_spouse"):
                objection = suggestion["objection"]
            handoff = handoff or stage == "apply"
        slots = ["income"] if product == "credit_card" else ["amount", "term", "income"]
        missing = [k for k in slots if k not in known]
        return {
            "plan": {
                "product": product,
                "handoff": handoff,
                "missing": missing,
                "callback": any(x in low for x in ["gọi lại", "hẹn", "liên hệ lại"]),
                "summary": any(x in low for x in ["gửi tóm tắt", "gửi tổng kết"]),
                "objection": objection,
                "stage": stage,
            }
        }

    def select_cards(self, s):
        version = self.db.setting("playbook", "v1")
        path = Path(__file__).parent.parent / "playbooks" / (version + ".yaml")
        if not path.is_file():
            raise RuntimeError("Configured playbook unavailable")
        book = yaml.safe_load(path.read_text())
        candidates = book.get("cards", {}).get(s["plan"].get("objection"), {}).get("retail", [])
        if not candidates:
            return {"cards": []}
        index = int(ident("", [s["session"]["id"], version]), 16) % len(candidates)
        return {"cards": [candidates[index]]}

    def call_tools(self, s):
        p = s["plan"]
        known = s["known"]
        facts = dict(s["facts"])
        calls = []
        disclosures = []
        error = False
        unsuitable = False
        session = s["session"]
        product = p["product"]

        def call(name, args):
            nonlocal error
            result = self.bank.call(name, {"product": product, **args}, session["customer_id"], session["id"])
            calls.append(
                {"id": result.get("tool_call_id"), "tool": name, "args": {"product": product, **args}, "result": result}
            )
            error = error or bool(result["error"])
            for f in result["facts"]:
                facts[f["fact_id"]] = {**f, "id": f["fact_id"]}
            return result

        if p["callback"]:
            call("schedule_callback", {"action_key": s["turn_id"], "reason": s["text"]})
        if not p["missing"] and not s.get("confirmations"):
            args = {k: known[k] for k in ["amount", "term", "income", "debt", "property_value"] if k in known}
            suitability = call("check_suitability", args)
            unsuitable = not suitability["data"].get("ok", False)
            if not unsuitable:
                call("get_card_offer", args) if product == "credit_card" else call("get_rate", args)
                call("get_active_promotions", {"channel": session["channel"]})
                ds = call("get_disclosures", {})
                disclosures = ds["data"].get("disclosures", [])
                if product != "credit_card" and not error:
                    rate = next((f for f in facts.values() if f["key"] == "promo_rate"), None) or next(
                        (f for f in facts.values() if f["key"] == "standard_rate"), None
                    )
                    standard = next((f for f in facts.values() if f["key"] == "standard_rate"), rate)
                    months = next((f for f in facts.values() if f["key"] == "promo_months"), {"value": 0})
                    if rate:
                        call(
                            "calc_installment",
                            {
                                "amount": known["amount"],
                                "term": known["term"],
                                "rate": rate["value"],
                                "rate_fact_id": rate["id"],
                                "promo_months": months["value"],
                                "after_rate": standard["value"],
                            },
                        )
        if error:
            call("schedule_callback", {"action_key": s["turn_id"], "reason": "Hệ thống ngân hàng cần kiểm tra lại"})
        if p["summary"]:
            ids = [f["id"] for f in facts.values() if f["source_type"] == "bank_tool"]
            if ids and session["channel"] != "web":
                call("send_summary", {"channel": session["channel"], "fact_ids": ids, "action_key": s["turn_id"]})
        return {
            "facts": facts,
            "tool_calls": calls,
            "disclosures": disclosures,
            "tool_error": error,
            "unsuitable": unsuitable,
        }

    def rules_draft(self, s):
        if s["tool_error"]:
            return "Hệ thống ngân hàng đang gián đoạn. Em đã ghi nhận yêu cầu để chuyên viên xác nhận lại."
        if s["unsuitable"]:
            return "Hồ sơ hiện chưa phù hợp điều kiện sản phẩm. Em có thể chuyển chuyên viên để trao đổi phương án phù hợp hơn."
        if s.get("confirmations"):
            labels = {
                "income": "thu nhập",
                "amount": "số tiền vay",
                "term": "kỳ hạn",
                "debt": "khoản trả nợ hàng tháng",
            }
            return (
                "Thông tin "
                + labels.get(s["confirmations"][0], s["confirmations"][0])
                + " giữa các kênh chưa thống nhất. Anh/chị vui lòng xác nhận thông tin chính xác để em cập nhật nhé?"
            )
        if s["plan"]["missing"]:
            labels = {"amount": "số tiền muốn vay", "term": "kỳ hạn mong muốn", "income": "thu nhập hàng tháng"}
            return "Anh/chị cho em biết " + labels[s["plan"]["missing"][0]] + " để em tư vấn phù hợp nhé?"
        facts = s["facts"]

        def ph(key):
            f = next((x for x in facts.values() if x["key"] == key), None)
            return "{fact:" + f["id"] + "}" if f else ""

        if s["plan"]["product"] == "credit_card":
            reply = f"Hạn mức thẻ tham khảo từ {ph('card_limit_min')} đến {ph('card_limit_max')}. Phí thường niên {ph('annual_fee')}."
        else:
            rate = ph("promo_rate") or ph("standard_rate")
            reply = f"Khoản vay {ph('amount') or s['known'].get('amount', '')} kỳ hạn {ph('term') or s['known'].get('term', '')} có mức lãi tham khảo {rate}."
            if ph("promo_months"):
                reply += f" Thời gian ưu đãi {ph('promo_months')}."
            if ph("installment_promo"):
                reply += f" Trả góp minh họa {ph('installment_promo')} mỗi tháng trong ưu đãi, sau đó {ph('installment_after')}."
        for card in s.get("cards", []):
            reply += " " + card["text"]
        return reply

    def draft(self, s):
        draft = self.rules_draft(s)
        candidate = json_completion(
            self.settings,
            "Viết câu tư vấn ngân hàng tiếng Việt. Mọi số phải dùng {fact:id} từ facts. Không tạo fact. Giữ ý nghĩa rules_draft. Trả JSON {draft:string}.",
            {"rules_draft": draft, "known": s["known"], "facts": s["facts"], "context_parts": s["context_parts"]},
            schema=DraftSuggestion,
        )
        if candidate and isinstance(candidate.get("draft"), str) and 0 < len(candidate["draft"]) < 8000:
            draft = candidate["draft"]
        return {"draft": draft}

    def guard(self, s):
        events = []
        draft = s["draft"]
        session = s["session"]
        if session["mode"] != "harness":
            return {"guard_events": events}
        if s["tool_error"]:
            events.append(event("G2", "tool_failed", "callback"))
            draft = self.rules_draft(s)
        if s["unsuitable"]:
            events.append(event("G4", "unsuitable", "decline"))
            draft = self.rules_draft(s)
        invalid, stale = draft_problem(draft, s["facts"], self.db.setting("clock"))
        if stale:
            events.append(event("G3", "expired_fact", "refresh"))
        if invalid or stale:
            events.append(event("G1", "unbacked_number_or_fact", "regenerate", 1))
            draft = self.rules_draft(s)
            if any(draft_problem(draft, s["facts"], self.db.setting("clock"))):
                draft = "Em cần xác nhận lại thông tin trước khi đưa ra báo giá. Anh/chị muốn gặp chuyên viên không?"
                events.append(event("G1", "second_failure", "safe_fallback", 2))
        old = self.db.one(
            "SELECT id FROM facts WHERE customer_id=? AND source_type='bank_tool' AND status='expired' LIMIT 1",
            (session["customer_id"],),
        )
        if old and not s["tool_error"] and not s["plan"]["missing"]:
            draft = "Ưu đãi trước đã hết hạn; em cập nhật thông tin hiện tại. " + draft
            events.append(event("G3", "previous_quote_expired", "refresh"))
        if s["disclosures"]:
            events.append(event("G5", "required_disclosure", "append"))
        return {"draft": draft, "guard_events": events}

    def render(self, s):
        spans = []
        used = []
        text = s["draft"]
        pieces = []
        offset = 0
        cursor = 0
        for m in re.finditer(r"\{fact:([^}]+)\}", text):
            prefix = text[cursor : m.start()]
            pieces.append(prefix)
            offset += len(prefix)
            fact = s["facts"].get(m[1])
            rendered = format_fact(fact) if fact else "[thông tin chưa xác nhận]"
            pieces.append(rendered)
            if fact:
                spans.append(
                    {
                        "start": offset,
                        "end": offset + len(rendered),
                        "fact_id": m[1],
                        "source_ref": fact["source_ref"],
                        "key": fact["key"],
                        "value": fact["value"],
                        "unit": fact["unit"],
                    }
                )
                used.append(m[1])
            offset += len(rendered)
            cursor = m.end()
        pieces.append(text[cursor:])
        reply = "".join(pieces)
        if s["disclosures"]:
            reply += "\n\n" + " ".join(s["disclosures"])
        return {"reply": reply, "source_spans": spans, "used_fact_ids": list(dict.fromkeys(used))}

    def handoff_prepare(self, s):
        session = s["session"]
        cid = session["customer_id"]
        self.memory.flush(cid)
        hid = ident("ho_", [session["id"], s["turn_id"]])
        brief = self.memory.brief(cid)
        turns = []
        for row in self.db.all(
            "SELECT id,reply,result FROM turns WHERE session_id=? AND reply IS NOT NULL ORDER BY created_at",
            (session["id"],),
        ):
            saved = json.loads(row["result"] or "{}")
            trace_row = self.db.one("SELECT data FROM traces WHERE turn_id=?", (row["id"],))
            trace = json.loads(trace_row["data"]) if trace_row else {}
            turns.append(
                {
                    "turn_id": row["id"],
                    "reply": row["reply"],
                    "fact_ids": saved.get("used_fact_ids", []),
                    "tool_call_ids": [call["id"] for call in trace.get("tool_calls", []) if call.get("id")],
                }
            )
        summary = {
            "handoff_id": hid,
            "session_id": session["id"],
            "customer_id": cid,
            "status": "pending",
            "reason": s["text"],
            "brief": brief,
            "bot_said": turns,
            "open_questions": [f["key"] for f in brief["conflicts"]],
            "next_step": "Chuyên viên xác nhận các thông tin còn mở và tư vấn bước tiếp theo.",
        }
        self.db.execute(
            "INSERT OR IGNORE INTO handoffs VALUES(?,?,?,?,?,?)",
            (hid, session["id"], "pending", canonical(summary), None, now()),
        )
        self.db.execute("UPDATE sessions SET status='waiting_human' WHERE id=?", (session["id"],))
        return {
            "handoff_id": hid,
            "reply": "Em đã chuyển yêu cầu cùng thông tin đã trao đổi cho chuyên viên. Anh/chị vui lòng chờ phản hồi.",
            "guard_events": [event("G6", "customer_requested_human", "handoff")],
            "source_spans": [],
            "used_fact_ids": [],
        }

    def wait_human(self, s):
        completion = interrupt({"handoff_id": s["handoff_id"]})
        return {"completion": completion}

    def complete(self, handoff, body, recover=False):
        session = self.db.one("SELECT * FROM sessions WHERE id=?", (handoff["session_id"],))
        with self.lock(session["customer_id"]):
            with self.db.transaction() as c:
                current = c.execute("SELECT * FROM handoffs WHERE id=?", (handoff["id"],)).fetchone()
                if current["status"] == "completed":
                    return json.loads(current["receipt"])
                if current["status"] == "completing" and not recover:
                    from fastapi import HTTPException

                    raise HTTPException(409, "Handoff completion already in progress")
                if current["status"] == "pending":
                    c.execute(
                        "UPDATE handoffs SET status='completing',receipt=? WHERE id=? AND status='pending'",
                        (canonical({"pending_request": body}), handoff["id"]),
                    )
                elif recover:
                    body = json.loads(current["receipt"])["pending_request"]
            cfg = {"configurable": {"thread_id": session["id"]}}
            state = self.graph.get_state(cfg)
            if state.next:
                self.graph.invoke(Command(resume=body), cfg)
            receipt = {
                "handoff_id": handoff["id"],
                "status": "completed",
                "notes": body["notes"],
                "commitments": body["commitments"],
            }
            with self.db.transaction() as c:
                for i, commitment in enumerate(body["commitments"]):
                    key = ident("cb_", [handoff["id"], i])
                    c.execute(
                        "INSERT OR IGNORE INTO callbacks VALUES(?,?,?,?,?,?)",
                        (
                            key,
                            session["customer_id"],
                            session["id"],
                            self.db.setting("clock") + "T09:00:00+07:00",
                            "open",
                            canonical({"reason": commitment}),
                        ),
                    )
                c.execute(
                    "UPDATE handoffs SET status='completed',receipt=? WHERE id=?", (canonical(receipt), handoff["id"])
                )
                c.execute("UPDATE sessions SET status='active' WHERE id=?", (session["id"],))
            return receipt
