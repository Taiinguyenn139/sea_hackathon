import json
import re
import time
from decimal import Decimal

from bank_tools.service import canonical, ident
from harness.customer_profile import customer_profile
from harness.llm import ExtractionSuggestion, LLMUnavailable, json_completion


def extract(text):
    low = text.lower()
    slots = {}
    if any(x in low for x in ["bảo hiểm sức khỏe", "health_insurance"]):
        slots["product"] = ("health_insurance", "")
    elif any(x in low for x in ["mua nhà", "vay nhà", "home_loan"]):
        slots["product"] = ("home_loan", "")
    elif any(x in low for x in ["tiêu dùng", "personal_loan"]):
        slots["product"] = ("personal_loan", "")
    elif any(x in low for x in ["thẻ tín dụng", "credit_card"]):
        slots["product"] = ("credit_card", "")
    elif "bảo hiểm" in low:
        slots["product"] = ("health_insurance", "")
    elif "thẻ" in low:
        slots["product"] = ("credit_card", "")
    previous_money_end = 0
    for match in re.finditer(r"(-?\d+(?:[.,]\d+)?)\s*(tỷ|triệu|nghìn|trieu|ty)", low):
        value = (
            Decimal(match[1].replace(",", "."))
            * {"tỷ": 10**9, "ty": 10**9, "triệu": 10**6, "trieu": 10**6, "nghìn": 1000}[match[2]]
        )
        before = low[max(previous_money_end, match.start() - 120) : match.start()]
        previous_money_end = match.end()
        cues = {"income": ["thu nhập", "lương"], "debt": ["nợ", "trả nợ"], "property_value": ["giá nhà", "giá trị"]}
        nearest = [(before.rfind(cue), key) for key, words in cues.items() for cue in words if cue in before]
        if not nearest and slots.get("product", (None,))[0] == "health_insurance":
            continue  # A premium/benefit is not a requested loan amount.
        key = max(nearest)[1] if nearest else "amount"
        slots[key] = (str(value.quantize(Decimal(1))), "đồng")
    if re.search(r"không (?:có |còn )?nợ|(?:nợ|trả nợ)(?: hàng tháng)?\s*(?:là |: )?0(?:\s|$|[.,;])", low):
        slots["debt"] = ("0", "đồng")
    term = re.search(r"(?:kỳ hạn|thời hạn|trong|vay)\s*(\d+)\s*(năm|tháng)", low)
    if not term:
        term = re.search(r"(\d+)\s*(năm|tháng)", low)
    if term and slots.get("product", (None,))[0] != "health_insurance":
        slots["term"] = (str(int(term[1]) * (12 if term[2] == "năm" else 1)), "tháng")
    if any(x in low for x in ["lãi cao", "lãi sau", "tăng lãi"]):
        slots["objection"] = ("rate_high", "")
    if any(x in low for x in ["chồng", "vợ", "gia đình"]):
        slots["objection"] = ("need_spouse", "")
    if any(x in low for x in ["gọi lại", "hẹn", "liên hệ lại"]):
        slots["commitment"] = ("Khách yêu cầu liên hệ lại", "")
    return slots


def corrects(text, key):
    """Recognize an explicit current-value update for this field, not another clause."""
    clauses = re.split(r"[;\n]|\.(?!\d)|,(?!\d)|\s+(?:và|nhưng)\s+", text.lower())
    for clause in clauses:
        if key not in extract(clause):
            continue
        # A proposed or negated change must not resolve conflicting statements.
        intent = re.sub(r"không (?:có |còn )?nợ", "", clause)
        if "?" in clause or re.search(
            r"\b(?:nếu|giả sử|giả định|ước gì|mong muốn|dự kiến|sẽ|chưa|không)\b", intent,
        ):
            continue
        if re.search(
            r"\b(?:xác nhận|đính chính|sửa lại|chính xác|cập nhật|"
            r"tăng (?:lên|thành)|giảm (?:xuống|còn)|đổi (?:sang|thành)|"
            r"hiện tại|bây giờ|hiện nay|mới là)\b", clause,
        ):
            return True
    return False


