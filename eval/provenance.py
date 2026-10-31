"""Audit claimed provenance independently of tool implementation and renderer."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from eval.oracle import numeric_claims


def audit(
    reply: str,
    trace: dict[str, Any],
    expected: dict[str, Decimal],
    as_of: str,
    channel: str,
    customer_claims: list[list[Any]],
) -> list[str]:
    issues: list[str] = []
    facts = {f.get("id", f.get("fact_id")): f for f in trace.get("facts", [])}
    spans = trace.get("source_spans", [])
    customer = {(unit, Decimal(str(value))) for unit, value in customer_claims}
    covered = list(reply)
    for span in spans:
        start, end = span.get("start", -1), span.get("end", -1)
        fid = span.get("fact_id")
        fact = facts.get(fid)
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(reply):
            issues.append("invalid_source_span")
            continue
        covered[start:end] = " " * (end - start)
        if not fact or not fact.get("source_ref") or fact.get("source_ref") != span.get("source_ref"):
            issues.append(f"missing_fact_receipt:{fid}")
            continue
        rendered = numeric_claims(reply[start:end])
        try:
            value = Decimal(str(fact["value"]))
        except (InvalidOperation, KeyError):
            issues.append(f"invalid_fact_value:{fid}")
            continue
        unit = {"đồng": "VND", "%/năm": "%", "tháng": "months", "năm": "months",
                "ngày": "days", "tuổi": "age_years"}.get(fact.get("unit"), fact.get("unit"))
        if fact.get("unit") == "năm":
            value *= 12
        if not rendered or any(pair != (unit, value) for pair in rendered):
            issues.append(f"render_value_mismatch:{fid}")
        if fact.get("source_type") == "customer":
            if (unit, value) not in customer:
                issues.append(f"unknown_customer_claim:{fid}")
            continue
        if fact.get("source_type") != "bank_tool":
            issues.append(f"non_authoritative_source:{fid}")
            continue
        observed, expiry = fact.get("observed_at"), fact.get("valid_until")
        if not observed or observed > as_of or not expiry or expiry < as_of:
            issues.append(f"invalid_fact_time:{fid}")
        if fact.get("status") != "active":
            issues.append(f"inactive_fact:{fid}")
        if fact.get("key", "").startswith("promo_") and fact.get("channel") != channel:
            issues.append(f"wrong_promotion_channel:{fid}")
        key = fact.get("key")
        if key not in expected or expected[key] != value:
            issues.append(f"oracle_fact_mismatch:{key}")
        receipts = [
            f
            for call in trace.get("tool_calls", [])
            for f in call.get("result", {}).get("facts", [])
            if f.get("fact_id", f.get("id")) == fid and f.get("source_ref") == fact.get("source_ref")
        ]
        if not receipts:
            issues.append(f"missing_tool_receipt:{fid}")
    if re.search(r"\d", "".join(covered)):
        issues.append("untraced_numeric_claim")
    return issues
