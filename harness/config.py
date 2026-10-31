import math
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    ai_specialist_enabled: bool = field(default_factory=lambda: os.getenv("AI_SPECIALIST_ENABLED", "false").lower() == "true")
    sales_sandbox: bool = False
    demo_phone_identity: bool = field(default_factory=lambda: os.getenv("DEMO_PHONE_IDENTITY", "false").lower() == "true")
    db_path: str = os.getenv("HARNESS_DB", "data/harness.sqlite3")
    admin_token: str = os.getenv("ADMIN_TOKEN", "")
    staff_password: str = os.getenv("STAFF_PASSWORD", "")
    llm_backend: str = field(default_factory=lambda: os.getenv("LLM_BACKEND", "rules"))
    openai_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""), repr=False)
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", ""))
    openai_backup_model: str = field(default_factory=lambda: os.getenv("OPENAI_BACKUP_MODEL", ""))
    llm_call_budget_seconds: float = field(default_factory=lambda: float(os.getenv("LLM_CALL_BUDGET_SECONDS", "20")))
    identity_history_enabled: bool | None = field(default_factory=lambda: (
        os.getenv("IDENTITY_HISTORY_ENABLED", "").lower() == "true"
        if os.getenv("IDENTITY_HISTORY_ENABLED", "") else None
    ))
    agent_max_rounds: int = 8
    agent_max_tool_calls: int = 12
    agent_turn_budget_seconds: float = 60
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_secret: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    telegram_bot_username: str = os.getenv("TELEGRAM_BOT_USERNAME", "sea_bank_bot")
    results_dir: Path = Path(os.getenv("HARNESS_RESULTS_DIR", "eval/results"))
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    revision: str = os.getenv("DEPLOY_REVISION", "local")

    release_gate: str = os.getenv("HARNESS_RELEASE_GATE", "")
    environment: str = os.getenv("APP_ENV", "development")

    @property
    def release_ready(self):
        return not self.release_gate or Path(self.release_gate).is_file()

    def __post_init__(self):
        if self.ai_specialist_enabled and self.llm_backend != "openai":
            raise ValueError("AI_SPECIALIST_ENABLED requires LLM_BACKEND=openai")

        import re

        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", self.telegram_bot_username):
            raise ValueError("Invalid TELEGRAM_BOT_USERNAME")
        if self.environment == "production":
            if len(self.admin_token) < 24 or len(self.staff_password) < 16:
                raise ValueError("Production ADMIN_TOKEN and STAFF_PASSWORD must be configured with strong values")
            if self.telegram_token and len(self.telegram_secret) < 32:
                raise ValueError("Configured Telegram requires a strong webhook secret")
        if self.llm_backend not in ("rules", "openai"):
            raise ValueError("Unsupported LLM_BACKEND")
        if self.identity_history_enabled is None:
            self.identity_history_enabled = self.llm_backend == "openai"
        if self.agent_max_rounds < 1 or self.agent_max_tool_calls < 1 or not 0 < self.agent_turn_budget_seconds <= 120:
            raise ValueError("Agent limits must be positive and the turn budget at most 120 seconds")
        if not math.isfinite(self.llm_call_budget_seconds) or not 0 < self.llm_call_budget_seconds <= 120:
            raise ValueError("LLM_CALL_BUDGET_SECONDS must be between 0 and 120")
        if self.llm_backend == "openai":
            for name, value in (
                ("OPENAI_API_KEY", self.openai_key),
                ("OPENAI_MODEL", self.openai_model),
                ("OPENAI_BACKUP_MODEL", self.openai_backup_model),
            ):
                if not value.strip():
                    raise ValueError(name + " is required for LLM_BACKEND=openai")
            if self.openai_model == self.openai_backup_model:
                raise ValueError("OPENAI_MODEL and OPENAI_BACKUP_MODEL must be different")
