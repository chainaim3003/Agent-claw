"""get_user_location -> Nominatim forward geocode.

Nominatim Usage Policy (binding — https://operations.osmfoundation.org/policies/nominatim/):
  * max 1 request/second   * identifying User-Agent
  * cache results          * ODbL attribution      * self-host for volume

This module enforces 1 req/s and caches results in-process."""
from __future__ import annotations
import threading
import time
import requests

from ..config import get_settings
from ..http_client import request
from ..logging_setup import get_logger
from .exceptions import TransientProviderError, PermanentProviderError

log = get_logger("provider.geocode")

_LOCK = threading.Lock()
_LAST_CALL = 0.0
_CACHE: dict[str, dict] = {}


def get_user_location(address: str) -> dict:
    """Geocode an address string. Returns {lat, lng, label, source}.

    Raises PermanentProviderError if no result, TransientProviderError on 5xx/timeout.
    """
    if not address or not address.strip():
        raise PermanentProviderError("address must be a non-empty string")
    key = address.strip().lower()
    if key in _CACHE:
        log.info("geocode cache-hit %r", address)
        return _CACHE[key]

    s = get_settings()
    # honor 1 req/s
    global _LAST_CALL
    with _LOCK:
        wait = 1.0 - (time.time() - _LAST_CALL)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = request("GET", s.nominatim_url,
                           params={"q": address, "format": "json", "limit": 1},
                           headers={"User-Agent": s.nominatim_ua})
        except requests.RequestException as e:
            raise TransientProviderError(f"nominatim network error: {e}") from e
        finally:
            _LAST_CALL = time.time()

    if resp.status_code == 429:
        raise TransientProviderError("nominatim rate-limited (429)")
    if 500 <= resp.status_code < 600:
        raise TransientProviderError(f"nominatim {resp.status_code}")
    if not resp.ok:
        raise PermanentProviderError(f"nominatim {resp.status_code}: {resp.text[:200]}")

    rows = resp.json()
    if not rows:
        raise PermanentProviderError(f"no geocoding result for {address!r}")
    r = rows[0]
    out = {
        "lat": float(r["lat"]),
        "lng": float(r["lon"]),
        "label": r.get("display_name", address),
        "source": "nominatim",
    }
    _CACHE[key] = out
    log.info("geocoded %r -> (%s, %s)", address, out["lat"], out["lng"])
    return out
