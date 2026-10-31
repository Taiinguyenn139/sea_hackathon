"""Evaluator-owned checkpoint schema and public Call Brief comparison.

Never import harness, bank_tools, or their database. Expectations are curated
independently; runtime IDs are resolved from public traces, not hardcoded.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpectedFact(Contract):
    key: str = Field(min_length=1)
    value: str
    unit: str
    source_type: Literal["customer", "bank_tool"]
    status: Literal["active", "conflicted", "superseded", "expired", "stale"]
    channel: Literal["web", "telegram"]
    observed_at: date
    valid_until: date | None = None
    source_step: tuple[int, int] | None = None

    @model_validator(mode="after")
    def source_required(self):
        if self.source_type == "customer" and self.source_step is None:
            raise ValueError("Customer fact requires [session_number, zero_based_step]")
        if self.source_step and (self.source_step[0] < 1 or self.source_step[1] < 0):
            raise ValueError("Invalid source step")
        if self.source_type == "bank_tool" and (self.valid_until is None or self.source_step is not None):
            raise ValueError("Bank fact requires expiry and resolves its source through tool receipts")
        return self


class Checkpoint(Contract):
    session_number: int = Field(ge=1)
    as_of: date
    conflict_keys: list[str]
    unique_active_keys: list[str]
    facts: list[ExpectedFact] = Field(min_length=1)

    @model_validator(mode="after")
    def temporal_consistency(self):
        for fact in self.facts:
            if fact.observed_at > self.as_of:
                raise ValueError("Checkpoint cannot contain future facts")
            if fact.source_step and fact.source_step[0] > self.session_number:
                raise ValueError("Checkpoint cannot cite future session")
            if fact.valid_until and fact.valid_until < self.as_of and fact.status == "active":
                raise ValueError("Expired fact cannot remain active")
        if set(self.conflict_keys) & set(self.unique_active_keys):
            raise ValueError("Conflicted slot cannot also require an active value")
        signatures = [fact.model_dump_json() for fact in self.facts]
        if len(set(signatures)) != len(signatures):
            raise ValueError("Duplicate expected fact")
        return self


class MemoryContract(Contract):
    schema_version: Literal[1]
    scenario_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    customer_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    modes: list[Literal["harness", "no_memory", "full_history"]] = Field(min_length=1)
    checkpoints: list[Checkpoint] = Field(min_length=1)

    @model_validator(mode="after")
    def ordered_checkpoints(self):
        numbers = [cp.session_number for cp in self.checkpoints]
        if numbers != sorted(set(numbers)):
            raise ValueError("Checkpoint sessions must be ordered and unique")
        return self

    def validate_scenario(self, scenario: dict) -> None:
        if self.scenario_id != scenario["scenario_id"] or self.customer_id != scenario.get("profile", "hoa"):
            raise ValueError("Memory contract does not match scenario/customer")
        for cp in self.checkpoints:
            if cp.session_number > len(scenario["sessions"]):
                raise ValueError("Checkpoint references missing session")
            session = scenario["sessions"][cp.session_number - 1]
            if cp.as_of.isoformat() != session.get("as_of", "2026-10-31") or not session.get("verify", True):
                raise ValueError("Checkpoint must follow a verified session on the same business date")
            for fact in cp.facts:
                if fact.source_step:
                    number, step = fact.source_step
                    source = scenario["sessions"][number - 1]
                    if step >= len(source["steps"]):
                        raise ValueError("Fact references missing source step")
                    if fact.channel != source.get("channel", "web"):
                        raise ValueError("Fact source channel mismatch")
                    if fact.observed_at.isoformat() != source.get("as_of", "2026-10-31"):
                        raise ValueError("Fact source date mismatch")


def score_memory(
    brief: dict, checkpoint: Checkpoint, customer_id: str,
    turn_ids: dict[tuple[int, int], str], tool_receipts: dict[str, dict],
) -> list[str]:
    """Match required facts, not a whole DB dump; reject duplicates and foreign data."""
    issues = []
    if brief.get("redacted") is not False or brief.get("customer_id") != customer_id:
        return ["memory:wrong_customer_or_redacted"]
    facts = brief.get("facts")
    if not isinstance(facts, list) or any(not isinstance(f, dict) for f in facts):
        return ["memory:invalid_facts"]
    for fact in facts:
        if fact.get("customer_id") != customer_id:
            issues.append("memory:foreign_customer_fact")
        observed = fact.get("observed_at")
        try:
            if date.fromisoformat(observed) > checkpoint.as_of:
                issues.append("memory:future_fact")
        except (ValueError, TypeError):
            issues.append("memory:invalid_observed_at")
    for expected in checkpoint.facts:
        fields = expected.model_dump(mode="json", exclude={"source_step"}, exclude_none=True)
        matches = [f for f in facts if all(f.get(k) == v for k, v in fields.items())]
        label = f"{expected.key}:{expected.value}:{expected.observed_at}:{expected.status}"
        if len(matches) != 1:
            issues.append(f"memory:expected_one:{label}:found={len(matches)}")
            continue
        actual = matches[0]
        if expected.status in ("expired", "stale", "conflicted"):
            warnings = brief.get("warnings", [])
            if not isinstance(warnings, list) or not any(
                isinstance(warning, dict) and warning.get("id") == actual.get("id") for warning in warnings
            ):
                issues.append(f"memory:missing_warning:{label}")
        if expected.source_step:
            source = turn_ids.get(expected.source_step)
            if not source or actual.get("source_ref") != source:
                issues.append(f"memory:customer_provenance:{label}")
        else:
            receipt = tool_receipts.get(actual.get("source_ref"), {})
            emitted = receipt.get("result", {}).get("facts", [])
            fields_to_match = ("key", "value", "unit", "channel", "observed_at", "valid_until", "source_ref")
            if not any(
                isinstance(f, dict) and f.get("fact_id") == actual.get("id")
                and all(f.get(k) == actual.get(k) for k in fields_to_match)
                for f in emitted
            ):
                issues.append(f"memory:bank_provenance:{label}")
    conflicts = {f.get("key") for f in facts if f.get("status") == "conflicted"}
    listed_conflicts = brief.get("conflicts", [])
    if not isinstance(listed_conflicts, list) or any(not isinstance(f, dict) for f in listed_conflicts):
        return issues + ["memory:invalid_conflicts"]
    brief_conflicts = {f.get("key") for f in listed_conflicts}
    if conflicts != set(checkpoint.conflict_keys) or brief_conflicts != conflicts:
        issues.append("memory:conflicts_mismatch")
    for key in checkpoint.conflict_keys:
        if any(f.get("key") == key and f.get("status") == "active" for f in facts):
            issues.append(f"memory:active_conflicted_slot:{key}")
    for key in checkpoint.unique_active_keys:
        matches = [f for f in facts if f.get("key") == key and f.get("source_type") == "customer"
                   and f.get("status") == "active"]
        if len(matches) != 1:
            issues.append(f"memory:active_count:{key}:{len(matches)}")
    return issues
