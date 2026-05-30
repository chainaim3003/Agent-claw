"""send_email -> Gmail SMTP (real outbound email, no mocks).

Uses Python's stdlib smtplib over an implicit-TLS connection to
smtp.gmail.com:465. Authentication is a Gmail account + a 16-char
**App Password** (NOT the normal account password). App Passwords require
2-Step Verification to be enabled on the sending Google account:

    Google Account -> Security -> 2-Step Verification (turn ON)
    -> App passwords -> generate one for "Mail" -> copy the 16 chars.

Config is read from the environment (same pattern as restaurant_eazydiner.py),
so this provider needs no change to config.py:

    SMTP_USER          the sending Gmail address (e.g. you@gmail.com)
    SMTP_APP_PASSWORD  the 16-char Gmail App Password (spaces are stripped)
    SMTP_HOST          optional, default smtp.gmail.com
    SMTP_PORT          optional, default 465 (implicit TLS)
    SMTP_FROM_NAME     optional display name, default "Agent-Claw Reservations"

Returns the SAME-shaped dict family as providers.sms.send_sms so the
orchestrator treats SMS and email uniformly:

    {"to": ..., "status": "sent", "channel": "smtp", "subject": ...}

Errors follow the project's provider-error convention:
    PermanentProviderError  bad creds / missing config / bad recipient (no retry)
    TransientProviderError  network/timeout/temporary SMTP 4xx (caller may retry)
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from ..logging_setup import get_logger
from ..config import get_settings
from .exceptions import TransientProviderError, PermanentProviderError

log = get_logger("provider.email")

DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 465
DEFAULT_FROM_NAME = "Agent-Claw Reservations"


def _require_env(name: str) -> str:
    val = (os.environ.get(name) or "").strip()
    if not val:
        raise PermanentProviderError(
            f"{name} is not set in .env. Email cannot be sent without it."
        )
    return val


def send_email(to: str, subject: str, body: str, **_: object) -> dict:
    """Send a plain-text email via Gmail SMTP.

    Args:
        to:      recipient address (a single address; required)
        subject: subject line
        body:    plain-text body

    Returns a status dict. Raises Permanent/TransientProviderError on failure
    so the orchestrator can log it as non-fatal exactly like send_sms.
    """
    if not to or "@" not in to:
        raise PermanentProviderError(f"invalid recipient email: {to!r}")

    # Ensure .env is loaded into os.environ. config._load_dotenv() runs inside
    # get_settings() (not at import time), and this provider reads os.environ
    # directly, so we must trigger the load here exactly like sms.py does.
    get_settings()

    user = _require_env("SMTP_USER")
    # App Passwords are shown with spaces ("abcd efgh ijkl mnop"); strip them.
    password = _require_env("SMTP_APP_PASSWORD").replace(" ", "")
    host = (os.environ.get("SMTP_HOST") or DEFAULT_HOST).strip()
    port = int(os.environ.get("SMTP_PORT") or DEFAULT_PORT)
    from_name = (os.environ.get("SMTP_FROM_NAME") or DEFAULT_FROM_NAME).strip()

    msg = EmailMessage()
    msg["From"] = formataddr((from_name, user))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    log.info("sending email to=%s subject=%r via %s:%d", to, subject, host, port)
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
            server.login(user, password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        # Wrong account/app-password, or 2FA/App-Password not set up. Not retryable.
        raise PermanentProviderError(
            f"smtp auth failed for {user}: {e}. "
            "Confirm SMTP_USER and that SMTP_APP_PASSWORD is a Gmail App Password "
            "(2-Step Verification must be enabled)."
        ) from e
    except smtplib.SMTPRecipientsRefused as e:
        raise PermanentProviderError(f"recipient refused: {to} ({e})") from e
    except (smtplib.SMTPException, OSError, TimeoutError) as e:
        # Connection drop, DNS failure, temporary SMTP error -> let caller retry.
        raise TransientProviderError(f"smtp send failed: {e}") from e

    log.info("email sent to=%s", to)
    return {"to": to, "status": "sent", "channel": "smtp", "subject": subject}
