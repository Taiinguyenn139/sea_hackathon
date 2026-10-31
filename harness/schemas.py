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
    dob: str = Field(max_length=10)


class HandoffInput(Input):
    reason: str = Field(default="Khách yêu cầu chuyên viên", max_length=1000)


class CompleteInput(Input):
    notes: str = Field(default="", max_length=4000)
    commitments: list[str] = Field(default_factory=list, max_length=20)
    idempotency_key: str = Field(default="complete", max_length=128)


class LoginInput(Input):
    password: str = Field(max_length=256)
