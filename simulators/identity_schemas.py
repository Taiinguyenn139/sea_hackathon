"""Authoring contract for synthetic phone identities and multi-session dialogue.

This dataset is independent of the live harness schema. Fixture identity checks
describe a scenario; they are never credentials or evidence of authentication.
"""

from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

PhoneId = Annotated[str, Field(strict=True, pattern=r"^0[0-9]{9}$")]
Channel = Literal["web", "telegram", "zalo", "hotline"]
Product = Literal["home_loan", "personal_loan"]
ToolName = Literal[
    "get_rate",
    "get_active_promotions",
    "get_card_offer",
    "calc_installment",
    "check_suitability",
    "get_disclosures",
    "send_summary",
    "schedule_callback",
]
Nonempty = Annotated[str, Field(min_length=1)]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChannelLink(Record):
    channel: Channel
    external_id: Annotated[str, Field(pattern=r"^synthetic:")]


class Identity(Record):
    user_id: PhoneId
    phone: PhoneId
    full_name: Nonempty
    date_of_birth: date
    bank_customer_id: Annotated[str, Field(pattern=r"^cus_[0-9]{3}$")]
    segment: Literal["mass", "priority"]
    runtime_profile: str | None = None
    channel_links: list[ChannelLink] = Field(min_length=1)

    @model_validator(mode="after")
    def canonical_identity(self):
        if self.phone != self.user_id:
            raise ValueError("user_id must equal the phone string")
        channels = [link.channel for link in self.channel_links]
        if len(set(channels)) != len(channels):
            raise ValueError("An identity has at most one link per channel")
        return self


class IdentityFile(Record):
    schema_version: Literal[1]
    synthetic: Literal[True]
    identities: list[Identity] = Field(min_length=1)


class Bank(Record):
    bank_id: Nonempty
    display_name: Nonempty


class Offer(Record):
    offer_id: Nonempty
    bank_id: Nonempty
    product: Product
    segment: Literal["mass", "priority"]
    channels: list[Channel] = Field(min_length=1)
    promo_rate: float = Field(ge=0, le=100, allow_inf_nan=False)
    promo_months: int = Field(strict=True, ge=1)
    after_rate: float = Field(ge=0, le=100, allow_inf_nan=False)
    annual_fee_vnd: int = Field(strict=True, ge=0)
    early_repayment_fee_pct: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    fee_note: Nonempty
    rate_unit: Literal["percent_per_year"]
    repayment_method: Literal["annuity"]
    valid_from: date
    valid_until: date
    as_of: date
    source_type: Literal["synthetic_fixture"]
    source_ref: Nonempty

    @model_validator(mode="after")
    def effective_period(self):
        if not self.valid_from <= self.as_of <= self.valid_until:
            raise ValueError("Offer as_of must fall within its effective period")
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("Duplicate offer channels")
        return self


class OfferFile(Record):
    schema_version: Literal[1]
    synthetic: Literal[True]
    banks: list[Bank] = Field(min_length=1)
    offers: list[Offer] = Field(min_length=1)


class UserMessage(Record):
    message_id: Nonempty
    user_id: PhoneId
    session_id: Nonempty
    channel: Channel
    at: AwareDatetime
    role: Literal["user"]
    text: Nonempty


class ChatSession(Record):
    session_id: Nonempty
    user_id: PhoneId
    channel: Channel
    external_id: Annotated[str, Field(pattern=r"^synthetic:")]
    as_of: date
    started_at: AwareDatetime
    identity_status: Literal["unverified", "verified"]
    verified_at: AwareDatetime | None = None
    logged_in: Literal[False]
    messages: list[UserMessage] = Field(min_length=1)

    @model_validator(mode="after")
    def session_verification(self):
        if (self.identity_status == "verified") != (self.verified_at is not None):
            raise ValueError("Verification is explicit and specific to each session")
        if self.verified_at and self.verified_at < self.started_at:
            raise ValueError("Verification cannot precede session start")
        return self


class Journey(Record):
    schema_version: Literal[1]
    scenario_id: Nonempty
    user_id: PhoneId
    title: Nonempty
    themes: list[Literal["family_decision", "bank_comparison", "cross_channel"]] = Field(min_length=1)
    product: Product
    offer_segment: Literal["mass", "priority"]
    sessions: list[ChatSession] = Field(min_length=1)


class Counts(Record):
    users: int = Field(strict=True, ge=1)
    sessions: int = Field(strict=True, ge=1)
    user_messages: int = Field(strict=True, ge=1)
    assistant_references: int = Field(strict=True, ge=1)


class Manifest(Record):
    schema_version: Literal[1]
    dataset_id: Nonempty
    synthetic: Literal[True]
    description: Nonempty
    business_timezone: Literal["Asia/Ho_Chi_Minh"]
    identity_file: Literal["identities.json"]
    offers_file: Literal["offers.json"]
    journey_files: list[Annotated[str, Field(pattern=r"^journeys/0[0-9]{9}\.json$")]] = Field(min_length=1)
    expected_counts: Counts
    supported_runtime_channels: list[Literal["web", "telegram"]]
    simulated_only_channels: list[Literal["zalo", "hotline"]]
    capability_gaps: list[Nonempty]


class ExpectedMemory(Record):
    key: Nonempty
    value: str | int | bool
    unit: str
    source_message_id: Nonempty
    status: Literal["active", "superseded", "conflicted"]


class QuoteExpectation(Record):
    offer_id: Nonempty
    source_message_id: Nonempty
    quoted_at: date
    status: Literal["active", "expired"]


class ReferenceResponse(Record):
    message_id: Nonempty
    user_id: PhoneId
    session_id: Nonempty
    channel: Channel
    at: AwareDatetime
    role: Literal["assistant"]
    in_reply_to: Nonempty
    reference_text: Nonempty
    checks: list[Nonempty] = Field(min_length=1)
    offer_ids: list[str]
    suggested_tools: list[ToolName]


class MemoryCheckpoint(Record):
    user_id: PhoneId
    session_id: Nonempty
    as_of: date
    cross_session_memory_allowed: bool = Field(strict=True)
    crm_allowed: Literal[False]
    visible_memory_keys: list[str]
    customer_memory: list[ExpectedMemory]
    pending_topics: list[str]
    quotes: list[QuoteExpectation]
    responses: list[ReferenceResponse] = Field(min_length=1)


class JourneyExpectations(Record):
    schema_version: Literal[1]
    scenario_id: Nonempty
    user_id: PhoneId
    checkpoints: list[MemoryCheckpoint] = Field(min_length=1)
