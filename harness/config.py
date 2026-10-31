import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    db_path: str = os.getenv("HARNESS_DB", "data/harness.sqlite3")
    admin_token: str = os.getenv("ADMIN_TOKEN", "")
    staff_password: str = os.getenv("STAFF_PASSWORD", "")
    llm_backend: str = os.getenv("LLM_BACKEND", "rules")
    openai_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "")
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_secret: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    results_dir: Path = Path(os.getenv("HARNESS_RESULTS_DIR", "eval/results"))
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    revision: str = os.getenv("DEPLOY_REVISION", "local")

    release_gate: str = os.getenv("HARNESS_RELEASE_GATE", "")
    environment: str = os.getenv("APP_ENV", "development")

    @property
    def release_ready(self):
        return not self.release_gate or Path(self.release_gate).is_file()

    def __post_init__(self):
        if self.environment == "production":
            if len(self.admin_token) < 24 or len(self.staff_password) < 16:
                raise ValueError("Production ADMIN_TOKEN and STAFF_PASSWORD must be configured with strong values")
            if self.telegram_token and len(self.telegram_secret) < 32:
                raise ValueError("Configured Telegram requires a strong webhook secret")
        if self.llm_backend not in ("rules", "openai"):
            raise ValueError("Unsupported LLM_BACKEND")
        if self.llm_backend == "openai" and not (self.openai_key and self.openai_model):
            self.llm_backend = "rules"
