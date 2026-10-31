from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Mode = Literal["harness", "no_memory", "full_history"]
Channel = Literal["web", "telegram"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionInput(Input):
    mode: Mode = "harness"
    demo_profile: str = "hoa"


class AdminSessionInput(SessionInput):
    channel: Channel = "web"


class MessageInput(Input):
    text: str = Field(min_length=1, max_length=8000)
    client_message_id: str = Field(min_length=1, max_length=128)


class VerifyInput(Input):
    phone: str = Field(min_length=1, max_length=64)


class PhoneLoginInput(Input):
    phone: str = Field(min_length=5, max_length=64)


class PhoneConfirmInput(Input):
    challenge_id: str = Field(min_length=20, max_length=100)
    code: str = Field(pattern=r"^\d{6}$")


class HandoffInput(Input):
    reason: str = Field(default="Khách yêu cầu chuyên viên", max_length=1000)


class SpecialistStartInput(Input):
    reason: str = Field(default="Khách yêu cầu tư vấn chuyên sâu", min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=1, max_length=128)


class SpecialistCompleteInput(Input):
    handoff_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)


class CompleteInput(Input):
    notes: str = Field(default="", max_length=4000)
    commitments: list[str] = Field(default_factory=list, max_length=20)
    idempotency_key: str = Field(default="complete", max_length=128)


class LoginInput(Input):
    password: str = Field(max_length=256)
