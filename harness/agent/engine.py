import json
import re
import sqlite3
import threading
from datetime import date, timedelta
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
    action_notes: list


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
    if u in ("đồng", "VND"):
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
                    "action_notes": [],
                },
                {"configurable": {"thread_id": session["id"]}},
            )

    def load_context(self, s):
        session = s["session"]
        customer = session["customer_id"]
        job = self.db.one("SELECT seq FROM memory_jobs WHERE turn_id=?", (s["turn_id"],))
        if session.get("verified", False):
            self.memory.flush(customer, job["seq"] - 1 if job else None)
        current = self.db.all(
            "SELECT id,text,reply FROM turns WHERE session_id=? ORDER BY created_at,id", (session["id"],)
        )
        known = {}
        facts = {}
        parts = []
        confirmations = []
        if session.get("verified", False) and session["mode"] == "harness":
            for f in self.memory.facts(customer):
                if f["status"] == "conflicted" and f["key"] not in extract(s["text"]):
                    confirmations.append(f["key"])
                if f["source_type"] == "customer" and f["status"] == "active":
                    known[f["key"]] = f["value"]
                    facts[f["id"]] = f
            parts.append({"type": "call_brief", "fact_ids": list(facts)})
        elif session.get("verified", False) and session["mode"] == "full_history":
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
        for t in current:
            for k, (v, u) in extract(t["text"]).items():
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
        handoff = handoff and bool(s["session"].get("verified", False))
        slots = ["income", "debt"] if product == "credit_card" else ["amount", "term", "income", "debt"]
        if product == "home_loan":
            slots.append("property_value")
        missing = [k for k in slots if k not in known]
        return {
            "plan": {
                "product": product,
                "public_info": not known.get("amount")
                and any(x in low for x in ["lãi suất", "ưu đãi", "điều kiện", "phí"]),
                "private_request": not s["session"].get("verified", False)
                and any(x in low for x in ["lần trước", "lịch sử", "hồ sơ của tôi", "gặp chuyên viên", "crm"]),
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
        p, known, session = s["plan"], s["known"], s["session"]
        facts, calls, disclosures, notes = dict(s["facts"]), [], [], []
        error, unsuitable = False, False
        product = p["product"]
        as_of = self.db.setting("clock")
        available = {tool["name"] for tool in self.bank.definitions(session["customer_id"], session["id"])}

        def call(name, args):
            nonlocal error
            args = {**args, "as_of": as_of}
            result = self.bank.call(name, args, session["customer_id"], session["id"], idempotency_key=s["turn_id"])
            calls.append({"id": result["tool_call_id"], "tool": name, "args": args, "result": result})
            error = error or bool(result["error"])
            for fact in result["facts"]:
                facts[fact["fact_id"]] = {**fact, "id": fact["fact_id"]}
            return result

        if p["private_request"]:
            return {
                "facts": facts,
                "tool_calls": [],
                "action_notes": ["Anh/chị vui lòng xác minh để xem thông tin cá nhân hoặc gặp chuyên viên."],
            }
        if p["callback"]:
            if "schedule_callback" not in available:
                notes.append("Anh/chị vui lòng xác minh trước khi đặt lịch gọi lại.")
            else:
                low = s["text"].lower()
                iso = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})", s["text"])
                time = iso[0] if iso else None
                hour = re.search(r"(?<![\dT])([01]?\d|2[0-3])(?::([0-5]\d)|\s*giờ(?:\s*([0-5]?\d)\s*phút)?)", low)
                offset = re.search(r"sau\s+(\d+)\s+ngày", low)
                days = int(offset[1]) if offset else 1 if "ngày mai" in low else 2 if "hai ngày" in low else None
                calendar = re.search(r"\b\d{4}-\d{2}-\d{2}\b", low)
                if days is not None and not 1 <= days <= 365:
                    days = None
                if not time and hour and (days is not None or calendar):
                    due = calendar[0] if calendar else (date.fromisoformat(as_of) + timedelta(days=days)).isoformat()
                    time = f"{due}T{int(hour[1]):02}:{int(hour[2] or hour[3] or 0):02}:00+07:00"
                if not time:
                    notes.append(
                        "Anh/chị cho em ngày và giờ gọi lại cụ thể, sau ngày hiện tại; giờ được hiểu theo giờ Việt Nam."
                    )
                else:
                    receipt = call("schedule_callback", {"time": time})
                    if not receipt["error"]:
                        notes.append("Đã tạo lịch gọi lại mô phỏng theo thời gian anh/chị yêu cầu.")
        quote = not p["missing"] and not s.get("confirmations")
        if quote or p["public_info"]:
            if quote:
                args = {
                    key: int(known[key])
                    for key in ["income", "debt", "amount", "term", "property_value"]
                    if key in known
                }
                suitability = call("check_suitability", {"product": product, **args})
                unsuitable = not suitability["error"] and not suitability["data"].get("ok", False)
                if not error and not unsuitable:
                    # Mass is the public illustrative offer. Never infer CRM/login from a demo profile.
                    if product == "credit_card":
                        card = call("get_card_offer", {"income": int(known["income"]), "segment": "mass"})
                        unsuitable = not card["error"] and not card["data"].get("eligible", False)
                    else:
                        call(
                            "get_rate",
                            {
                                "product": product,
                                "amount": int(known["amount"]),
                                "term": int(known["term"]),
                                "segment": "mass",
                            },
                        )
            if not error and not unsuitable:
                promos = call("get_active_promotions", {"product": product, "channel": session["channel"]})
                ds = call(
                    "get_disclosures",
                    {
                        "product": product,
                        "topics": ["fee", "promo"] if product == "credit_card" else ["rate", "installment", "promo"],
                    },
                )
                disclosures = [item["text"] for item in ds["data"].get("disclosures", [])]
                if quote and product != "credit_card" and not error:
                    standard = next(f for f in facts.values() if f["key"] == "standard_rate")
                    promo = next((f for f in promos["facts"] if f["key"] == "promo_rate"), None)
                    rate = promo or standard
                    months = next(
                        (
                            f["value"]
                            for f in promos["facts"]
                            if f["key"] == "promo_months" and f["label"] == rate["label"]
                        ),
                        0,
                    )
                    args = {
                        "amount": int(known["amount"]),
                        "term": int(known["term"]),
                        "rate": rate["value"],
                        "rate_fact_id": rate["fact_id"],
                        "promo_months": min(months, int(known["term"])),
                    }
                    if promo and int(known["term"]) > months:
                        args.update(after_rate=standard["value"], after_rate_fact_id=standard["fact_id"])
                    call("calc_installment", args)
        if p["summary"]:
            if "send_summary" not in available:
                notes.append("Anh/chị vui lòng xác minh trước khi gửi tóm tắt.")
            elif not error and not unsuitable:
                ids = [
                    f["id"] for f in facts.values() if f["source_type"] == "bank_tool" and f.get("observed_at") == as_of
                ]
                if ids:
                    receipt = call("send_summary", {"channel": session["channel"], "fact_ids": ids})
                    if not receipt["error"]:
                        notes.append("Đã lưu bản tóm tắt mô phỏng; chưa gửi qua nhà cung cấp bên ngoài.")
                else:
                    notes.append("Em cần báo giá đầy đủ theo thông tin hiện tại trước khi tạo tóm tắt.")
        return {
            "facts": facts,
            "tool_calls": calls,
            "disclosures": disclosures,
            "tool_error": error,
            "unsuitable": unsuitable,
            "action_notes": notes,
        }

    def rules_draft(self, s):
        if s["tool_error"]:
            return "Hệ thống ngân hàng đang gián đoạn. Anh/chị có thể thử lại hoặc yêu cầu gặp chuyên viên."
        if s["unsuitable"]:
            return "Hồ sơ hiện chưa phù hợp điều kiện sản phẩm. Em có thể chuyển chuyên viên để trao đổi phương án phù hợp hơn."
        if s.get("action_notes"):
            return " ".join(s["action_notes"])
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
        if s["plan"]["missing"] and not s["plan"].get("public_info"):
            labels = {
                "amount": "số tiền muốn vay",
                "term": "kỳ hạn mong muốn",
                "income": "thu nhập hàng tháng",
                "debt": "số tiền trả nợ hiện tại mỗi tháng (hoặc xác nhận không có nợ)",
                "property_value": "giá trị căn nhà",
            }
            return "Anh/chị cho em biết " + labels[s["plan"]["missing"][0]] + " để em tư vấn phù hợp nhé?"
        facts = s["facts"]

        def ph(key):
            f = next((x for x in facts.values() if x["key"] == key), None)
            return "{fact:" + f["id"] + "}" if f else ""

        if s["plan"].get("public_info") and s["plan"]["missing"]:
            if ph("promo_rate"):
                return f"Ưu đãi công khai trên kênh hiện tại: lãi suất {ph('promo_rate')} trong {ph('promo_months')}. Cần bổ sung thông tin để tính khoản trả góp phù hợp."
            if ph("promo_fee_waiver"):
                return f"Ưu đãi công khai: miễn {ph('promo_fee_waiver')} phí trong {ph('promo_months')}."
            return "Hiện không có ưu đãi phù hợp trên kênh này. Anh/chị có thể bổ sung nhu cầu để em tư vấn."
        if s["plan"]["product"] == "credit_card":
            reply = f"Hạn mức thẻ tham khảo từ {ph('card_limit_min')} đến {ph('card_limit_max')}. Phí thường niên {ph('annual_fee')}."
        else:
            rate = ph("promo_rate") or ph("standard_rate")
            reply = f"Khoản vay {ph('amount') or s['known'].get('amount', '')} kỳ hạn {ph('term') or s['known'].get('term', '')} có mức lãi tham khảo {rate}."
            if ph("promo_months"):
                reply += f" Thời gian ưu đãi {ph('promo_months')}."
            if ph("installment_promo"):
                reply += f" Trả góp minh họa {ph('installment_promo')} mỗi tháng trong ưu đãi."
                if ph("installment_after"):
                    reply += f" Sau ưu đãi {ph('installment_after')} mỗi tháng."
            elif ph("installment_standard"):
                reply += f" Trả góp minh họa {ph('installment_standard')} mỗi tháng."
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
        if not s["session"].get("verified", False):
            events.append(event("G7", "public_tools_only", "restrict_private"))
        draft = s["draft"]
        session = s["session"]
        if session["mode"] != "harness":
            return {"guard_events": events}
        if s["tool_error"]:
            events.append(event("G2", "tool_failed", "retry_or_handoff"))
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
        old = (
            self.db.one(
                "SELECT id FROM facts WHERE customer_id=? AND source_type='bank_tool' AND status='expired' LIMIT 1",
                (session["customer_id"],),
            )
            if session.get("verified", False)
            else None
        )
        if session.get("verified", False) and old and not s["tool_error"] and not s["plan"]["missing"]:
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
