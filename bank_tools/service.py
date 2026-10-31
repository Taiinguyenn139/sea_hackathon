"""SQLite adapter for the canonical tuannv tools; authentication remains server-owned."""

import hashlib
import json
import threading
from datetime import UTC, date, datetime
from inspect import signature
from pathlib import Path

import yaml

from bank_tools.auth import TOOL_ACCESS, AccessDenied, AuthContext, require_access
from bank_tools.catalog import TOOL_NAMES, get_tool_definitions
from bank_tools.core import BankTools as ToolClient
from harness.identity import IDENTITY_PATH, get_profile, profiles, verify_identity

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "bank.yaml"


def _customer_profiles():
    customers = yaml.safe_load((FIXTURE_PATH.parent / "customers.yaml").read_text())["customers"]
    by_id = {}
    cifs = set()
    for customer in customers:
        key, cif = customer["customer_id"], customer["cif"]
        if key in by_id or cif in cifs:
            raise ValueError("Duplicate customer_id or CIF in bank fixtures")
        by_id[key] = customer
        cifs.add(cif)
    joined = []
    for profile in profiles():
        customer = by_id.get(profile["bank_customer_id"])
        if not customer or (customer["full_name"], customer["segment"]) != (profile["name"], profile["segment"]):
            raise ValueError("Identity mapping does not match the bank customer fixture")
        joined.append({**profile, "cif": customer["cif"]})
    return joined


# Preserve the adapter export used by the engine and staff brief. Phone and
# customer mapping have one source: the validated ten-identity registry.
FIXTURES = {"schema_version": 2, "customers": _customer_profiles()}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def ident(prefix, value):
    return prefix + hashlib.sha256(canonical(value).encode()).hexdigest()[:20]


def now():
    return datetime.now(UTC).isoformat()