class Memory:
    def __init__(self, db, settings=None):
        self.db = db
        self.settings = settings

    def enqueue(self, conn, turn_id, customer, session, channel, text):
        if conn.execute("SELECT turn_id FROM memory_jobs WHERE turn_id=?", (turn_id,)).fetchone():
            return
        conn.execute("INSERT OR IGNORE INTO customer_sequences(customer_id) VALUES(?)", (customer,))
        conn.execute("UPDATE customer_sequences SET next_seq=next_seq+1 WHERE customer_id=?", (customer,))
        seq = conn.execute("SELECT next_seq FROM customer_sequences WHERE customer_id=?", (customer,)).fetchone()[0]
        conn.execute(
            "INSERT OR IGNORE INTO memory_jobs(turn_id,customer_id,seq,payload) VALUES(?,?,?,?)",
            (
                turn_id,
                customer,
                seq,
                canonical({"text": text, "channel": channel, "session_id": session, "date": self.db.setting("clock")}),
            ),
        )

    def flush(self, customer, target_seq=None, allow_llm=True):
        wait_deadline = time.monotonic() + (self.settings.llm_call_budget_seconds if self.settings else 20)
        waited = False
        with self.db.transaction() as c:
            row = c.execute("SELECT * FROM customer_sequences WHERE customer_id=?", (customer,)).fetchone()
            if not row:
                return
            target = row["next_seq"] if target_seq is None else min(target_seq, row["next_seq"])
        while True:
            busy = False
            with self.db.transaction() as c:
                row = c.execute("SELECT * FROM customer_sequences WHERE customer_id=?", (customer,)).fetchone()
                if row["processed_seq"] >= target:
                    return
                job = c.execute(
                    "SELECT * FROM memory_jobs WHERE customer_id=? AND seq=?", (customer, row["processed_seq"] + 1)
                ).fetchone()
                if not job:
                    raise RuntimeError("memory sequence gap")
                if job["status"] == "failed":
                    raise RuntimeError("memory job failed")
                use_llm = allow_llm and self.settings and self.settings.llm_backend == "openai"
                if not use_llm:
                    self._write(c, job["turn_id"], customer, json.loads(job["payload"]))
                    self._finish(c, job, customer)
                    continue
                if job["lease_until"] > time.time():
                    if job["status"] != "processing":
                        raise LLMUnavailable("memory_cooldown")
                    busy = True
                else:
                    if waited and time.monotonic() >= wait_deadline:
                        raise LLMUnavailable("memory_busy")
                    lease = time.time() + self.settings.llm_call_budget_seconds + 10
                    c.execute(
                        "UPDATE memory_jobs SET status='processing',lease_until=?,error=NULL WHERE turn_id=?",
                        (lease, job["turn_id"]),
                    )
                    payload = json.loads(job["payload"])
            if busy:
                waited = True
                if time.monotonic() >= wait_deadline:
                    raise LLMUnavailable("memory_busy")
                time.sleep(0.01)
                continue
            try:
                # Provider calls must never hold the shared SQLite transaction/lock.
                suggestion = json_completion(
                    self.settings,
                    "Trích các đoạn nguyên văn chứa thông tin nhu cầu tài chính. Trả JSON {spans:[string]}. Mỗi span phải là chuỗi con nguyên văn của text. Không tự tạo giá trị.",
                    {"text": payload["text"]},
                    schema=ExtractionSuggestion,
                    purpose="extract",
                )
                with self.db.transaction() as c:
                    current = c.execute("SELECT * FROM memory_jobs WHERE turn_id=?", (job["turn_id"],)).fetchone()
                    sequence = c.execute(
                        "SELECT processed_seq FROM customer_sequences WHERE customer_id=?", (customer,)
                    ).fetchone()[0]
                    if (
                        current["status"] != "processing" or current["lease_until"] != lease
                        or current["lease_until"] <= time.time() or sequence != job["seq"] - 1
                    ):
                        raise LLMUnavailable("memory_claim_lost")
                    self._write(c, job["turn_id"], customer, payload, suggestion)
                    self._finish(c, job, customer)
            except Exception as exc:
                # Preserve payload/order for a later request; no hot retry loop.
                self.db.execute(
                    "UPDATE memory_jobs SET status='pending',lease_until=?,error=? "
                    "WHERE turn_id=? AND status='processing' AND lease_until=?",
                    (
                        time.time() + 30, exc.category if isinstance(exc, LLMUnavailable) else "processing_failed",
                        job["turn_id"], lease,
                    ),
                )
                raise

    @staticmethod
    def _finish(c, job, customer):
        c.execute(
            "UPDATE memory_jobs SET status='processed',lease_until=0,error=NULL WHERE turn_id=?", (job["turn_id"],)
        )
        c.execute("UPDATE customer_sequences SET processed_seq=? WHERE customer_id=?", (job["seq"], customer))

    def _write(self, c, turn, customer, payload, suggestion=None):
        extracted = extract(payload["text"])
        if suggestion and isinstance(suggestion.get("spans"), list):
            for span in suggestion["spans"][:20]:
                if isinstance(span, str) and span and span in payload["text"]:
                    for key, value in extract(span).items():
                        if extracted.get("product", (None,))[0] == "health_insurance" and key in ("amount", "term"):
                            continue  # A partial span must retain the full message's product context.
                        extracted.setdefault(key, value)
        for key, (value, unit) in extracted.items():
            correction = corrects(payload["text"], key)
            fid = ident("cf_", [turn, key, value])
            old = c.execute(
                "SELECT * FROM facts WHERE customer_id=? AND key=? AND source_type='customer' AND status IN ('active','conflicted')",
                (customer, key),
            ).fetchall()
            different = [f for f in old if f["value"] != value]
            status = "active"
            if different and not correction and any(f["channel"] != payload["channel"] for f in different):
                status = "conflicted"
                c.execute(
                    "UPDATE facts SET status='conflicted' WHERE customer_id=? AND key=? AND source_type='customer' AND status IN ('active','conflicted')",
                    (customer, key),
                )
            else:
                c.execute(
                    "UPDATE facts SET status='superseded' WHERE customer_id=? AND key=? AND source_type='customer' AND status IN ('active','conflicted')",
                    (customer, key),
                )
            c.execute(
                "INSERT OR IGNORE INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fid,
                    customer,
                    key,
                    value,
                    unit,
                    "customer",
                    turn,
                    payload["channel"],
                    payload["date"],
                    None,
                    status,
                    0.95,
                ),
            )
            event = ident("je_", [turn, key])
            kind = "objection" if key == "objection" else "commitment" if key == "commitment" else "need"
            c.execute(
                "INSERT OR IGNORE INTO journey_events VALUES(?,?,?,?)",
                (event, customer, kind, canonical({"key": key, "fact_ids": [fid], "value": value})),
            )

    def facts(self, customer):
        date = self.db.setting("clock")
        self.db.execute(
            "UPDATE facts SET status=CASE WHEN source_type='bank_tool' THEN 'expired' ELSE 'stale' END WHERE customer_id=? AND valid_until IS NOT NULL AND valid_until<? AND status='active'",
            (customer, date),
        )
        return self.db.all("SELECT * FROM facts WHERE customer_id=? ORDER BY observed_at,id", (customer,))

    def profile(self, customer):
        return customer_profile(self.db, customer)

    def brief(self, customer):
        from harness.identity import resolve_identity

        customer = resolve_identity(customer) or customer
        self.flush(customer)
        profile = self.profile(customer)
        if not profile:
            raise ValueError("Unknown customer")
        facts = self.facts(customer)
        phone = self.db.one("SELECT phone_e164 FROM customer_profiles WHERE id=?", (customer,)) or self.db.one(
            "SELECT phone_e164 FROM demo_phone_links WHERE customer_id=?", (customer,)
        )
        return {
            "redacted": False,
            "customer_id": customer,
            "user_id": profile.get("user_id", phone["phone_e164"] if phone else customer),
            "name": profile["name"],
            "name_source": profile["name_source"],
            "cif": profile["cif"],
            "phone": phone["phone_e164"] if phone else profile.get("phone"),
            "facts": facts,
            "needs": [f for f in facts if f["source_type"] == "customer" and f["status"] == "active"],
            "conflicts": [f for f in facts if f["status"] == "conflicted"],
            "quotes": [f for f in facts if f["source_type"] == "bank_tool"],
            "warnings": [f for f in facts if f["status"] in ("expired", "stale", "conflicted")],
            "commitments": self.db.all("SELECT * FROM callbacks WHERE customer_id=? AND status='open'", (customer,)),
        }
