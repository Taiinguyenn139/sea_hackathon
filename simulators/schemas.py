"""Scenario authoring contract, independent of the application being evaluated."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Slot = Literal["amount", "term", "income", "debt", "property_value", "product", "objection", "commitment"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReplyExpectation(Input):
    offer: bool = False
    offer_unit: Literal["%", "VND", "months"] = "%"
    no_bank_numbers: bool = False
    confirmation_slots: list[Slot] = Field(default_factory=list)
    forbidden_text: list[str] = Field(default_factory=list)


class Fault(Input):
    tool: str = Field(min_length=1)
    mode: Literal["timeout", "error"]
    count: int = Field(ge=1)


class Step(Input):
    text: str | None = Field(default=None, min_length=1)
    variants: list[str] | None = Field(default=None, min_length=1)
    reveals: list[Slot] = Field(default_factory=list)
    expect: ReplyExpectation = Field(default_factory=ReplyExpectation)
    stage: Literal["discover", "quote", "objection", "followup", "apply"] | None = None
    addresses: dict[str, list[str]] = Field(default_factory=dict)
    handoff: bool = False
    handoff_required: list[str] = Field(default_factory=list)
    duplicate: bool = False
    fault: Fault | None = None

    @model_validator(mode="after")
    def has_message(self):
        if not self.text and not self.variants:
            raise ValueError("Step requires text or variants")
        if self.variants and any(not text.strip() for text in self.variants):
            raise ValueError("Variants must not contain empty messages")
        return self


class Session(Input):
    channel: Literal["web", "telegram"] = "web"
    as_of: date
    verify: bool = True
    steps: list[Step] = Field(min_length=1)


class Loan(Input):
    amount: str = Field(pattern=r"^[1-9][0-9]*$")
    term: int = Field(ge=1, le=600)


class Scenario(Input):
    scenario_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    profile: str = "hoa"
    dob: date
    product: Literal["home_loan", "personal_loan", "credit_card"]
    customer_claims: list[tuple[Literal["VND", "months", "%"], str | int | float]] = Field(default_factory=list)
    sessions: list[Session] = Field(min_length=1)
    loan: Loan | None = None
    card_income: int | None = Field(default=None, ge=0)
    concerns: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def ordered_sessions(self):
        dates = [session.as_of for session in self.sessions]
        if dates != sorted(dates):
            raise ValueError("Session business dates must be nondecreasing")
        return self
