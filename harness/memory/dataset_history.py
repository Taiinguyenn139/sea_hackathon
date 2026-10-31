"""Project customer statements from synthetic inputs without replaying actions.

Only input journeys are read. Fixture verification selects historical statements;
it never changes the identity or permissions of the session asking for a brief.
"""

import hashlib
import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from simulators.identity_dataset import DEFAULT_DATASET_DIR, load_dataset, normalize_phone


@lru_cache(maxsize=8)
def _inputs(directory):
    return load_dataset(directory)


def context_slots(text):
    """Keep customer decisions and comparison requests, never official prices."""
    low = text.lower()
    slots = {}
    family = any(word in low for word in ("chồng", "vợ", "bố", "mẹ", "anh trai", "chị gái", "em trai", "gia đình"))
    if family and any(word in low for word in ("hỏi", "bàn", "đồng ý", "cần", "không tham gia")):
        slots["family_decision"] = (text, "")
    if any(word in low for word in ("hoãn", "quyết định chưa vay", "chưa chọn ngân hàng", "chờ văn bản", "muốn gặp chuyên viên")):
        slots["family_decision"] = (text, "")
    if "hư cấu" in low or "so ngân hàng" in low or "so với" in low:
        banks = []
        if "mây" in low:
            banks.append("cloud_fiction")
        if "sông" in low:
            banks.append("river_fiction")
        if banks:
            slots["comparison_banks"] = (",".join(banks), "")
    if "phí" in low and any(word in low for word in ("chưa rõ", "chưa xác nhận", "chưa biết", "chờ văn bản", "còn thiếu")):
        slots["comparison_pending"] = (text, "")
    return slots


def project_history(customer, as_of, extractor, *, directory=None):
    """Return dated, sourced customer facts and user-only history for one identity."""
    empty = {"facts": [], "history": [], "pending_topics": []}
    if not isinstance(customer, str) or not customer:
        return empty
    day = as_of if type(as_of) is date else date.fromisoformat(as_of)
    dataset = _inputs(str(Path(directory or DEFAULT_DATASET_DIR).resolve()))
    try:
        phone = normalize_phone(customer)
    except (TypeError, ValueError):
        phone = next((row.user_id for row in dataset.identities.values() if row.runtime_profile == customer), None)
    if phone not in dataset.journeys:
        return empty
    identity = dataset.identities[phone]
    customer_id = identity.runtime_profile or identity.user_id
    timezone = ZoneInfo(dataset.manifest.business_timezone)
    facts, history, topics = [], [], {}
    active = {}
    for session in dataset.sessions_for(phone):
        if session.identity_status != "verified" or session.as_of > day:
            continue
        for message in session.messages:
            if message.at.astimezone(timezone).date() > day:
                continue
            source = {
                "message_id": message.message_id,
                "session_id": session.session_id,
                "user_id": phone,
                "channel": message.channel,
                "at": message.at.isoformat(),
                "role": "user",
                "text": message.text,
                "source_origin": "dataset_history",
                "dataset_id": dataset.manifest.dataset_id,
                "synthetic": True,
            }
            history.append(source)
            slots = {**extractor(message.text), **context_slots(message.text)}
            for key, (value, unit) in slots.items():
                value = str(value)
                if key in active and active[key]["value"] == value:
                    continue
                if key in active:
                    active[key]["status"] = "superseded"
                digest = hashlib.sha256(json.dumps(
                    [dataset.manifest.dataset_id, phone, message.message_id, key, value],
                    ensure_ascii=False, separators=(",", ":"),
                ).encode()).hexdigest()[:24]
                fact = {
                    "id": "hf_" + digest,
                    "customer_id": customer_id,
                    "key": key,
                    "value": value,
                    "unit": unit,
                    "source_type": "customer",
                    "source_ref": message.message_id,
                    "channel": message.channel,
                    "observed_at": message.at.astimezone(timezone).date().isoformat(),
                    "valid_until": None,
                    "status": "active",
                    "confidence": 1.0,
                    "source_origin": "dataset_history",
                    "dataset_id": dataset.manifest.dataset_id,
                    "source_at": message.at.isoformat(),
                }
                facts.append(fact)
                active[key] = fact
                if key in ("family_decision", "comparison_banks", "comparison_pending"):
                    topics[key] = {"topic": key, "text": message.text, "source_ref": message.message_id,
                                   "channel": message.channel, "at": message.at.isoformat()}
    return {"facts": facts, "history": history, "pending_topics": list(topics.values())}


def combine_facts(historical, runtime):
    """Runtime facts take precedence; an unresolved update exposes both sides."""
    by_key = {}
    for fact in runtime:
        if fact["source_type"] == "customer" and fact["status"] in ("active", "conflicted"):
            by_key.setdefault(fact["key"], []).append(fact)
    result = []
    for source in historical:
        fact = dict(source)
        updates = by_key.get(fact["key"], [])
        if fact["status"] == "active" and updates:
            if any(update["status"] == "conflicted" for update in updates) and any(
                update["value"] != fact["value"] for update in updates
            ):
                fact["status"] = "conflicted"
            else:
                fact["status"] = "superseded"
        result.append(fact)
    return sorted(result + runtime, key=lambda fact: (fact["observed_at"], fact["id"]))
