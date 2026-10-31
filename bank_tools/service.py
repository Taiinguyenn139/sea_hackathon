import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from datetime import date as calendar_date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "bank.yaml"
FIXTURES = yaml.safe_load(FIXTURE_PATH.read_text())


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def ident(prefix, value):
    return prefix + hashlib.sha256(canonical(value).encode()).hexdigest()[:20]


def now():
    return datetime.now(UTC).isoformat()


class BankTools:
    def __init__(self, db):
        self.db = db
        self.fixtures = FIXTURES

    def call(self, tool, args, customer_id, session_id):
        fault = self.db.one("SELECT * FROM faults WHERE tool=? AND count>0", (tool,))
        tc = ident("tc_", [session_id, tool, args, self.db.setting("clock"), now()])
        if fault:
            with self.db.transaction() as c:
                fault = c.execute("SELECT * FROM faults WHERE tool=? AND count>0", (tool,)).fetchone()
                if fault:
                    c.execute("UPDATE faults SET count=count-1 WHERE tool=?", (tool,))
            if fault:
                result = {"facts": [], "data": {}, "error": fault["mode"]}
                self._save_call(tc, session_id, tool, args, result)
                return result
        try:
            data, values = self._invoke(tool, args, customer_id, session_id)
            facts = []
            date = self.db.setting("clock")
            for key, value, unit, expiry, label in values:
                fid = ident("f_", [1, tool, key, label, args, date])
                fact = {
                    "fact_id": fid,
                    "key": key,
                    "value": str(value),
                    "unit": unit,
                    "source_type": "bank_tool",
                    "source_ref": tc,
                    "channel": args.get("channel", "web"),
                    "observed_at": date,
                    "valid_until": expiry,
                    "status": "active",
                    "confidence": 1.0,
                    "label": label,
                }
                facts.append(fact)
            result = {"data": data, "facts": facts, "error": None, "tool_call_id": tc}
            with self.db.transaction() as c:
                c.execute(
                    "INSERT INTO tool_calls VALUES(?,?,?,?,?,?)",
                    (
                        tc,
                        session_id,
                        tool,
                        canonical({k: ("[redacted]" if k == "dob" else v) for k, v in args.items()}),
                        canonical(result),
                        now(),
                    ),
                )
                for f in facts:
                    c.execute(
                        "INSERT OR IGNORE INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            f["fact_id"],
                            customer_id,
                            f["key"],
                            f["value"],
                            f["unit"],
                            "bank_tool",
                            tc,
                            f["channel"],
                            date,
                            f["valid_until"],
                            "active",
                            1.0,
                        ),
                    )
            return result
        except (ValueError, KeyError, ArithmeticError) as e:
            result = {"data": {}, "facts": [], "error": "invalid: " + str(e)}
            self._save_call(tc, session_id, tool, args, result)
            return result

    def _save_call(self, tc, session, tool, args, result):
        self.db.execute(
            "INSERT INTO tool_calls VALUES(?,?,?,?,?,?)",
            (
                tc,
                session,
                tool,
                canonical({k: ("[redacted]" if k == "dob" else v) for k, v in args.items()}),
                canonical(result),
                now(),
            ),
        )

    def _invoke(self, tool, a, customer, session):
        date = self.db.setting("clock")
        product = a.get("product", "home_loan")
        p = self.fixtures["products"].get(product)
        if tool == "crm_lookup":
            match = next((x for x in self.fixtures["customers"] if a["identifier"] in (x["id"], x["cif"])), None)
            return ({"customer": match}, [])
        if tool == "verify_identity":
            match = next((x for x in self.fixtures["customers"] if x["id"] == customer), None)
            import secrets

            return ({"verified": bool(match and secrets.compare_digest(match["dob"], a["dob"]))}, [])
        if not p:
            raise ValueError("unknown product")
        expiry = self.fixtures["rate_valid_until"]
        if tool == "get_rate":
            if date > expiry:
                raise ValueError("rate table expired")
            return {}, [(k, p[k], "%/năm", expiry, product) for k in ("standard_rate", "floating_margin") if k in p]
        if tool == "get_active_promotions":
            promos = [
                x
                for x in self.fixtures["promotions"]
                if x["product"] == product
                and x["valid_from"] <= date <= x["valid_until"]
                and a.get("channel", "web") in x["channels"]
            ]
            if not promos:
                return {"promotions": []}, []
            promo = promos[-1]
            values = [("promo_months", promo["months"], "tháng", promo["valid_until"], promo["promo_id"])]
            for key, output, unit in [("rate", "promo_rate", "%/năm"), ("fee_waiver", "promo_fee_waiver", "đồng")]:
                if key in promo:
                    values.append((output, promo[key], unit, promo["valid_until"], promo["promo_id"]))
            return {"promo_id": promo["promo_id"]}, values
        if tool == "get_card_offer":
            p = self.fixtures["products"]["credit_card"]
            return {}, [
                (k, p[k], "đồng", expiry, product)
                for k in ("card_limit_min", "card_limit_max", "annual_fee", "fee_waiver_spend")
            ]
        if tool == "get_disclosures":
            return {"disclosures": p["disclosures"]}, []
        if tool == "check_suitability":
            income = Decimal(str(a.get("income", 0)))
            debt = Decimal(str(a.get("debt", 0)))
            amount = Decimal(str(a.get("amount", 0)))
            property_value = Decimal(str(a.get("property_value", 0)))
            reasons = []
            if income < Decimal(str(p["min_income"])):
                reasons.append("income_below_min")
            if income and debt / income > Decimal(p["max_dti"]):
                reasons.append("dti_too_high")
            if product == "home_loan" and property_value and amount / property_value > Decimal(p["max_ltv"]):
                reasons.append("ltv_too_high")
            return {"ok": not reasons, "reasons": reasons}, []
        if tool == "calc_installment":
            rf = self.db.one("SELECT * FROM facts WHERE id=? AND customer_id=?", (a["rate_fact_id"], customer))
            if (
                not rf
                or rf["source_type"] != "bank_tool"
                or rf["valid_until"] < date
                or Decimal(rf["value"]) != Decimal(str(a["rate"]))
            ):
                raise ValueError("invalid rate provenance")
            amount, term = Decimal(str(a["amount"])), int(a["term"])
            months = min(int(a.get("promo_months", 0)), term)
            if amount <= 0 or term <= 0 or term > 600:
                raise ValueError("invalid amount/term")
            rate = Decimal(str(a["rate"])) / 1200
            after = Decimal(str(a.get("after_rate", a["rate"]))) / 1200

            def payment(principal, n, r):
                return principal / n if not r else principal * r / (1 - (1 + r) ** (-n))

            first = payment(amount, term, rate)
            balance = (
                amount
                if not months
                else (
                    amount * (1 + rate) ** months - first * (((1 + rate) ** months - 1) / rate)
                    if rate
                    else amount - first * months
                )
            )
            later = payment(balance, term - months, after) if months < term else first
            total = first * months + later * (term - months)
            rounded = lambda v: (v / 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP) * 1000
            return {}, [
                (k, rounded(v), "đồng", rf["valid_until"], product)
                for k, v in [("installment_promo", first), ("installment_after", later), ("total_payment", total)]
            ]
        if tool == "schedule_callback":
            reason = a.get("reason", "").lower()
            offset = 1 if "ngày mai" in reason else 2 if "hai ngày" in reason else 0
            match = re.search(r"sau\s+(\d+)\s+ngày", reason)
            if match:
                offset = min(int(match[1]), 365)
            due = (
                a.get("time")
                or (calendar_date.fromisoformat(date) + timedelta(days=offset)).isoformat() + "T09:00:00+07:00"
            )
            key = ident("cb_", [customer, session, a.get("action_key", due)])
            self.db.execute(
                "INSERT OR IGNORE INTO callbacks VALUES(?,?,?,?,?,?)",
                (key, customer, session, due, "open", canonical({"reason": a.get("reason", "Liên hệ lại")})),
            )
            return {"task_id": key, "time": due}, []
        if tool == "send_summary":
            channel = a["channel"]
            link = self.db.one(
                "SELECT * FROM identity_links WHERE customer_id=? AND channel=?", (customer, channel)
            ) or self.db.one("SELECT external_id FROM guest_customers WHERE id=? AND channel=?", (customer, channel))
            if not link:
                raise ValueError("channel identity not linked")
            verified = self.db.one(
                "SELECT s.id FROM sessions s LEFT JOIN guest_customers g ON g.id=s.customer_id "
                "AND g.channel=s.channel AND g.external_id=s.external_id "
                "WHERE s.id=? AND s.customer_id=? AND s.channel=? AND (s.verified=1 OR g.id IS NOT NULL)",
                (session, customer, channel),
            )
            if not verified:
                raise ValueError("channel not verified")
            facts = [
                self.db.one("SELECT * FROM facts WHERE customer_id=? AND id=?", (customer, x)) for x in a["fact_ids"]
            ]
            if not facts or any(
                not f or f["status"] != "active" or (f["valid_until"] and f["valid_until"] < date) for f in facts
            ):
                raise ValueError("invalid summary facts")
            key = ident("out_", [customer, a.get("action_key", a["fact_ids"]), channel])
            body = "\n".join(f"{f['key']}: {f['value']} {f['unit']}" for f in facts)
            self.db.execute(
                "INSERT OR IGNORE INTO outbox(id,channel,target,text,status) VALUES(?,?,?,?,?)",
                (key, channel, link["external_id"], body, "pending"),
            )
            return {"message_id": key}, []
        raise ValueError("unknown tool")
