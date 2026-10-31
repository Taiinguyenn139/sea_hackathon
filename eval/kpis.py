"""Mechanical KPI counters; no model grading or SUT tool result as oracle."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from eval.oracle import numeric_claims

SLOT_TERMS = {
    "amount": ("bao nhiêu tiền", "số tiền", "vay bao nhiêu"),
    "term": ("kỳ hạn", "bao nhiêu năm", "thời hạn"),
    "income": ("thu nhập", "lương"),
    "debt": ("dư nợ", "nợ hiện tại"),
    "property_value": ("giá trị nhà", "giá trị tài sản"),
}


@dataclass
class Counters:
    redundant_questions: int = 0
    eligible_questions: int = 0
    price_errors: int = 0
    numeric_claims: int = 0
    provenance_errors: int = 0
    traced_replies: int = 0
    expected_offers: int = 0
    missing_offers: int = 0
    handoff_present: int = 0
    handoff_required: int = 0
    converted: int = 0
    scenarios: int = 0
    calls: int = 0
    failures: int = 0

    def add(self, other: Counters) -> None:
        for key, value in asdict(other).items():
            setattr(self, key, getattr(self, key) + value)

    def values(self) -> dict[str, float | None]:
        def ratio(a: int, b: int) -> float | None:
            return round(a / b, 6) if b else None

        return {
            "reask_rate": ratio(self.redundant_questions, self.eligible_questions),
            "price_error_rate": ratio(self.price_errors, self.numeric_claims),
            "provenance_error_rate": ratio(self.provenance_errors, self.traced_replies),
            "missing_offer_rate": ratio(self.missing_offers, self.expected_offers),
            "handoff_completeness": ratio(self.handoff_present, self.handoff_required),
            "calls_to_conversion": ratio(self.calls, self.converted) if self.converted == self.scenarios else None,
            "conversion_rate": ratio(self.converted, self.scenarios),
            "failure_rate": ratio(self.failures, self.scenarios),
        }


def score_reply(
    reply: str, expected: dict[str, Any], allowed: set, known_slots: set[str]
) -> tuple[Counters, list[str]]:
    counts = Counters()
    issues: list[str] = []
    lower = reply.casefold()
    # Confirmation of conflicting slots is explicitly scenario-authorized.
    confirmed = set(expected.get("confirmation_slots", []))
    if "?" in reply:
        for slot, terms in SLOT_TERMS.items():
            if slot in confirmed:
                continue
            if any(term in lower for term in terms):
                counts.eligible_questions += 1
                if slot in known_slots:
                    counts.redundant_questions += 1
                    issues.append(f"reasked:{slot}")
    claims = numeric_claims(reply)
    counts.numeric_claims = len(claims)
    for unit, value in claims:
        if (unit, value) not in allowed or expected.get("no_bank_numbers") and unit == "%":
            counts.price_errors += 1
            issues.append(f"unexpected_numeric_claim:{value}:{unit}")
    if expected.get("offer"):
        counts.expected_offers += 1
        required_unit = expected.get("offer_unit", "%")
        if not any(unit == required_unit for unit, _ in claims):
            counts.missing_offers += 1
            issues.append("missing_offer")
    for text in expected.get("forbidden_text", []):
        if text.casefold() in lower:
            issues.append(f"forbidden_text:{text}")
            counts.failures += 1
    return counts, issues


def handoff_score(summary: dict[str, Any], required: list[str]) -> Counters:
    counts = Counters(handoff_required=len(required))
    for key in required:
        value: Any = summary
        for part in key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if value is not None and value != "" and value != [] and value != {}:
            counts.handoff_present += 1
    return counts
