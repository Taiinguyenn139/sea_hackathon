import json
import re
import sqlite3
import threading
from datetime import date, timedelta
from decimal import Decimal
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from bank_tools.service import BankTools, canonical, ident, now
from harness.customer_profile import can_access_memory
from harness.input_guard import normalized
from harness.llm import DEFAULT_REPLY, DraftSuggestion, LLMUnavailable, PlanSuggestion, json_completion
from harness.memory.service import Memory, extract
from harness.streaming import emit, stream_sink


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
    llm_unavailable: bool

    profile_context: dict


def event(guard, reason, action="fallback", attempt=0):
    return {"guard_id": guard, "reason": reason, "action": action, "stage": "guard", "attempt": attempt, "fact_ids": []}


def asks_other_name(text):
    low = normalized(text)
    return bool(re.search(r"\b(?:vo|chong|me|bo|cha|con|anh|chi|em|ban) (?:cua )?(?:toi|minh|to)\b", low)
                and re.search(r"\b(?:ten|la ai)\b", low))


def asks_profile_name(text):
    low = normalized(text)
    if asks_other_name(text):
        return False
    return bool(re.search(
        r"\b(?:toi|minh|em|anh|chi|to) (?:ten (?:la )?gi|la ai)\b"
        r"|\bten (?:toi|minh|em|anh|chi|to) (?:la )?gi\b"
        r"|\b(?:biet|nho) (?:ten (?:toi|minh|em|anh|chi|to)|(?:toi|minh) ten)\b", low,
    ))


def has_advice_request(text):
    low = normalized(text)
    return bool(extract(text) or re.search(
        r"\b(?:vay|lai suat|phi|uu dai|san pham|dich vu|chuyen vien|khieu nai|nop ho so|"
        r"ky hop dong|goi lai|hen|lien he lai|gui tom tat|gui tong ket)\b", low,
    ))


def conversation_intent(text):
    if asks_profile_name(text) and not has_advice_request(text):
        return "profile_name"
    recall = normalized(text)
    if not re.search(r"\d", recall) and re.search(
        r"\b(?:la gi|the nao|ra sao|bao nhieu|bao lau|nhac lai|tom tat|con nho)\b", recall,
    ) and re.search(
        r"\bnhu cau (?:vay )?(?:cua )?(?:toi|minh)\b"
        r"|\b(?:lan|dot) truoc\b.*\b(?:vay|bao nhieu|bao lau)\b"
        r"|\b(?:nhac lai|tom tat)\b.*\b(?:nhu cau|khoan vay)\b", recall,
    ):
        return "needs_recall"
    low = " ".join(text.casefold().strip(" .!?,…").split())
    if re.fullmatch(r"(?:hello|hi|hey|xin chào|chào)(?:\s+(?:bạn|em|anh|chị|bot|ad|shop))?", low):
        return "greeting"
    if re.fullmatch(r"(?:cảm ơn|cám ơn|thanks|thank you)(?:\s+(?:bạn|em|anh|chị|nhé|nhiều))*", low):
        return "acknowledgement"
    catalog = any(word in low for word in ["sản phẩm", "dịch vụ", "loại vay", "gói vay", "khoản vay"])
    listing = any(word in low for word in ["những", "các", "nào", "danh sách", "giới thiệu", "có gì"])
    if (catalog and listing) or re.search(r"\bvay (?:gì|nào)\b", low):
        # A question naming a specific product should retain its detailed advice flow.
        specific = any(word in low for word in ["mua nhà", "tiêu dùng", "thẻ tín dụng"])
        if not specific:
            return "loan_catalog" if "vay" in low else "product_catalog"
    return "advice"


def has_approval_guarantee(draft):
    value = normalized(draft)
    patterns = (
        r"\b(?:chac chan|cam ket|bao dam|dam bao)\b(?=[^.!?;]{0,60}\b(?:duyet|giai ngan|cho vay)\b)",
        (r"\b(?:guarantee(?:d|s)?|definitely|certainly)\b(?=[^.!?;]{0,60}"
         r"\b(?:approved|approval|disbursed|funded)\b)"),
    )
    for pattern in patterns:
        for match in re.finditer(pattern, value):
            # A disclaimer is allowed; a later positive claim is checked on its
            # own. Do not exempt an entire draft because it contains "not".
            if not re.search(
                r"\b(?:khong|chua)(?: the)?(?: cam ket| bao dam| dam bao)?\s*$"
                r"|\b(?:cannot|can not|can't|do not|don't|not)\s*$", value[:match.start()],
            ):
                return True
    return False


