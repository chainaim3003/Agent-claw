"""One requests.Session for the whole process, with retries on transient
failures (429 + 5xx), connect/read timeouts, and request/response logging.

The retry policy is conservative on purpose: idempotent GETs retry freely;
POSTs (booking, SMS) retry only on 429 + 5xx, never on 4xx, because Cal.com
and Twilio respond 4xx for permanent client errors that should not be hidden."""
from __future__ import annotations
from typing import Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import get_settings
from .logging_setup import get_logger

log = get_logger("http")
_session: requests.Session | None = None


def _build_session() -> requests.Session:
    s = get_settings()
    sess = requests.Session()
    retry = Retry(
        total=s.http_retries,
        connect=s.http_retries,
        read=s.http_retries,
        status=s.http_retries,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = _build_session()
    return _session


def request(method: str, url: str, **kwargs: Any) -> requests.Response:
    s = get_settings()
    kwargs.setdefault("timeout", s.http_timeout_s)
    log.debug("%s %s", method, url)
    resp = session().request(method, url, **kwargs)
    log.debug("-> %s %s (%d bytes)", resp.status_code, resp.reason, len(resp.content))
    return resp
