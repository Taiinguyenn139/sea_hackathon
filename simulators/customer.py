"""Seeded customer state machine driven only by public reply text."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Customer:
    scenario: dict[str, Any]
    seed: int
    stage: str = "discover"
    concerns: set[str] = field(default_factory=set)
    calls: int = 0
    converted: bool = False
    valid_offer_seen: bool = False

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        self.concerns = set(self.scenario.get("concerns", []))

    def message(self, step: dict[str, Any]) -> str:
        self.stage = step.get("stage", self.stage)
        choices = step.get("variants")
        return self.rng.choice(choices) if choices else step["text"]

    def observe(self, reply: str, step: dict[str, Any], offer_valid: bool) -> None:
        for concern, terms in step.get("addresses", {}).items():
            if any(term.casefold() in reply.casefold() for term in terms):
                self.concerns.discard(concern)
        if step.get("expect", {}).get("offer"):
            self.valid_offer_seen = offer_valid
        if step.get("stage") == "apply" and self.valid_offer_seen and offer_valid and not self.concerns:
            self.converted = True
