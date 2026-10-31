"""Reviewable dev improvement records and regression comparison."""

from __future__ import annotations

from typing import Any


def validate_changes(manifest: dict[str, Any]) -> None:
    changes = manifest.get("changes", [])
    if not 1 <= len(changes) <= 4:
        raise ValueError("A dev iteration requires 1–4 reviewed changes")
    ids = set()
    for change in changes:
        if not all(change.get(key) for key in ("change_id", "diff", "approved_by", "approved_at")):
            raise ValueError("Each change needs ID, diff and human approval receipt")
        if change["change_id"] in ids:
            raise ValueError("Duplicate change_id")
        ids.add(change["change_id"])


def regression(baseline: dict, candidate: dict) -> list[str]:
    if baseline["split"] != "dev" or candidate["split"] != "dev":
        raise ValueError("Tune/regress only on dev, never held-out")
    if baseline["status"] != "completed" or candidate["status"] != "completed":
        return ["incomplete_run"]
    for key in ("oracle_hash", "scenario_hash"):
        if baseline["metadata"][key] != candidate["metadata"][key]:
            raise ValueError(f"Cannot compare different {key}")
    problems = []
    for key in ("reask_rate", "price_error_rate", "missing_offer_rate", "handoff_completeness", "conversion_rate"):
        before, after = baseline["summary"]["harness"][key], candidate["summary"]["harness"][key]
        if before is None or after is None:
            if before != after:
                problems.append(key + ":missing_denominator")
            continue
        if (after < before) if key in ("handoff_completeness", "conversion_rate") else (after > before):
            problems.append(key)
    return problems
