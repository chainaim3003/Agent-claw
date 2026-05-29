"""create_calendar_event -> Google Calendar API v3.

Auth: OAuth 2.0 installed-app flow. First-run consent (`python -m agent_claw
--gcal-setup`) opens a browser; the resulting refresh_token is cached at
GOOGLE_TOKEN_PATH and reused silently on every subsequent run.

Scope: 'https://www.googleapis.com/auth/calendar.events' — read/write events on
calendars the user can access; does NOT permit listing all calendars.

Behaviour when Google is not configured:
  - GOOGLE_CLIENT_SECRETS_PATH unset .............. returns {"skipped": True}
  - Token file missing / consent not yet completed  returns {"skipped": True}
This keeps the booking pipeline working when calendar is intentionally off.

References:
  https://developers.google.com/calendar/api/v3/reference/events/insert
  https://developers.google.com/identity/protocols/oauth2/native-app

Service-account swap (Google Workspace with domain-wide delegation):
  Replace `_load_credentials()` with
      from google.oauth2 import service_account
      creds = service_account.Credentials.from_service_account_file(
          SVC_KEY_PATH, scopes=SCOPES, subject="user@yourdomain.com")
  No browser flow; everything else here stays the same.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from ..config import get_settings, DATA_DIR
from ..http_client import request
from ..logging_setup import get_logger
from .exceptions import TransientProviderError, PermanentProviderError

log = get_logger("provider.calendar")

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


# --- helpers -----------------------------------------------------------------
def _token_path() -> Path:
    s = get_settings()
    p = s.google_token_path or str(DATA_DIR / "google_token.json")
    return Path(p)


def _parse_reminder_minutes(raw: str) -> list[int]:
    """Parse e.g. '60,1440' into [60, 1440]. Silently drops bad entries."""
    out: list[int] = []
    for token in (raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.append(int(token))
        except ValueError:
            log.warning("ignoring non-integer reminder value %r", token)
    return out or [60]  # never return empty; at least one 60-min reminder


def _to_utc_iso(slot: str) -> str:
    """Normalise an ISO start (with offset or 'Z') to UTC ISO with 'Z'."""
    if slot.endswith("Z"):
        return slot
    try:
        dt = datetime.fromisoformat(slot)
    except ValueError:
        return slot  # let Google reject if truly malformed
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# --- OAuth credential management ---------------------------------------------
def _load_credentials():
    """Return a refreshable google.oauth2.credentials.Credentials, or None if
    auth has not been set up yet. Refresh happens transparently here."""
    try:
        from google.oauth2.credentials import Credentials  # type: ignore
        from google.auth.transport.requests import Request as GoogleAuthRequest  # type: ignore
    except ImportError as e:
        raise PermanentProviderError(
            f"Google auth libraries not installed ({e}). "
            "Run: pip install google-auth google-auth-oauthlib"
        ) from e

    token_file = _token_path()
    if not token_file.exists():
        return None

    try:
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    except Exception as e:  # noqa: BLE001 - corrupted/old token file
        log.warning("token file %s unreadable (%s); re-run --gcal-setup",
                    token_file, e)
        return None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleAuthRequest())
            token_file.write_text(creds.to_json())
            log.info("refreshed Google OAuth access token")
        except Exception as e:  # noqa: BLE001
            raise TransientProviderError(
                f"failed to refresh Google token: {e}") from e

    if not creds or not creds.valid:
        return None
    return creds


def setup_oauth() -> Path:
    """Run the installed-app OAuth flow: open browser, capture consent,
    persist refresh token to disk. Idempotent — re-running re-consents.

    Returns the path of the saved token file.
    """
    s = get_settings()
    secrets_path = s.google_client_secrets_path
    if not secrets_path:
        raise PermanentProviderError(
            "GOOGLE_CLIENT_SECRETS_PATH is not set. Download "
            "client_secret_*.json from Google Cloud Console (OAuth client \u2192 "
            "Desktop app) and point GOOGLE_CLIENT_SECRETS_PATH at it.")
    secrets_file = Path(secrets_path)
    if not secrets_file.is_absolute():
        # Resolve relative to the project root (same convention as DATA_DIR).
        from ..config import ROOT
        secrets_file = (ROOT / secrets_path).resolve()
    if not secrets_file.exists():
        raise PermanentProviderError(
            f"Google client secrets file not found at {secrets_file}")

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
    except ImportError as e:
        raise PermanentProviderError(
            f"google-auth-oauthlib not installed ({e}). "
            "Run: pip install google-auth-oauthlib") from e

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_file), SCOPES)
    creds = flow.run_local_server(port=0)  # picks a free port, opens browser
    token_file = _token_path()
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json())
    log.info("saved Google OAuth token to %s", token_file)
    return token_file


# --- event body --------------------------------------------------------------
def _build_event_body(record: dict, start_iso_utc: str, duration_min: int,
                      reminder_minutes: list[int]) -> dict:
    """Compose the JSON body for events.insert."""
    end_dt = (datetime.fromisoformat(start_iso_utc.replace("Z", "+00:00"))
              + timedelta(minutes=duration_min))
    end_iso_utc = end_dt.isoformat().replace("+00:00", "Z")

    summary = f"Dinner at {record.get('restaurant_name', 'restaurant')}"
    description_lines = [
        f"Confirmation: {record.get('confirmation_id', '?')}",
        f"Restaurant:   {record.get('restaurant_name', '?')}"
        f" ({record.get('restaurant_id', '?')})",
        f"Date / slot:  {record.get('date', '?')} @ {record.get('slot', '?')}",
        f"Party size:   {record.get('party', '?')}",
        f"Contact:      {record.get('contact', '?')}",
        f"Provider:     {record.get('provider', 'calcom')}",
    ]
    if record.get("rating"):
        description_lines.append(f"Rating:       ★ {record['rating']}")
    if record.get("eazydiner_url"):
        description_lines.append(f"EazyDiner:    {record['eazydiner_url']}")
    description_lines += ["", "Created by Agent-claw."]
    return {
        "summary": summary,
        "description": "\n".join(description_lines),
        "location": record.get("restaurant_name", ""),
        "start": {"dateTime": start_iso_utc, "timeZone": "UTC"},
        "end": {"dateTime": end_iso_utc, "timeZone": "UTC"},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": m} for m in reminder_minutes
            ],
        },
    }


# --- the public tool surface -------------------------------------------------
def create_calendar_event(record: dict, **_: object) -> dict:
    """Insert a Google Calendar event for a confirmed booking.

    Expects a record from `book_reservation()` with at least:
      confirmation_id, restaurant_name, party, contact, slot (ISO start), date.

    Returns one of:
      {"event_id", "html_link", "status", "channel": "google_calendar"}
      {"skipped": True, "reason": "<why>"}     (graceful no-op)

    Raises TransientProviderError / PermanentProviderError only for problems
    *after* credentials are confirmed present and valid.
    """
    s = get_settings()
    if not s.google_client_secrets_path:
        log.info("calendar skipped: GOOGLE_CLIENT_SECRETS_PATH not set")
        return {"skipped": True, "reason": "google not configured"}

    creds = _load_credentials()
    if creds is None:
        log.warning("calendar skipped: no valid token; "
                    "run `python -m agent_claw --gcal-setup`")
        return {"skipped": True, "reason": "oauth not completed"}

    slot = record.get("slot")
    if not slot:
        raise PermanentProviderError("record is missing 'slot' for calendar event")

    start_iso_utc = _to_utc_iso(slot)
    reminder_minutes = _parse_reminder_minutes(s.google_reminder_minutes)
    body = _build_event_body(record, start_iso_utc,
                             s.google_event_duration_min, reminder_minutes)
    calendar_id = s.google_calendar_id or "primary"
    url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json",
    }
    try:
        resp = request("POST", url, headers=headers, json=body)
    except requests.RequestException as e:
        raise TransientProviderError(f"google calendar network error: {e}") from e

    if resp.status_code == 429:
        raise TransientProviderError("google calendar rate-limited (429)")
    if 500 <= resp.status_code < 600:
        raise TransientProviderError(f"google calendar {resp.status_code}")
    if resp.status_code == 401:
        # Token may have been revoked; tell user how to fix.
        raise PermanentProviderError(
            "google calendar 401: token rejected. Re-run `--gcal-setup`.")
    if not resp.ok:
        raise PermanentProviderError(
            f"google calendar {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    log.info("created google calendar event %s (%s)",
             data.get("id"), data.get("htmlLink"))
    return {
        "event_id": data.get("id", ""),
        "html_link": data.get("htmlLink", ""),
        "status": data.get("status", ""),
        "channel": "google_calendar",
    }
