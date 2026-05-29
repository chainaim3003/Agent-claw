"""search_restaurants -> dispatcher over multiple restaurant data sources.

Sources:
  - 'osm'       (default) — Overpass query for amenity=restaurant near (lat,lng).
                Returns lat/lng and haversine distance_km.
  - 'eazydiner' — Playwright scrape of EazyDiner's city listing page.
                Returns rating, locality, price_for_two, eazydiner_url.
                No lat/lng; distance_km is 0.0.

Source is selected by the env var RESTAURANT_SOURCE (default 'osm').
"""
from __future__ import annotations
import math
import os
import requests

from ..config import get_settings
from ..http_client import request
from ..logging_setup import get_logger
from .exceptions import TransientProviderError, PermanentProviderError

log = get_logger("provider.search")


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _search_restaurants_osm(lat: float, lng: float,
                            cuisine: str | None = None,
                            limit: int = 5,
                            radius_m: int = 3000) -> list[dict]:
    """OSM/Overpass implementation (original, unchanged behavior)."""
    s = get_settings()
    query = (
        f"[out:json][timeout:25];"
        f'node["amenity"="restaurant"](around:{radius_m},{lat},{lng});'
        f"out body 80;"
    )
    try:
        resp = request("POST", s.overpass_url,
                       data={"data": query},
                       headers={"User-Agent": s.nominatim_ua})
    except requests.RequestException as e:
        raise TransientProviderError(f"overpass network error: {e}") from e

    if resp.status_code == 429:
        raise TransientProviderError("overpass rate-limited (429)")
    if 500 <= resp.status_code < 600:
        raise TransientProviderError(f"overpass {resp.status_code}")
    if not resp.ok:
        raise PermanentProviderError(f"overpass {resp.status_code}: {resp.text[:200]}")

    elements = resp.json().get("elements", [])
    results: list[dict] = []
    for e in elements:
        tags = e.get("tags", {})
        name = tags.get("name")
        if not name:
            continue  # unnamed restaurants aren't bookable end-user candidates
        if cuisine:
            if cuisine.lower() not in tags.get("cuisine", "").lower():
                continue
        results.append({
            "id": f"osm{e['id']}",
            "name": name,
            "cuisine": tags.get("cuisine", "unknown"),
            "lat": e["lat"],
            "lng": e["lon"],
            "distance_km": round(_haversine_km(lat, lng, e["lat"], e["lon"]), 2),
            "source": "osm",
        })

    results.sort(key=lambda r: r["distance_km"])
    log.info("overpass found %d restaurant(s); returning top %d", len(results), limit)
    return results[:limit]


def search_restaurants(lat: float, lng: float,
                       cuisine: str | None = None,
                       limit: int = 5,
                       radius_m: int = 3000,
                       address: str | None = None) -> list[dict]:
    """Dispatch over the configured restaurant source.

    All callers pass lat/lng (the geocoded address centroid) for OSM. The new
    optional `address` parameter is needed only by EazyDiner, which extracts a
    city slug from it. Without an address, EazyDiner cannot work and we fall
    back to OSM with a warning so the booking still succeeds.
    """
    source = os.environ.get("RESTAURANT_SOURCE", "osm").lower().strip()

    if source == "eazydiner":
        if not address:
            log.warning("RESTAURANT_SOURCE=eazydiner but no address passed; "
                        "falling back to OSM")
        else:
            try:
                # Lazy import: scraper deps are optional.
                from .restaurant_eazydiner import search_restaurants_eazydiner
                return search_restaurants_eazydiner(address, cuisine=cuisine,
                                                    limit=limit)
            except PermanentProviderError as e:
                log.warning("eazydiner failed permanently (%s); falling back to OSM", e)
            except TransientProviderError as e:
                log.warning("eazydiner failed transiently (%s); falling back to OSM", e)
    elif source not in ("osm", ""):
        log.warning("unknown RESTAURANT_SOURCE=%r; using OSM", source)

    return _search_restaurants_osm(lat, lng, cuisine=cuisine,
                                   limit=limit, radius_m=radius_m)
