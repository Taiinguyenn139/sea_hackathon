"""Read-only demo history from user-authored journey inputs, never evaluator answers."""

import re
from functools import lru_cache

from bank_tools.service import ident
from harness.identity import get_profile
from simulators.identity_dataset import load_dataset

NEED_KEYS = {"product", "amount", "term", "income", "debt", "property_value", "loan_status"}
LOAN_DETAIL_KEYS = {"amount", "term", "property_value", "loan_status"}


@lru_cache(maxsize=1)
def dataset():
    return load_dataset()


def history(customer, as_of):
    profile = get_profile(customer)
    if not profile:
        return []
    messages = []
    for session in dataset().sessions_for(profile["user_id"]):
        # These flags filter trusted scenario inputs; they never authorize a live session.
        if session.identity_status != "verified" or session.as_of.isoformat() > as_of:
            continue
        for message in session.messages:
            if message.at < session.verified_at or message.at.date().isoformat() > as_of:
                continue
            messages.append({
                "id": message.message_id, "text": message.text, "channel": message.channel,
                "at": message.at.isoformat(),
                "source_ref": f"demo_history:identity_v1/journeys/{profile['user_id']}.json#{message.message_id}",
            })
    return messages


def declared_needs(text):
    from harness.memory.service import extract

    values = {}
    # Questions, advertised offer periods and rejected/hypothetical amounts are
    # context, not customer updates. Keep their original text in history only.
    for clause in re.split(r"(?<=[.!?;])\s+|,(?!\d)|\s+(?:nhưng|nên)\s+", text.lower()):
        if "?" in clause or "%" in clause or re.search(
            r"\b(?:nếu|giả sử|giả định|đừng|không (?:tự|lấy|cộng)|chưa xác nhận)\b", clause,
        ):
            continue
        clause = re.sub(r"^\s*nhà\b", "giá nhà", clause)
        parsed = {k: v for k, v in extract(clause).items() if k in NEED_KEYS}
        if not re.search(r"\b(?:vay|mức|phương án|ngân sách|cùng)\b", clause):
            parsed.pop("amount", None)
        if not re.search(r"\b(?:vay|mức|phương án|trong|vẫn|cùng)\b", clause):
            parsed.pop("term", None)
        values.update(parsed)
    return values


def initial_facts(customer, as_of, live_facts):
    latest = {}
    for message in history(customer, as_of):
        for key, (value, unit) in declared_needs(message["text"]).items():
            latest[key] = {
                "id": ident("dh_", [customer, message["id"], key, value]),
                "customer_id": customer,
                "key": key,
                "value": value,
                "unit": unit,
                "source_type": "demo_history",
                "source_ref": message["source_ref"],
                "channel": message["channel"],
                "observed_at": message["at"][:10],
                "valid_until": None,
                "status": "active",
                "confidence": 1.0,
            }
    # Even a conflicted/stale live field owns its key: never resurrect the demo value.
    live_keys = {fact["key"] for fact in live_facts if fact["source_type"] == "customer"}
    live_products = [
        fact for fact in live_facts
        if fact["key"] == "product"
        and fact["source_type"] == "customer"
        and fact["status"] in ("active", "conflicted")
    ]
    baseline_product = latest.get("product", {}).get("value")
    product_changed = any(
        fact["status"] == "conflicted" or fact["value"] != baseline_product
        for fact in live_products
    )
    if product_changed:
        live_keys.update(LOAN_DETAIL_KEYS)
    return [fact for key, fact in latest.items() if key not in live_keys]
