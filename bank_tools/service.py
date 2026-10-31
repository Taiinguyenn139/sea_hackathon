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


# Preserve the adapter export used by the engine and staff brief. DOB and
# customer mapping now have one source: the validated ten-identity registry.
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
    digest.update(b"identity_v1/identities.json\0" + IDENTITY_PATH.read_bytes())
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

    def call(self, tool, args, customer_id, session_id, *, context=None, idempotency_key=None):
        with self.lock:
            tc = ident("tc_", [session_id, tool, args, now()])
            result = {"tool_call_id": tc, "tool": tool, "as_of": None, "data": {}, "facts": [], "error": None}
            try:
                if tool == "verify_identity":
                    # The server-bound session selects the registry identity. The
                    # caller supplies a DOB, never the identity being verified.
                    session = self.db.one("SELECT * FROM sessions WHERE id=?", (session_id,))
                    if not session or session["customer_id"] != customer_id or session["status"] == "ended":
                        raise AccessDenied("forbidden: invalid verification session")
                    result["as_of"] = date.fromisoformat(args["as_of"]).isoformat()
                    result["data"] = {"verified": verify_identity(customer_id, args["dob"])}
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
                context = context if context is not None else self.auth_context(customer_id, session_id)
                if context is not None and (not isinstance(context, AuthContext) or context.customer_id != customer_id):
                    raise AccessDenied("forbidden: customer_id does not match backend context")
                native_context = (
                    AuthContext(self.bank_id(customer_id), context.logged_in, context.identity_verified)
                    if context is not None
                    else None
                )
                if TOOL_ACCESS[tool] != "public":
                    require_access(tool, native_context, self.bank_id(customer_id))
                    if "customer_id" in args or "context" in args:
                        raise AccessDenied("forbidden: customer identity is supplied only by the backend")
                client = self.clients.setdefault((session_id, customer_id), None)
                if client is None:
                    client = self.clients[(session_id, customer_id)] = ToolClient()
                # Validate required arguments/date before consuming a counted injected fault.
                bound_args = dict(args)
                if TOOL_ACCESS[tool] != "public":
                    bound_args.update(customer_id=self.bank_id(customer_id), context=native_context)
                signature(getattr(client, tool)).bind(**bound_args)
                with self.db.transaction() as connection:
                    fault = connection.execute("SELECT * FROM faults WHERE tool=? AND count>0", (tool,)).fetchone()
                    if fault:
                        connection.execute("UPDATE faults SET count=count-1 WHERE tool=?", (tool,))
                if fault:
                    client.inject_fault(tool, fault["mode"], 1)
                result = client.call(tool, args, context=native_context)
                if not result["error"]:
                    for fact in result["facts"]:
                        fact["channel"] = fact["metadata"].get("channel", session["channel"] if session else "web")
                    if tool == "crm_lookup" and result["data"].get("found"):
                        result["data"]["customer_id"] = customer_id
                    if tool in ("schedule_callback", "send_summary"):
                        self._save_action(tool, args, result, customer_id, session_id, idempotency_key)
            except AccessDenied as exc:
                result.update(data={}, facts=[], error=str(exc))
            except (ValueError, TypeError, KeyError) as exc:
                result.update(data={}, facts=[], error="invalid: " + str(exc))
            storage_customer = customer_id if isinstance(context, AuthContext) and (context.logged_in or context.identity_verified) else "guest:" + session_id
            self._save_call(session_id, tool, args, result, storage_customer)
            return result

    def _save_action(self, tool, args, result, customer, session, action_key):
        data = result["data"]
        key = ident("cb_" if tool == "schedule_callback" else "out_", [session, tool, action_key or data])
        if tool == "schedule_callback":
            previous = self.db.one("SELECT payload FROM callbacks WHERE id=?", (key,))
            if previous and json.loads(previous["payload"])["request"] != args:
                raise ValueError("idempotency key was used for a different callback")
            data["task_id"] = key
            self.db.execute(
                "INSERT OR IGNORE INTO callbacks VALUES(?,?,?,?,?,?)",
                (key, customer, session, data["time"], "open", canonical({"request": args, "mock": True})),
            )
        else:
            previous = self.db.one("SELECT receipt FROM outbox WHERE id=?", (key,))
            if previous and json.loads(previous["receipt"])["request"] != args:
                raise ValueError("idempotency key was used for a different summary")
            data["message_id"] = key
            # Canonical tool is a mock. Never silently enqueue a real provider send.
            snapshot = self.clients[(session, customer)].get_mock_actions()["messages"][-1]["facts"]
            self.db.execute(
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

    def _save_call(self, session, tool, args, result, customer):
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO tool_calls VALUES(?,?,?,?,?,?)",
                (
                    result["tool_call_id"],
                    session,
                    tool,
                    canonical({k: "[redacted]" if k == "dob" else v for k, v in args.items()}),
                    canonical(result),
                    now(),
                ),
            )
            for fact in result["facts"]:
                connection.execute(
                    "INSERT OR REPLACE INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        fact["fact_id"],
                        customer,
                        fact["key"],
                        str(fact["value"]),
                        fact["unit"],
                        "bank_tool",
                        fact["source_ref"],
                        fact["channel"],
                        fact["observed_at"],
                        fact["valid_until"],
                        fact["status"],
                        fact["confidence"],
                    ),
                )
