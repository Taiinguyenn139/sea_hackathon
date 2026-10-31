import json
import time

from bank_tools.service import canonical, ident
from harness.customer_profile import customer_profile
from harness.llm import ExtractionSuggestion, LLMUnavailable, current_deadline, json_completion
from harness.memory import rules
from harness.memory.dataset_history import combine_facts, context_slots, project_history
from harness.memory.prompts import DEMO_HISTORY_INSTRUCTION, EXTRACTION_INSTRUCTION
from harness.memory.rules import corrects, extract


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
            customer, as_of or self.db.setting("clock"), rules.extract, directory=self.history_directory,
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
                use_llm = self.settings and self.settings.llm_backend == "openai"
                if use_llm and not allow_llm:
                    # Preflight checks ordering but leaves interpretation for the LLM worker.
                    return
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
                suggestion = self.interpret(payload["text"], payload["session_id"], job["turn_id"], customer)
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

    def interpret(self, text, session_id, turn_id, customer=None):
        history = self.db.all(
            "SELECT t.id,t.text,t.reply FROM turns t WHERE t.session_id=? AND t.rowid < "
            "(SELECT rowid FROM turns WHERE id=?) AND NOT EXISTS "
            "(SELECT 1 FROM memory_jobs j WHERE j.turn_id=t.id AND ? IS NOT NULL AND j.customer_id<>?) "
            "ORDER BY t.rowid DESC LIMIT 12", (session_id, turn_id, customer, customer),
        )[::-1]
        session = self.db.one("SELECT mode FROM sessions WHERE id=?", (session_id,))
        remembered = [
            {key: fact[key] for key in ("key", "value", "unit", "status", "channel", "source_ref")}
            for fact in self.facts(customer)
            if fact["source_type"] in ("customer", "demo_history") and fact["status"] in ("active", "conflicted")
        ] if customer and (not session or session["mode"] != "no_memory") else []
        result = json_completion(
            self.settings, EXTRACTION_INSTRUCTION,
            {"text": text, "recent_conversation": history, "remembered_facts": remembered},
            schema=ExtractionSuggestion, purpose="extract",
        )
        if result is None:
            raise LLMUnavailable("missing_extraction")
        return result

    def turn_extraction(self, turn_id):
        job = self.db.one("SELECT payload FROM memory_jobs WHERE turn_id=? AND status='processed'", (turn_id,))
        return json.loads(job["payload"]).get("extraction") if job else None

    @staticmethod
    def _finish(c, job, customer):
        c.execute(
            "UPDATE memory_jobs SET status='processed',lease_until=0,error=NULL WHERE turn_id=?", (job["turn_id"],)
        )
        c.execute("UPDATE customer_sequences SET processed_seq=? WHERE customer_id=?", (job["seq"], customer))

    def _write(self, c, turn, customer, payload, suggestion=None):
        if self.settings and self.settings.llm_backend == "openai":
            if suggestion is None:
                raise LLMUnavailable("missing_extraction")
            extraction = ExtractionSuggestion.model_validate(suggestion).model_dump()
            updates = extraction["facts"]
            # Cache exactly what the model decided, atomically with the facts and sequence.
            payload = {**payload, "extraction": extraction}
            c.execute("UPDATE memory_jobs SET payload=? WHERE turn_id=?", (canonical(payload), turn))
        else:
            extracted = extract(payload["text"])
            if getattr(self.settings, "identity_history_enabled", False):
                extracted.update(context_slots(payload["text"]))
            updates = [{"key": key, "value": value, "unit": unit, "operation": "replace" if corrects(payload["text"], key)
                        else "observe"} for key, (value, unit) in extracted.items()]
        baseline = {} if self.settings and self.settings.llm_backend == "openai" else {
            fact["key"]: fact for fact in self._historical(customer, payload["date"])["facts"] if fact["status"] == "active"
        }
        for update in updates:
            key, value, unit = update["key"], update["value"], update["unit"]
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
            if update["operation"] == "conflict" or (
                update["operation"] == "observe" and different
                and any(f["channel"] != payload["channel"] for f in different)
            ):
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

    def llm_demo_facts(self, customer, date, live):
        from harness.demo_history import filter_live, history

        messages = history(customer, date)
        if not messages:
            return []
        cache_key = ident("demo_llm_", [customer, date, messages, self.settings.openai_model, DEMO_HISTORY_INSTRUCTION])
        extraction = self.db.setting(cache_key)
        if extraction is None:
            extraction = json_completion(
                self.settings, DEMO_HISTORY_INSTRUCTION, {"history": messages},
                schema=ExtractionSuggestion, purpose="extract_demo_history",
            )
            if extraction is None:
                raise LLMUnavailable("missing_demo_extraction")
            self.db.set(cache_key, extraction)
        baseline = [{
            "id": ident("dh_", [customer, cache_key, fact["key"], fact["value"]]),
            "customer_id": customer, "key": fact["key"], "value": fact["value"], "unit": fact["unit"],
            "source_type": "demo_history", "source_ref": messages[-1]["source_ref"].split("#")[0] + "#llm",
            "source_text": fact["source_text"], "channel": "demo_history", "observed_at": date,
            "valid_until": None, "status": "conflicted" if fact["operation"] == "conflict" else "active",
            "confidence": 0.95,
        } for fact in extraction["facts"]]
        return filter_live(baseline, live)

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
