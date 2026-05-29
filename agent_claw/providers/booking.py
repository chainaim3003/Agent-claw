"""check_availability + book_reservation -> Cal.com v2 (cloud or self-hosted).

Verified against the official docs on 2026-05-28:
  GET  /v2/slots     cal-api-version: 2024-09-04
       params: eventTypeId, start (YYYY-MM-DD), end (YYYY-MM-DD), timeZone
       response: {"data": {"YYYY-MM-DD": [{"start": "...ISO..."}, ...]}}
  POST /v2/bookings  cal-api-version: 2024-08-13
       body: {"start": "...UTC ISO...", "eventTypeId": int,
              "attendee": {"name", "email", "timeZone", "phoneNumber"?, "language"?}}
       NB: if your event type has required booking fields (e.g. {title}), pass
           them in `bookingFieldsResponses` to avoid 400 error_required_field.

Sources:
  https://cal.com/docs/api-reference/v2/slots/get-available-time-slots-for-an-event-type
  https://cal.com/docs/api-reference/v2/bookings/create-a-booking
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import requests

from ..config import get_settings, require
from ..http_client import request
from ..logging_setup import get_logger
from .exceptions import TransientProviderError, PermanentProviderError

log = get_logger("provider.booking")


class CalcomClient:
    def __init__(self) -> None:
        s = get_settings()
        self.base = require("CALCOM_BASE_URL", s.calcom_base_url).rstrip("/")
        self.key = require("CALCOM_API_KEY", s.calcom_api_key)
        self.event_type_id = int(require("CALCOM_EVENT_TYPE_ID", s.calcom_event_type_id))
        self.timezone = s.timezone
        self.version_slots = s.calcom_api_version_slots
        self.version_bookings = s.calcom_api_version_bookings
        self.attendee_name = s.attendee_name
        self.attendee_email = s.attendee_email

    # --- public surface used by the agent ----------------------------------
    def get_slots(self, date: str) -> list[str]:
        """Return ISO-8601 start strings available on the given YYYY-MM-DD."""
        # Cal.com's /v2/slots takes start and end as YYYY-MM-DD.
        next_day = (datetime.fromisoformat(date) + timedelta(days=1)).date().isoformat()
        url = f"{self.base}/v2/slots"
        headers = {
            "Authorization": f"Bearer {self.key}",
            "cal-api-version": self.version_slots,
        }
        params = {
            "eventTypeId": self.event_type_id,
            "start": date,
            "end": next_day,
            "timeZone": self.timezone,
        }
        try:
            resp = request("GET", url, headers=headers, params=params)
        except requests.RequestException as e:
            raise TransientProviderError(f"cal.com slots network error: {e}") from e

        if resp.status_code == 429:
            raise TransientProviderError("cal.com slots rate-limited (429)")
        if 500 <= resp.status_code < 600:
            raise TransientProviderError(f"cal.com slots {resp.status_code}")
        if not resp.ok:
            raise PermanentProviderError(
                f"cal.com slots {resp.status_code}: {resp.text[:300]}")

        payload = resp.json()
        data = payload.get("data") or {}
        # data is a map of date -> [{start: iso}, ...]; flatten across dates.
        starts: list[str] = []
        if isinstance(data, dict):
            for _date, items in data.items():
                for it in items:
                    if isinstance(it, dict) and "start" in it:
                        starts.append(it["start"])
        log.info("cal.com returned %d slot(s) for %s", len(starts), date)
        return starts

    def create_booking(self, start_iso_utc: str,
                       extra_responses: dict | None = None) -> dict:
        """Create a real booking. Returns the parsed Cal.com response.

        start_iso_utc must be in UTC ('...Z' or '+00:00').
        """
        url = f"{self.base}/v2/bookings"
        headers = {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "cal-api-version": self.version_bookings,
        }
        body: dict = {
            "start": start_iso_utc,
            "eventTypeId": self.event_type_id,
            "attendee": {
                "name": self.attendee_name,
                "email": self.attendee_email,
                "timeZone": self.timezone,
                "language": "en",
            },
        }
        if extra_responses:
            body["bookingFieldsResponses"] = extra_responses

        try:
            resp = request("POST", url, headers=headers, json=body)
        except requests.RequestException as e:
            raise TransientProviderError(f"cal.com booking network error: {e}") from e

        if resp.status_code == 429:
            raise TransientProviderError("cal.com booking rate-limited (429)")
        if 500 <= resp.status_code < 600:
            raise TransientProviderError(f"cal.com booking {resp.status_code}")
        if not resp.ok:
            # 400 here is permanent — surface Cal.com's reason verbatim.
            raise PermanentProviderError(
                f"cal.com booking {resp.status_code}: {resp.text[:400]}")
        return resp.json()


# --- functions used by the agent's tool dispatch ------------------------------
def _load_tz(name: str):
    """Best-effort ZoneInfo loader. Returns None if the platform lacks tzdata
    (common on Windows without the `tzdata` pip package). Caller treats None as
    'compare in whatever offset the ISO string already carries'."""
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # type: ignore
    except ImportError:
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        return None


def check_availability(restaurant: dict, date: str, time_window: str, party: int,
                       **_: object) -> list[str]:
    """Return Cal.com slot ISO starts for `date`, sorted by proximity to the
    local-time `time_window` (HH:MM). All slots are returned — the agent picks
    `slots[0]`, so closest-first is the desired order.

    Robust to both 'Z'-suffixed UTC and offset-bearing ISO strings. If a slot
    fails to parse it's appended at the end rather than dropped (a parsed slot
    is always preferred over an unparseable one).
    """
    client = CalcomClient()
    slots = client.get_slots(date)
    if not slots:
        return []

    try:
        target_h, target_m = (int(p) for p in time_window.split(":"))
        target_minutes = target_h * 60 + target_m
    except (ValueError, AttributeError):
        # Caller gave a malformed time_window — return slots in Cal.com's order.
        log.warning("time_window %r unparseable; returning unsorted slots", time_window)
        return slots

    tz = _load_tz(client.timezone)
    scored: list[tuple[int, str]] = []
    unparsed: list[str] = []
    for s in slots:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if tz is not None and dt.tzinfo is not None:
                dt = dt.astimezone(tz)
            slot_minutes = dt.hour * 60 + dt.minute
            scored.append((abs(slot_minutes - target_minutes), s))
        except (ValueError, TypeError):
            unparsed.append(s)

    scored.sort(key=lambda x: x[0])
    sorted_slots = [s for _, s in scored] + unparsed
    log.info("check_availability: %d slot(s), closest-to-%s first",
             len(sorted_slots), time_window)
    return sorted_slots


def book_reservation(restaurant: dict, date: str, slot: str, party: int,
                     contact: str) -> dict:
    """Create a real booking for the chosen slot. Returns a normalized record."""
    client = CalcomClient()
    # `slot` is an ISO start coming from get_slots. Normalize to UTC ISO.
    start_iso_utc = _to_utc_iso(slot)
    raw = client.create_booking(start_iso_utc)
    data = (raw or {}).get("data") or raw or {}
    confirmation = data.get("uid") or data.get("id") or "CALCOM-UNKNOWN"
    return {
        "confirmation_id": str(confirmation),
        "status": "confirmed",
        "restaurant_id": restaurant.get("id", "unknown"),
        "restaurant_name": restaurant.get("name", "unknown"),
        "date": date,
        "slot": slot,
        "party": party,
        "contact": contact,
        "provider": "calcom",
    }


def _to_utc_iso(s: str) -> str:
    """Parse an ISO string (with offset or 'Z') and emit UTC ISO with 'Z'."""
    if s.endswith("Z"):
        return s
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Last-resort: assume already UTC.
        return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
