import json
import re
import time
from decimal import Decimal

from bank_tools.service import canonical, ident
from harness.customer_profile import customer_profile
from harness.llm import ExtractionSuggestion, LLMUnavailable, current_deadline, json_completion
from harness.memory.dataset_history import combine_facts, context_slots, project_history

_MONEY = re.compile(
    r"(?<![\w.])-?(?:\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s*(?:tỷ|triệu|nghìn|trieu|ty|đồng|vnd)\b"
)
_CLAUSES = re.compile(r"(?<!\d)[.;!?]|[.;!?](?!\d)|,(?!\d)")


def _money_slots(text):
    slots = {}
    for clause in _CLAUSES.split(text):
        previous_end = 0
        for match in _MONEY.finditer(clause):
            token = re.fullmatch(r"(-?[\d.,]+)\s*(tỷ|triệu|nghìn|trieu|ty|đồng|vnd)", match[0])
            number, unit = token.groups()
            if unit in ("đồng", "vnd") and not re.fullmatch(r"-?(?:\d{1,3}(?:[.,]\d{3})+|\d+)", number):
                continue
            number = re.sub(r"[.,]", "", number) if re.fullmatch(r"-?\d{1,3}(?:[.,]\d{3})+", number) else number.replace(",", ".")
            value = Decimal(number) * {
                "tỷ": 10**9, "ty": 10**9, "triệu": 10**6, "trieu": 10**6,
                "nghìn": 1000, "đồng": 1, "vnd": 1,
            }[unit]
            before = clause[previous_end:match.start()]
            after = clause[match.end():match.end() + 40]
            previous_end = match.end()
            if any(cue in before for cue in ("đừng", "không tự", "không lấy lại", "không được", "chưa nhận")):
                continue
            cues = {
                "income": ["thu nhập", "lương"], "debt": ["nợ", "trả nợ"],
                "property_value": ["giá nhà", "giá trị", "căn nhà"], "amount": ["khoản vay", "vay", "ngân sách"],
            }
            nearest = [(before.rfind(cue), key) for key, words in cues.items() for cue in words if cue in before]
            key = max(nearest)[1] if nearest else "amount"
            if re.search(r"(?<!mua )\bnhà\s*(?:giá|là|vẫn)?\s*$", before):
                key = "property_value"
            if not nearest and (re.match(r"\s*thu nhập", after) or ("khai" in before and "mỗi tháng" in after)):
                key = "income"
            if key == "amount" and any(cue in before for cue in ("hỗ trợ", "giúp thêm", "góp thêm", "cho thêm")):
                continue
            if key == "amount" and any(cue in clause for cue in ("hư cấu", "%", "ưu đãi")) and not any(
                cue in before for cue in ("vay", "ngân sách", "cùng", "phương án")
            ):
                continue
            slots[key] = (str(value.quantize(Decimal(1))), "đồng")
    return slots


def extract(text):
    low = text.lower()
    slots = {}
    if any(x in low for x in ["mua nhà", "vay nhà", "home_loan"]):
        slots["product"] = ("home_loan", "")
    elif any(x in low for x in ["tiêu dùng", "personal_loan"]):
        slots["product"] = ("personal_loan", "")
    elif any(x in low for x in ["thẻ", "credit_card"]):
        slots["product"] = ("credit_card", "")
    slots.update(_money_slots(low))
    if re.search(r"không (?:có |còn )?nợ|(?:nợ|trả nợ)(?: hàng tháng)?\s*(?:là |: )?0(?:\s|$|[.,;])", low):
        slots["debt"] = ("0", "đồng")
    for clause in _CLAUSES.split(low):
        # A promotion's duration is a bank claim, not the customer's loan term.
        if any(cue in clause for cue in ("%", "ưu đãi", "hư cấu", "trả sớm", "trả bớt")):
            continue
        term = re.search(r"(\d+)\s*(năm|tháng)", clause)
        if term:
            slots["term"] = (str(int(term[1]) * (12 if term[2] == "năm" else 1)), "tháng")
    if any(x in low for x in ["lãi cao", "lãi sau", "tăng lãi"]):
        slots["objection"] = ("rate_high", "")
    if any(x in low for x in ["chồng", "vợ", "gia đình"]):
        slots["objection"] = ("need_spouse", "")
    for clause in _CLAUSES.split(low):
        if any(x in clause for x in ["gọi lại", "hẹn", "liên hệ lại"]) and not re.search(
            r"(?:đừng|không|chưa)\b.*(?:hẹn|gọi|liên hệ)|(?:tự|sẽ)\s*(?:chủ động\s*)?liên hệ", clause,
        ):
            slots["commitment"] = ("Khách yêu cầu liên hệ lại", "")
    if re.search(r"\btôi (?:sẽ |quyết định |muốn )?(?:hoãn|dừng kế hoạch|chưa vay|không vay nữa)\b", low):
        slots["loan_status"] = ("tạm hoãn kế hoạch vay", "")
    return slots


