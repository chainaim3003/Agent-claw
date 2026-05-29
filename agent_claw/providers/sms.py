"""send_sms -> Twilio Programmable Messaging REST API.

POST https://api.twilio.com/2010-04-01/Accounts/{Sid}/Messages.json
Basic Auth: Sid:Token  |  Form: From, To, Body, StatusCallback?

Trial-account caveats (Twilio docs):
  * outbound to verified numbers only
  * trial notice prepended to body
  * US local numbers need A2P 10DLC registration
"""
from __future__ import annotations
import requests

from ..config import get_settings, require
from ..http_client import request
from ..logging_setup import get_logger
from .exceptions import TransientProviderError, PermanentProviderError

log = get_logger("provider.sms")


def send_sms(to: str, body: str, **_: object) -> dict:
    s = get_settings()
    sid = require("TWILIO_ACCOUNT_SID", s.twilio_sid)
    tok = require("TWILIO_AUTH_TOKEN", s.twilio_token)
    frm = require("TWILIO_FROM_NUMBER", s.twilio_from)
    if not to:
        raise PermanentProviderError("recipient phone number (to) is required")

    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    form: dict = {"From": frm, "To": to, "Body": body}
    if s.twilio_status_callback:
        form["StatusCallback"] = s.twilio_status_callback

    try:
        resp = request("POST", url, data=form, auth=(sid, tok))
    except requests.RequestException as e:
        raise TransientProviderError(f"twilio network error: {e}") from e

    if resp.status_code == 429:
        raise TransientProviderError("twilio rate-limited (429)")
    if 500 <= resp.status_code < 600:
        raise TransientProviderError(f"twilio {resp.status_code}")
    if not resp.ok:
        # 401 = bad creds, 400 = bad number / unverified recipient on trial, etc.
        raise PermanentProviderError(f"twilio {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    log.info("twilio queued sid=%s status=%s", data.get("sid"), data.get("status"))
    return {
        "sid": data.get("sid", ""),
        "status": data.get("status", ""),
        "channel": "twilio",
        "error_code": data.get("error_code"),
    }
