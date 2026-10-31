"""Frozen evaluation input verification; only nonsecret effective settings are hashed."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def effective_config(state: dict[str, Any]) -> dict[str, Any]:
    # Explicit allowlist avoids mutable fault/clock counters and credentials in metadata.
    return {
        "backend": state.get("backend"),
        "model": state.get("model"),
        "config": state.get("effective_config", {}),
        "fixture_hash": state.get("fixture_hash"),
    }


def verify_runtime(manifest: dict[str, Any], state: dict[str, Any]) -> None:
    if not state.get("fixture_hash") or not isinstance(state.get("effective_config"), dict):
        raise ValueError("Server must expose fixture_hash and nonsecret effective_config to verify freeze")
    if manifest["config_hash"] != canonical_hash(effective_config(state)):
        raise ValueError("Effective server configuration changed after freeze")
    if manifest["playbook_hash"] != canonical_hash(state.get("playbook")):
        raise ValueError("Effective playbook changed after freeze")
    if manifest.get("fixture_hash") != state["fixture_hash"]:
        raise ValueError("Bank fixture changed after freeze")