def corrects(text, key):
    """Recognize an explicit current-value update for this field, not another clause."""
    clauses = re.split(r"[;\n]|\.(?!\d)|,(?!\d)|\s+(?:và|nhưng)\s+", text.lower())
    # An explicit confirmation of the whole new request also covers the
    # comma-separated fields in that sentence, without extending to later ones.
    clauses.extend(sentence for sentence in re.split(r"[;\n]|\.(?!\d)", text.lower())
                   if re.search(r"\bxác nhận nhu cầu mới\b", sentence))
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
    def __init__(self, db, settings=None, *, history_directory=None):
        self.db = db
        self.settings = settings
        self.history_directory = history_directory

    @staticmethod
    def _customer(customer):
        from harness.identity import resolve_identity

        return resolve_identity(customer) or customer

    def _historical(self, customer, as_of=None):
        if not getattr(self.settings, "identity_history_enabled", False):
            return {"facts": [], "history": [], "pending_topics": []}
        return project_history(
            customer, as_of or self.db.setting("clock"), extract, directory=self.history_directory,
        )

    def _legacy_demo_history_enabled(self):
        # OpenAI's explicit opt-out must not be reversed by the older demo flag.
        return bool(self.settings and self.settings.demo_phone_identity
                    and self.settings.llm_backend == "rules" and not self.settings.identity_history_enabled)

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
        deadline = current_deadline()
        if deadline is not None:
            wait_deadline = min(wait_deadline, deadline)
        waited = False
        with self.db.transaction() as c:
            row = c.execute("SELECT * FROM customer_sequences WHERE customer_id=?", (customer,)).fetchone()
            if not row:
                return
            target = row["next_seq"] if target_seq is None else min(target_seq, row["next_seq"])
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise LLMUnavailable("agent_turn_deadline")
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
        if getattr(self.settings, "identity_history_enabled", False):
            extracted.update(context_slots(payload["text"]))
        if suggestion and isinstance(suggestion.get("spans"), list):
            for span in suggestion["spans"][:20]:
                if isinstance(span, str) and span and span in payload["text"]:
                    for key, value in extract(span).items():
                        extracted.setdefault(key, value)
        baseline = {fact["key"]: fact for fact in self._historical(customer, payload["date"])["facts"]
                    if fact["status"] == "active"}
        for key, (value, unit) in extracted.items():
            correction = corrects(payload["text"], key)
            fid = ident("cf_", [turn, key, value])
            old = c.execute(
                "SELECT * FROM facts WHERE customer_id=? AND key=? AND source_type='customer' AND status IN ('active','conflicted')",
                (customer, key),
            ).fetchall()
            # Historical input is the initial context only. Once this key has a
            # runtime update, the usual runtime conflict/confirmation rules own it.
            if not old and key in baseline:
                old = [baseline[key]]
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
        customer = self._customer(customer)
        date = self.db.setting("clock")
        self.db.execute(
            "UPDATE facts SET status=CASE WHEN source_type='bank_tool' THEN 'expired' ELSE 'stale' END WHERE customer_id=? AND valid_until IS NOT NULL AND valid_until<? AND status='active'",
            (customer, date),
        )
        runtime = self.db.all("SELECT * FROM facts WHERE customer_id=? ORDER BY observed_at,id", (customer,))
        for fact in runtime:
            fact["source_origin"] = "runtime"
        if self._legacy_demo_history_enabled():
            from harness.demo_history import initial_facts

            return initial_facts(customer, date, runtime) + runtime
        historical = self._historical(customer)["facts"]
        product = next((fact["value"] for fact in historical if fact["key"] == "product" and fact["status"] == "active"), None)
        if product and any(fact["key"] == "product" and fact["source_type"] == "customer"
                           and fact["status"] in ("active", "conflicted")
                           and (fact["status"] == "conflicted" or fact["value"] != product) for fact in runtime):
            historical = [fact for fact in historical if fact["key"] not in ("amount", "term", "property_value", "loan_status")]
        return combine_facts(historical, runtime)

    def demo_history(self, customer):
        if not self._legacy_demo_history_enabled():
            return []
        from harness.demo_history import history

        return history(self._customer(customer), self.db.setting("clock"))

    def history(self, customer):
        """Authorized caller's source history; no LLM, replay, or queue mutation."""
        customer = self._customer(customer)
        history = self._historical(customer)["history"]
        for row in self.db.all(
            "SELECT t.id,t.session_id,t.text,t.created_at,s.channel,j.payload FROM turns t "
            "JOIN memory_jobs j ON j.turn_id=t.id JOIN sessions s ON s.id=t.session_id "
            "WHERE j.customer_id=? AND j.status='processed' ORDER BY t.created_at,t.id", (customer,),
        ):
            as_of = json.loads(row["payload"])["date"]
            if as_of > self.db.setting("clock"):
                continue
            history.append({
                "message_id": row["id"], "session_id": row["session_id"], "channel": row["channel"],
                "at": row["created_at"], "as_of": as_of, "role": "user", "text": row["text"],
                "source_origin": "runtime", "synthetic": False,
            })
        return history

    def profile(self, customer):
        return customer_profile(self.db, customer)

    def brief(self, customer, *, flush=True):
        from harness.identity import resolve_identity

        customer = resolve_identity(customer) or customer
        if flush:
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
            "segment": profile.get("segment", "mass"),
            "as_of": self.db.setting("clock"),
            "name_source": profile["name_source"],
            "cif": profile["cif"],
            "phone": phone["phone_e164"] if phone else profile.get("phone"),
            "facts": facts,
            "history": self.history(customer),
            "pending_topics": self._historical(customer)["pending_topics"],
            "needs": [f for f in facts if f["source_type"] in ("customer", "demo_history") and f["status"] == "active"],
            "demo_history": self.demo_history(customer),
            "conflicts": [f for f in facts if f["status"] == "conflicted"],
            "quotes": [f for f in facts if f["source_type"] == "bank_tool"],
            "warnings": [f for f in facts if f["status"] in ("expired", "stale", "conflicted")],
            "commitments": self.db.all("SELECT * FROM callbacks WHERE customer_id=? AND status='open'", (customer,)),
        }
