"""Global settings, loaded once from environment variables (.env in development)."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv(override=False)


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # Public URL of this server, e.g. https://voice.yourdomain.com (no trailing slash).
    public_base_url: str = field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL", "").rstrip("/"))

    # Twilio
    twilio_account_sid: str = field(default_factory=lambda: os.getenv("TWILIO_ACCOUNT_SID", ""))
    twilio_auth_token: str = field(default_factory=lambda: os.getenv("TWILIO_AUTH_TOKEN", ""))
    validate_twilio_signature: bool = field(default_factory=lambda: _bool("VALIDATE_TWILIO_SIGNATURE", True))

    # AI providers
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5"))
    summary_model: str = field(default_factory=lambda: os.getenv("SUMMARY_MODEL", "claude-haiku-4-5"))
    deepgram_api_key: str = field(default_factory=lambda: os.getenv("DEEPGRAM_API_KEY", ""))
    deepgram_model: str = field(default_factory=lambda: os.getenv("DEEPGRAM_MODEL", "nova-3-general"))
    cartesia_api_key: str = field(default_factory=lambda: os.getenv("CARTESIA_API_KEY", ""))
    default_voice_id: str = field(
        default_factory=lambda: os.getenv("CARTESIA_VOICE_ID", "86e30c1d-714b-4074-a1f2-1cb6b552fb49")
    )

    # Cal.com
    calcom_base_url: str = field(default_factory=lambda: os.getenv("CALCOM_BASE_URL", "https://api.cal.com/v2"))
    calcom_slots_version: str = field(default_factory=lambda: os.getenv("CALCOM_SLOTS_API_VERSION", "2024-09-04"))
    calcom_bookings_version: str = field(
        default_factory=lambda: os.getenv("CALCOM_BOOKINGS_API_VERSION", "2026-02-25")
    )

    # Operations
    database_path: str = field(default_factory=lambda: os.getenv("DATABASE_PATH", "data/leads.db"))
    clients_dir: str = field(default_factory=lambda: os.getenv("CLIENTS_DIR", "clients"))
    max_concurrent_calls: int = field(default_factory=lambda: int(os.getenv("MAX_CONCURRENT_CALLS", "20")))
    max_call_minutes: float = field(default_factory=lambda: float(os.getenv("MAX_CALL_MINUTES", "12")))
    user_idle_seconds: float = field(default_factory=lambda: float(os.getenv("USER_IDLE_SECONDS", "8")))

    # Security
    # Used to sign websocket tokens and outgoing CRM webhooks. Use a long random string.
    app_secret: str = field(default_factory=lambda: os.getenv("APP_SECRET", ""))
    admin_username: str = field(default_factory=lambda: os.getenv("ADMIN_USERNAME", "admin"))
    admin_password: str = field(default_factory=lambda: os.getenv("ADMIN_PASSWORD", ""))

    def problems(self) -> list[str]:
        """Return a list of configuration problems (empty = ready for production)."""
        missing = []
        for name in (
            "public_base_url",
            "twilio_account_sid",
            "twilio_auth_token",
            "anthropic_api_key",
            "deepgram_api_key",
            "cartesia_api_key",
            "app_secret",
            "admin_password",
        ):
            if not getattr(self, name):
                missing.append(name.upper())
        out = [f"Missing env var {m}" for m in missing]
        if self.app_secret and len(self.app_secret) < 24:
            out.append("APP_SECRET should be at least 24 characters")
        return out


settings = Settings()
