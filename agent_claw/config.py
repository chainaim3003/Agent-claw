"""Centralised configuration. All settings come from environment variables;
.env is auto-loaded if python-dotenv is installed. Validation happens on
demand so unrelated commands don't fail just because, say, Cal.com env is
missing."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "outputs"
DB_PATH = DATA_DIR / "agent_claw.db"


def _load_dotenv() -> None:
    """Load .env into os.environ if python-dotenv is installed.
    Idempotent; safe to call repeatedly."""
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(ROOT / ".env", override=False)
    except ImportError:
        pass  # env can also be set in the shell


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


# IMPORTANT: env-backed fields use `default_factory` so the lookup happens at
# `Settings()` *instantiation* time — NOT at class-definition time. This is
# what lets `_load_dotenv()` in `get_settings()` actually take effect.
@dataclass(frozen=True)
class Settings:
    # --- Nominatim (OSM geocoding) ---
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    nominatim_ua: str = field(default_factory=lambda: _env(
        "NOMINATIM_UA", "AgentClaw/1.0 (set NOMINATIM_UA in .env)"))

    # --- Overpass (OSM POI search) ---
    overpass_url: str = field(default_factory=lambda: _env(
        "OVERPASS_URL", "https://overpass-api.de/api/interpreter"))

    # --- Cal.com (booking) ---
    calcom_base_url: str = field(default_factory=lambda: _env(
        "CALCOM_BASE_URL", "https://api.cal.com"))
    calcom_api_key: str = field(default_factory=lambda: _env("CALCOM_API_KEY"))
    calcom_event_type_id: str = field(default_factory=lambda: _env("CALCOM_EVENT_TYPE_ID"))
    calcom_api_version_slots: str = field(default_factory=lambda: _env(
        "CALCOM_API_VERSION_SLOTS", "2024-09-04"))
    calcom_api_version_bookings: str = field(default_factory=lambda: _env(
        "CALCOM_API_VERSION_BOOKINGS", "2024-08-13"))
    attendee_name: str = field(default_factory=lambda: _env("ATTENDEE_NAME", "Agent Guest"))
    attendee_email: str = field(default_factory=lambda: _env("ATTENDEE_EMAIL", "guest@example.com"))
    timezone: str = field(default_factory=lambda: _env("TZ", "Asia/Kolkata"))

    # --- Twilio (SMS) ---
    twilio_sid: str = field(default_factory=lambda: _env("TWILIO_ACCOUNT_SID"))
    twilio_token: str = field(default_factory=lambda: _env("TWILIO_AUTH_TOKEN"))
    twilio_from: str = field(default_factory=lambda: _env("TWILIO_FROM_NUMBER"))
    twilio_status_callback: str = field(default_factory=lambda: _env("TWILIO_STATUS_CALLBACK"))

    # --- LLM planners ---
    ollama_base_url: str = field(default_factory=lambda: _env(
        "OLLAMA_BASE_URL", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "qwen2.5"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(default_factory=lambda: _env("CLAUDE_MODEL", "claude-opus-4-7"))

    # --- Google Calendar (OAuth installed-app flow) ---
    # Path to client_secret_*.json downloaded from Google Cloud Console.
    # Relative paths resolve against ROOT (Restaurant-claw/).
    google_client_secrets_path: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_SECRETS_PATH"))
    # Where to cache the refresh token after first consent.
    google_token_path: str = field(default_factory=lambda: _env("GOOGLE_TOKEN_PATH"))
    # Target calendar; 'primary' is the user's main calendar.
    google_calendar_id: str = field(default_factory=lambda: _env("GOOGLE_CALENDAR_ID", "primary"))
    # Comma-separated popup reminder offsets in minutes before event start.
    google_reminder_minutes: str = field(default_factory=lambda: _env("GOOGLE_REMINDER_MINUTES", "60,1440"))
    # How long the calendar block should be (Cal.com only knows the start).
    google_event_duration_min: int = field(default_factory=lambda: int(_env("GOOGLE_EVENT_DURATION_MIN", "90")))

    # --- HTTP client ---
    http_timeout_s: float = field(default_factory=lambda: float(_env("HTTP_TIMEOUT_S", "30")))
    http_retries: int = field(default_factory=lambda: int(_env("HTTP_RETRIES", "3")))


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _load_dotenv()
        _settings = Settings()
        DATA_DIR.mkdir(exist_ok=True)
        OUTPUT_DIR.mkdir(exist_ok=True)
    return _settings


# --- Validation helpers (called by the providers that need them) -----------
def require(name: str, value: str) -> str:
    if not value:
        raise RuntimeError(
            f"Missing required environment variable {name}. "
            f"See .env.example and set it in your .env file.")
    return value