def fixture_hash():
    digest = hashlib.sha256()
    for path in sorted(FIXTURE_PATH.parent.glob("*.yaml")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    dataset = IDENTITY_PATH.parent
    for path in sorted(dataset.rglob("*.json")):
        digest.update(("identity_v1/" + path.relative_to(dataset).as_posix()).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


class BankTools:
    def __init__(self, db):
        self.db = db
        self.clients = {}
        self.lock = threading.RLock()

    def auth_context(self, customer_id, session_id):
        session = self.db.one("SELECT * FROM sessions WHERE id=?", (session_id,))
        if not session or session["customer_id"] != customer_id or session["status"] == "ended":
            return None
        # A customer capability or a staff login is NOT a customer login.
        return AuthContext(customer_id=customer_id, identity_verified=bool(session["verified"]))

    @staticmethod
    def bank_id(customer_id):
        profile = get_profile(customer_id)
        return profile["bank_customer_id"] if profile else customer_id

    def definitions(self, customer_id, session_id):
        return get_tool_definitions(context=self.auth_context(customer_id, session_id))

    def _owns_memory(self, customer_id, session_id):
        # Channel/OTP ownership permits storing public advisory facts under a
        # prospect's own key. It grants no canonical private tool permission.
        return self.db.one(
            "SELECT s.id FROM sessions s LEFT JOIN guest_customers g ON g.id=s.customer_id "
            "AND g.channel=s.channel AND g.external_id=s.external_id "
            "LEFT JOIN phone_sessions p ON p.session_id=s.id AND p.customer_id=s.customer_id "
            "WHERE s.id=? AND s.customer_id=? AND s.status!='ended' "
            "AND (s.verified=1 OR g.id IS NOT NULL OR p.session_id IS NOT NULL)",
            (session_id, customer_id),
        ) is not None

    def _client(self, session, customer, as_of):
        key = (session["id"], customer)
        if key in self.clients:
            return self.clients[key]
        client = ToolClient()
        # Only committed operations issued to this exact live session/customer
        # may restore a native instance's private fact store. Model facts and
        # arbitrary rows in the public facts table are never recovery sources.
        for row in self.db.all(
            "SELECT result FROM agent_tool_receipts WHERE session_id=? AND customer_id=? ORDER BY rowid",
            (session["id"], customer),
        ):
            result = json.loads(row["result"])
            if result.get("error") or result.get("as_of") != as_of:
                continue
            audit = self.db.one("SELECT session_id,result FROM tool_calls WHERE id=?", (result["tool_call_id"],))
            if not audit or audit["session_id"] != session["id"] or json.loads(audit["result"]) != result:
                continue
            facts = [fact for fact in result.get("facts", []) if (
                fact.get("source_ref") == result["tool_call_id"]
                and fact.get("tool") == result["tool"]
                and fact.get("observed_at") == as_of
                and fact.get("status") == "active"
                and fact.get("valid_until", "") >= as_of
                and fact.get("channel") == session["channel"]
                and fact.get("metadata", {}).get("channel", session["channel"]) == session["channel"]
            )]
            client._restore_issued_facts(facts, as_of=as_of)
        self.clients[key] = client
        return client

    def call(self, tool, args, customer_id, session_id, *, context=None, idempotency_key=None):
        with self.lock:
            tc = ident("tc_", [session_id, tool, args, now()])
            result = {"tool_call_id": tc, "tool": tool, "as_of": None, "data": {}, "facts": [], "error": None}
            try:
                if tool == "verify_identity":
                    # Compatibility reader only; agent binding is an atomic
                    # session-owned operation in Harness.agent_verify.
                    session = self.db.one("SELECT * FROM sessions WHERE id=?", (session_id,))
                    if not session or session["customer_id"] != customer_id or session["status"] == "ended":
                        raise AccessDenied("forbidden: invalid verification session")
                    result["as_of"] = date.fromisoformat(args["as_of"]).isoformat()
                    result["data"] = {"verified": verify_identity(customer_id, args["phone"])}
                    self._save_call(session_id, tool, args, result, customer_id)
                    return result
                if tool not in TOOL_NAMES:
                    raise ValueError("unknown tool")
                if not isinstance(args, dict) or "as_of" not in args:
                    raise ValueError("as_of is required for every tool")
                as_of = date.fromisoformat(args["as_of"]).isoformat()
                if as_of != args["as_of"]:
                    raise ValueError("as_of must use YYYY-MM-DD")
                result["as_of"] = as_of
                session = self.db.one("SELECT * FROM sessions WHERE id=?", (session_id,))
                if session and (session["customer_id"] != customer_id or session["status"] == "ended"):
                    raise AccessDenied("forbidden: session does not belong to the current customer")
                if idempotency_key is not None and (
                    not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 256
                    or not session or session["status"] != "active"
                ):
                    raise AccessDenied("forbidden: tool operation requires an active bound session and operation key")
                context = context if context is not None else self.auth_context(customer_id, session_id)
                if context is not None and (not isinstance(context, AuthContext) or context.customer_id != customer_id):
                    raise AccessDenied("forbidden: customer_id does not match backend context")
                native_context = (
                    AuthContext(self.bank_id(customer_id), context.logged_in, context.identity_verified)
                    if context is not None else None
                )
                if TOOL_ACCESS[tool] != "public":
                    require_access(tool, native_context, self.bank_id(customer_id))
                    if "customer_id" in args or "context" in args:
                        raise AccessDenied("forbidden: customer identity is supplied only by the backend")
                request_hash = ident("request_", [tool, args])
                previous = self.db.one(
                    "SELECT * FROM agent_tool_receipts WHERE operation_id=?", (idempotency_key,),
                ) if idempotency_key is not None else None
                if previous and (
                    previous["session_id"] != session_id or previous["customer_id"] != customer_id
                    or previous["tool"] != tool or previous["request_hash"] != request_hash
                ):
                    raise AccessDenied("conflict: tool operation key belongs to another request")
                client = self._client(session, customer_id, as_of) if session else self.clients.setdefault(
                    (session_id, customer_id), ToolClient(),
                )
                if previous:
                    return json.loads(previous["result"])
                bound_args = dict(args)
                if TOOL_ACCESS[tool] != "public":
                    bound_args.update(customer_id=self.bank_id(customer_id), context=native_context)
                signature(getattr(client, tool)).bind(**bound_args)
            except AccessDenied as exc:
                result.update(data={}, facts=[], error=str(exc))
                self._save_call(session_id, tool, args, result, "guest:" + session_id)
                return result
            except (ValueError, TypeError, KeyError) as exc:
                result.update(data={}, facts=[], error="invalid: " + str(exc))
                self._save_call(session_id, tool, args, result, "guest:" + session_id)
                return result
            # Native tools are local mocks. A single SQLite transaction commits
            # their action, audit facts and operation receipt together. A crash
            # after commit can return the exact native receipt without execution.
            try:
                with self.db.transaction() as connection:
                    fault = connection.execute("SELECT * FROM faults WHERE tool=? AND count>0", (tool,)).fetchone()
                    if fault:
                        connection.execute("UPDATE faults SET count=count-1 WHERE tool=?", (tool,))
                        client.inject_fault(tool, fault["mode"], 1)
                    result = client.call(tool, args, context=native_context)
                    if not result["error"]:
                        for fact in result["facts"]:
                            fact["channel"] = fact["metadata"].get("channel", session["channel"] if session else "web")
                        if tool == "crm_lookup" and result["data"].get("found"):
                            result["data"]["customer_id"] = customer_id
                        if tool in ("schedule_callback", "send_summary"):
                            self._save_action(tool, args, result, customer_id, session_id, idempotency_key, connection)
                    has_identity = isinstance(context, AuthContext) and (context.logged_in or context.identity_verified)
                    storage_customer = customer_id if has_identity or self._owns_memory(customer_id, session_id) else "guest:" + session_id
                    self._save_call(session_id, tool, args, result, storage_customer, connection=connection)
                    if idempotency_key is not None:
                        connection.execute("INSERT INTO agent_tool_receipts VALUES(?,?,?,?,?,?,?)", (
                            idempotency_key, session_id, customer_id, tool, request_hash, canonical(result), now(),
                        ))
                return result
            except BaseException:
                # Rollback removes durable effects; discard any in-memory mock
                # mutations or newly issued facts from the unsuccessful attempt.
                self.clients.pop((session_id, customer_id), None)
                raise

    def _save_action(self, tool, args, result, customer, session, action_key, connection):
        data = result["data"]
        key = ident("cb_" if tool == "schedule_callback" else "out_", [session, tool, action_key or data])
        if tool == "schedule_callback":
            previous = connection.execute("SELECT payload FROM callbacks WHERE id=?", (key,)).fetchone()
            if previous and json.loads(previous["payload"])["request"] != args:
                raise ValueError("idempotency key was used for a different callback")
            data["task_id"] = key
            connection.execute(
                "INSERT OR IGNORE INTO callbacks VALUES(?,?,?,?,?,?)",
                (key, customer, session, data["time"], "open", canonical({"request": args, "mock": True})),
            )
        else:
            previous = connection.execute("SELECT receipt FROM outbox WHERE id=?", (key,)).fetchone()
            if previous and json.loads(previous["receipt"])["request"] != args:
                raise ValueError("idempotency key was used for a different summary")
            data["message_id"] = key
            # Canonical tool is a mock. Never silently enqueue a real provider send.
            snapshot = self.clients[(session, customer)].get_mock_actions()["messages"][-1]["facts"]
            connection.execute(
                "INSERT OR IGNORE INTO outbox(id,channel,target,text,status,receipt) VALUES(?,?,?,?,?,?)",
                (
                    key,
                    args["channel"],
                    "demo:" + customer,
                    canonical(snapshot),
                    "mock_sent",
                    canonical({"request": args, "mock": True}),
                ),
            )

    def _save_call(self, session, tool, args, result, customer, *, connection=None):
        if connection is None:
            with self.db.transaction() as transaction:
                self._save_call(session, tool, args, result, customer, connection=transaction)
            return
        connection.execute(
            "INSERT INTO tool_calls VALUES(?,?,?,?,?,?)",
            (
                result["tool_call_id"], session, tool,
                canonical({k: "[redacted]" if k in ("phone", "dob") else v for k, v in args.items()})
                if isinstance(args, dict) else canonical({"invalid_arguments": True}),
                canonical(result), now(),
            ),
        )
        for fact in result["facts"]:
            connection.execute(
                "INSERT OR REPLACE INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fact["fact_id"], customer, fact["key"], str(fact["value"]), fact["unit"], "bank_tool",
                    fact["source_ref"], fact["channel"], fact["observed_at"], fact["valid_until"],
                    fact["status"], fact["confidence"],
                ),
            )