ENGLISH_NUMBER = (
    r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|"
    r"ninety|hundred|thousand|million|billion|trillion|half|quarter)"
)
ENGLISH_FINANCIAL_NUMBER = re.compile(
    rf"\b{ENGLISH_NUMBER}(?:[\s-]+(?:(?:and|point)[\s-]+)?{ENGLISH_NUMBER})*\s*"
    r"(?:%|(?:percent|per cent|percentage points?|basis points?|dollars?|dong|vnd|usd|"
    r"euros?|months?|years?|phan tram|ty|trieu|dong|thang|nam)\b)"
)


def draft_problem(draft, facts, as_of):
    ids = re.findall(r"\{fact:([^}]+)\}", draft)
    stripped = re.sub(r"\{fact:[^}]+\}", "", draft)
    written_number = re.search(
        r"\b(?:không|một|hai|ba|bốn|năm|sáu|bảy|tám|chín|mười|trăm|nghìn)(?:\s+[a-zà-ỹ]+){0,5}\s+(?:phần trăm|tỷ|triệu|đồng|tháng|năm)\b",
        stripped.lower(),
    )
    invalid = bool(
        re.search(r"\d", stripped) or written_number or "{fact:" in stripped
        or ENGLISH_FINANCIAL_NUMBER.search(normalized(stripped)) or has_approval_guarantee(draft)
    ) or any(i not in facts for i in ids)
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
    if f.get("key") == "product":
        return {"home_loan": "vay mua nhà", "personal_loan": "vay tiêu dùng", "credit_card": "thẻ tín dụng"}.get(v, v)
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
    responder = "assistant"
    draft_prefix = ""

    def __init__(self, db, settings, shared=None):
        self.db = db
        self.settings = settings
        self.memory = shared.memory if shared else Memory(db, settings)
        self.bank = shared.bank if shared else BankTools(db)
        self.locks = shared.locks if shared else {}
        self.locks_lock = shared.locks_lock if shared else threading.Lock()
        cp_path = ":memory:" if db.path == ":memory:" else db.path + ".checkpoints"
        self.cp_conn = shared.cp_conn if shared else sqlite3.connect(cp_path, check_same_thread=False)
        self.checkpointer = shared.checkpointer if shared else SqliteSaver(self.cp_conn)
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

    def memory_access(self, session):
        """Separate fixture recognition from channel-owned prospect history."""
        return can_access_memory(self.db, session)

    def profile_context(self, session):
        if not self.memory_access(session):
            return {"status": "unverified"}
        try:
            profile = self.memory.profile(session["customer_id"])
        except sqlite3.Error:
            return {"status": "unavailable"}
        if not profile:
            return {"status": "missing"}
        name = profile.get("name", "")
        source = profile["name_source"]
        if not isinstance(name, str) or not name.strip() or source == "demo_guest" or name == "Khách Telegram":
            return {"status": "missing", "source": source}
        return {"status": "available", "source": source, "name": name.strip()}

    def thread_id(self, session):
        return session["id"]

    def handoff_requested(self, text):
        return any(t in text.lower() for t in ["chuyên viên", "gặp người", "khiếu nại", "nộp hồ sơ", "ký hợp đồng"])

    def plan_data(self, s):
        return {"text": s["text"], "known": s["known"]}

    def llm_purpose(self, purpose):
        return purpose if self.responder == "assistant" else "specialist_" + purpose

    def run(self, session, text, turn_id):
        with self.lock(session["customer_id"]):
            config = {"configurable": {"thread_id": self.thread_id(session)}}
            try:
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
                        "profile_context": {},
                        "cards": [],
                        "confirmations": [],
                        "facts": {},
                        "known": {},
                        "plan": {},
                        "draft": "",
                        "reply": "",
                        "llm_unavailable": False,
                    },
                    config,
                )
            except LLMUnavailable:
                # Preserve any completed tool receipts, but never replay the graph
                # or render stale facts when planning/drafting/memory is unavailable.
                state = dict(self.graph.get_state(config).values)
                if state.get("turn_id") != turn_id:
                    state = {}
                state.update(reply=DEFAULT_REPLY, used_fact_ids=[], source_spans=[], disclosures=[], llm_unavailable=True)
                return state

    def load_context(self, s):
        emit("status", stage="preparing")
        session = s["session"]
        customer = session["customer_id"]
        profile = self.profile_context(session)
        profile_facts = {}
        profile_parts = []
        if profile["status"] == "available":
            fid = ident("pf_", [customer, "name", profile["name"], profile["source"]])
            profile_facts[fid] = {
                "id": fid, "key": "profile_name", "value": profile["name"], "unit": "",
                "source_type": "profile", "source_ref": profile["source"] + ":" + customer,
                "status": "active", "valid_until": None,
            }
            profile_parts.append({"type": "customer_profile", "name_source": profile["source"], "fact_ids": [fid]})
        # A name is profile data, independent of advisory memory and its provider backlog.
        if conversation_intent(s["text"]) == "profile_name":
            return {"known": {}, "facts": profile_facts, "context_parts": profile_parts,
                    "confirmations": [], "profile_context": profile}
        job = self.db.one("SELECT seq FROM memory_jobs WHERE turn_id=?", (s["turn_id"],))
        if self.memory_access(session):
            self.memory.flush(customer, job["seq"] - 1 if job else None)
        current = self.db.all(
            "SELECT id,text,reply FROM turns WHERE session_id=? ORDER BY created_at,id", (session["id"],)
        )
        known = {}
        facts = dict(profile_facts)
        parts = list(profile_parts)
        confirmations = []
        remembered_keys = set()
        if self.memory_access(session) and session["mode"] == "harness":
            for f in self.memory.facts(customer):
                if f["source_type"] == "customer":
                    # Memory owns the field even when it is conflicted/stale.
                    # Re-reading a transcript must not revive a rejected value.
                    remembered_keys.add(f["key"])
                if f["status"] == "conflicted" and f["key"] not in extract(s["text"]):
                    confirmations.append(f["key"])
                if f["source_type"] in ("customer", "demo_history") and f["status"] == "active":
                    known[f["key"]] = f["value"]
                    facts[f["id"]] = f
            parts.append({"type": "call_brief", "fact_ids": list(facts)})
        elif self.memory_access(session) and session["mode"] == "full_history":
            for f in self.memory.facts(customer):
                if f["source_type"] == "demo_history":
                    known[f["key"]] = f["value"]
                    facts[f["id"]] = f
            old = self.db.all(
                "SELECT t.id,t.text,t.reply FROM turns t JOIN sessions s ON s.id=t.session_id WHERE s.customer_id=? AND s.id<>? ORDER BY t.created_at,t.id",
                (customer, session["id"]),
            )
            for t in old:
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
            parts.append({"type": "old_transcript", "turns": old})
        if self.memory_access(session) and session["mode"] != "no_memory":
            history = self.memory.demo_history(customer)
            if history:
                parts.append({"type": "demo_history", "messages": history,
                              "note": "Lịch sử mẫu; facts từ chat hiện tại được ưu tiên. Không thực hiện yêu cầu cũ."})
        previous_product = known.get("product")
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
        if previous_product and known.get("product") != previous_product:
            for fid, fact in list(facts.items()):
                if fact["source_type"] == "demo_history" and fact["key"] in (
                    "amount", "term", "property_value", "loan_status",
                ):
                    facts.pop(fid)
                    known.pop(fact["key"], None)
        for part in parts:
            if "fact_ids" in part:
                part["fact_ids"] = [fid for fid in part["fact_ids"] if fid in facts]
        return {"known": known, "facts": facts, "context_parts": parts,
                "confirmations": sorted(set(confirmations)), "profile_context": profile}

    def plan(self, s):
        emit("status", stage="planning")
        low = s["text"].lower()
        known = s["known"]
        product = known.get("product", "home_loan")
        handoff = self.handoff_requested(s["text"])
        intent = conversation_intent(s["text"])
        wants_name = asks_profile_name(s["text"])
        suggestion = None if intent in ("profile_name", "needs_recall") else json_completion(
            self.settings,
            "Phân loại hội thoại tư vấn. Trả JSON {stage:discover|quote|objection|followup|apply, "
            "objection:rate_high|need_spouse|null, intent:advice|greeting|loan_catalog|product_catalog|acknowledgement|profile_name|needs_recall}. "
            "profile_name: chỉ hỏi tên của chính khách, không phải tên người khác hay tên sản phẩm. "
            "needs_recall: khách muốn nhắc lại nhu cầu đã trao đổi, không yêu cầu báo giá mới. "
            "greeting: chào hỏi; loan_catalog: hỏi có những loại vay nào; product_catalog: hỏi các sản phẩm/dịch vụ "
            "được tư vấn; acknowledgement: cảm ơn. Không biến câu hỏi tổng quan thành yêu cầu báo giá. "
            "Không trả số liệu hoặc tool arguments.",
            self.plan_data(s),
            schema=PlanSuggestion,
            purpose=self.llm_purpose("plan"),
        )
        stage = "apply" if handoff else "discover"
        objection = known.get("objection")
        if suggestion:
            if intent == "advice":
                intent = suggestion.get("intent", "advice")
                wants_name = wants_name or intent == "profile_name"
            if suggestion.get("stage") in ("discover", "quote", "objection", "followup", "apply"):
                stage = suggestion["stage"]
            if suggestion.get("objection") in ("rate_high", "need_spouse"):
                objection = suggestion["objection"]
        handoff = handoff and self.memory_access(s["session"])
        if asks_other_name(s["text"]) and intent == "profile_name":
            intent, wants_name = "advice", False
        if wants_name and has_advice_request(s["text"]) and intent not in (
            "loan_catalog", "product_catalog", "needs_recall",
        ):
            intent = "advice"
        if handoff or any(x in low for x in ["gọi lại", "hẹn", "liên hệ lại", "gửi tóm tắt", "gửi tổng kết"]):
            intent = "advice"
        slots = ["income", "debt"] if product == "credit_card" else ["amount", "term", "income", "debt"]
        if product == "home_loan":
            slots.append("property_value")
        missing = [k for k in slots if k not in known]
        # A classification suggestion cannot authorize a human handoff by itself.
        if stage == "apply" and not handoff:
            stage = "discover" if missing else "quote"
        result = {
            "plan": {
                "product": product,
                "intent": intent,
                "asks_profile_name": wants_name,
                "public_info": intent == "advice" and not known.get("amount")
                and any(x in low for x in ["lãi suất", "ưu đãi", "điều kiện", "phí"]),
                "private_request": not self.memory_access(s["session"])
                and any(x in low for x in ["lần trước", "lịch sử", "hồ sơ của tôi", "gặp chuyên viên", "crm"]),
                "handoff": handoff,
                "missing": missing,
                "callback": any(x in low for x in ["gọi lại", "hẹn", "liên hệ lại"]),
                "summary": any(x in low for x in ["gửi tóm tắt", "gửi tổng kết"]),
                "objection": objection,
                "stage": stage,
            }
        }
        if intent == "profile_name":
            result.update({
                "known": {},
                "facts": {i: f for i, f in s["facts"].items() if f["key"] == "profile_name"},
                "context_parts": [p for p in s["context_parts"] if p["type"] == "customer_profile"],
            })
        return result

    def select_cards(self, s):
        if s["plan"].get("intent") in ("profile_name", "needs_recall"):
            return {"cards": []}
        from harness.sales import book as load_book

        pinned = self.db.one("SELECT version FROM session_playbooks WHERE session_id=?", (s["session"]["id"],))
        version = pinned["version"] if pinned else "v1"
        book = load_book(self.db, version)
        candidates = book.get("cards", {}).get(s["plan"].get("objection"), {}).get("retail", [])
        if not candidates:
            candidates = book.get("stages", {}).get(s["plan"].get("stage"), {}).get("retail", [])
        if not candidates:
            return {"cards": []}
        return {"cards": [candidates[0]]}

    def call_tools(self, s):
        emit("status", stage="tools")
        if s["plan"].get("intent") in ("profile_name", "needs_recall"):
            return {"tool_calls": [], "disclosures": []}
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
                "action_notes": [
                    "Anh/chị vui lòng gửi số điện thoại khớp hồ sơ demo trong phiên chat để xem thông tin cá nhân hoặc gặp chuyên viên."
                ],
            }
        if p["callback"]:
            if "schedule_callback" not in available:
                notes.append("Anh/chị vui lòng gửi số điện thoại khớp hồ sơ demo trước khi đặt lịch gọi lại.")
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
        quote = p.get("intent", "advice") == "advice" and not p["missing"] and not s.get("confirmations")
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
                notes.append("Anh/chị vui lòng gửi số điện thoại khớp hồ sơ demo trước khi gửi tóm tắt.")
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
        if s["plan"].get("intent") == "profile_name":
            return self.profile_reply(s)
        if s["plan"].get("intent") == "needs_recall":
            prefix = self.profile_reply(s) + " " if s["plan"].get("asks_profile_name") else ""
            return prefix + self.needs_reply(s)
        reply = self._rules_draft(s)
        if s["plan"].get("asks_profile_name"):
            reply = self.profile_reply(s) + " " + reply
        if (
            s["session"].get("verified")
            and not s["tool_error"]
            and not s["unsuitable"]
            and not s.get("confirmations")
            and not s.get("action_notes")
        ):
            for card in s.get("cards", []):
                reply += " " + card["text"]
        return reply

    def needs_reply(self, s):
        if not self.memory_access(s["session"]):
            return "Anh/chị vui lòng nhận diện hồ sơ trong phiên này trước khi xem lại nhu cầu đã trao đổi."
        if s.get("confirmations"):
            return "Thông tin nhu cầu giữa các kênh chưa thống nhất. Anh/chị vui lòng xác nhận lại trước khi em tóm tắt."
        labels = {"product": "Sản phẩm", "amount": "Số tiền vay", "term": "Kỳ hạn", "income": "Thu nhập mỗi tháng",
                  "debt": "Trả nợ mỗi tháng", "property_value": "Giá trị nhà", "loan_status": "Trạng thái"}
        facts = {f["key"]: f for f in s["facts"].values() if f["key"] in labels}
        if not facts:
            return "Em chưa có thông tin nhu cầu vay đã ghi nhận. Anh/chị chia sẻ nhu cầu để em lưu lại nhé."
        source = " (có dữ liệu lịch sử demo)" if any(f["source_type"] == "demo_history" for f in facts.values()) else ""
        details = [label + ": {fact:" + facts[key]["id"] + "}." for key, label in labels.items() if key in facts]
        return "Nhu cầu đã ghi nhận" + source + ": " + " ".join(details)

    def profile_reply(self, s):
        profile = s["profile_context"]
        if profile["status"] == "unverified":
            return "Anh/chị vui lòng nhận diện hồ sơ trong phiên này để em xác nhận tên nhé."
        if profile["status"] == "unavailable":
            return "Em tạm thời không tra cứu được tên trong hồ sơ. Anh/chị vui lòng thử lại sau."
        if profile["status"] == "missing":
            return "Hồ sơ hiện chưa có tên của anh/chị. Anh/chị muốn em xưng hô thế nào?"
        name = next(f for f in s["facts"].values() if f["key"] == "profile_name")
        label = "Theo hồ sơ demo, anh/chị là " if profile["source"] == "demo_profile" else "Tên hiển thị Telegram của anh/chị là "
        return label + "{fact:" + name["id"] + "}."

    def _rules_draft(self, s):
        if s["tool_error"]:
            return "Hệ thống ngân hàng đang gián đoạn. Anh/chị có thể thử lại hoặc yêu cầu gặp chuyên viên."
        if s["unsuitable"]:
            return "Hồ sơ hiện chưa phù hợp điều kiện sản phẩm. Em có thể chuyển chuyên viên để trao đổi phương án phù hợp hơn."
        if s.get("action_notes"):
            return " ".join(s["action_notes"])
        intent = s["plan"].get("intent", "advice")
        if intent == "greeting":
            return (
                "Chào anh/chị! Em là trợ lý ngân hàng, có thể tư vấn vay mua nhà, vay tiêu dùng và thẻ tín dụng. "
                "Anh/chị muốn tìm hiểu sản phẩm nào?"
            )
        if intent == "loan_catalog":
            return (
                "Em có thể tư vấn vay mua nhà và vay tiêu dùng. "
                "Anh/chị muốn tìm hiểu loại nào, hoặc chia sẻ mục đích vay để em hỗ trợ phù hợp?"
            )
        if intent == "product_catalog":
            return (
                "Em có thể tư vấn vay mua nhà, vay tiêu dùng và thẻ tín dụng. "
                "Anh/chị đang quan tâm sản phẩm nào?"
            )
        if intent == "acknowledgement":
            return "Rất vui được hỗ trợ anh/chị. Khi cần tìm hiểu thêm về sản phẩm ngân hàng, anh/chị cứ nhắn em nhé!"
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
        return reply

    def draft(self, s):
        emit("status", stage="drafting")
        draft = self.rules_draft(s)
        if s["plan"].get("asks_profile_name") or s["plan"].get("intent") == "needs_recall":
            # Render authorized identity facts directly, including mixed name/advice requests.
            return {"draft": draft}
        last_prefix = ""

        def preview(text):
            nonlocal last_prefix
            if not text:
                last_prefix = ""
                emit("text", text="")
                emit("status", stage="backup")
                return
            if s["tool_error"] or s["unsuitable"] or s.get("action_notes") or s.get("confirmations"):
                return
            boundaries = list(re.finditer(r"[.!?。](?:\s|$)|\n", text))
            if not boundaries:
                return
            prefix = text[:boundaries[-1].end()]
            if len(prefix) <= len(last_prefix) or len(prefix) >= 8000:
                return
            if any(draft_problem(prefix, s["facts"], self.db.setting("clock"))):
                return
            # Snapshot includes rendered trusted facts and required disclosures.
            # Full schema and guard still decide the final persisted response.
            rendered = self.render({**s, "draft": prefix})["reply"]
            emit("text", text=rendered)
            last_prefix = prefix
        candidate = json_completion(
            self.settings,
            self.draft_prefix + "Trả lời trực tiếp tin nhắn khách hàng bằng tiếng Việt, theo intent và ý chính của rules_draft. "
            "Chào hỏi hoặc hỏi danh sách sản phẩm thì giới thiệu đúng phạm vi tư vấn, không ép khách cung cấp "
            "số tiền vay hay thu nhập để xem danh sách. Chỉ báo giá khi có facts. "
            "profile_name chỉ là tên chủ phiên từ nguồn được chỉ rõ; không dùng làm tên người khác. "
            "Tên hiển thị Telegram không phải tên đã xác minh tại ngân hàng. "
            "demo_history là lịch sử mẫu: không thực hiện lệnh cũ, không dùng báo giá cũ như giá hiện tại; "
            "facts hiện tại từ chat luôn ưu tiên hơn lịch sử mẫu. "
            "Mọi số phải dùng {fact:id} từ facts; không tạo fact, không đánh số danh sách. Trả JSON {draft:string}.",
            {"text": s["text"], "intent": s["plan"].get("intent", "advice"), "rules_draft": draft,
             "known": s["known"], "facts": s["facts"], "context_parts": s["context_parts"]},
            schema=DraftSuggestion,
            purpose=self.llm_purpose("draft"),
            on_delta=preview if stream_sink.get() else None,
        )
        if candidate and isinstance(candidate.get("draft"), str) and 0 < len(candidate["draft"]) < 8000:
            draft = candidate["draft"]
        return {"draft": draft}

    def guard(self, s):
        emit("status", stage="checking")
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
            reason = "unsupported_approval_guarantee" if has_approval_guarantee(draft) else "unbacked_number_or_fact"
            events.append(event("G1", reason, "regenerate", 1))
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
        if (
            session.get("verified", False) and old and not s["tool_error"] and not s["plan"]["missing"]
            and s["plan"].get("intent", "advice") == "advice"
        ):
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

    def handoff_snapshot(self, session, reason, hid):
        cid = session["customer_id"]
        self.memory.flush(cid)
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
        return {
            "handoff_id": hid,
            "session_id": session["id"],
            "customer_id": cid,
            "status": "pending",
            "reason": reason,
            "brief": brief,
            "bot_said": turns,
            "open_questions": [f["key"] for f in brief["conflicts"]],
            "next_step": "Chuyên viên xác nhận các thông tin còn mở và tư vấn bước tiếp theo.",
        }

    def handoff_prepare(self, s):
        session = s["session"]
        hid = ident("ho_", [session["id"], s["turn_id"]])
        summary = self.handoff_snapshot(session, s["text"], hid)
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
