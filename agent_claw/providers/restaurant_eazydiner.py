"""search_restaurants_eazydiner -> Playwright scraper for EazyDiner listings.

Returns restaurants in the SAME dict shape as providers.search.search_restaurants
so the agent's orchestrator and tool layer don't need to care which source
was used. The only extra field is `eazydiner_url` (string), which the SMS and
calendar event include when present.

Design notes:
- Read-only. This file never POSTs anywhere; it only loads a public listing
  page in a real browser, exactly as a visitor would, and reads visible text.
- Headless=True by default (no popup window during agent runs). Set the env
  var EAZYDINER_HEADLESS=false to see the browser for debugging.
- Per-city cache in memory + on disk (data/eazydiner_cache_<city>.json), TTL
  defined by EAZYDINER_CACHE_MIN env var (default 60 min). Avoids hammering
  the site on repeated agent runs.
- Selectors are pinned to the classes confirmed by test_scraper_card.py:
    listing_res_name__uVIN8   (restaurant-name span, 9 per page)
  The per-card text layout, in order observed:
    line 0: '%'                          (icon glyph; skip)
    line 1: deal text                    e.g. "25% Off :Payeazy"
    line 2 (optional): feature flag      e.g. "Best Buffet"  (only some cards)
    line N-4: NAME
    line N-3: LOCALITY                   e.g. "Siddhapudur, Coimbatore"
    line N-2: CUISINE                    e.g. "Multicuisine"
    line N-1: PRICE                      e.g. "₹1000 for two"
    line N  : RATING                     e.g. "4.9"
  The function walks bottom-up so feature flags / extra deal lines don't
  corrupt the field mapping.

Caveats:
- HTML class names are auto-generated and CHANGE WHEN EAZYDINER REDEPLOYS.
  When the scraper stops returning results, re-run test_scraper_card.py to
  refresh the selectors. Expected to break once every few weeks to months.
- The site shows ~9 restaurants per listing page. We don't paginate by
  default; the first page is the "featured" set which is what users want
  to see anyway. Set EAZYDINER_PAGES env var to scrape more pages.
- No lat/lng in the cards. distance_km is set to 0.0 as a sentinel. If you
  need real distance, geocode the locality through Nominatim afterwards.
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import time
from pathlib import Path

from ..config import get_settings, DATA_DIR
from ..logging_setup import get_logger
from .exceptions import TransientProviderError, PermanentProviderError

log = get_logger("provider.eazydiner")

BASE_URL = "https://www.eazydiner.com"
NAME_SELECTOR = '[class*="listing_res_name"]'  # tolerant of class-suffix churn
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_RATING_RE = re.compile(r"^\d(?:\.\d)?$")               # "4.9", "4", "3.5"
_PRICE_RE = re.compile(r"₹\s*\d[\d,]*\s*for\s*two", re.IGNORECASE)
_DEAL_RE = re.compile(r"(\d+\s*%|flat|extra|off|eazypoints|bank)", re.IGNORECASE)
_FEATURE_RE = re.compile(r"^(best|recommended|trending|new|popular)\b", re.IGNORECASE)

_mem_cache: dict[str, tuple[float, list[dict]]] = {}


# --- city slug normalization -------------------------------------------------
def _city_slug(city: str) -> str:
    """Convert 'Coimbatore, Tamil Nadu' -> 'coimbatore'.

    EazyDiner URLs use a city-only slug. We strip state/country, lowercase,
    and replace whitespace with hyphens. Cities not on EazyDiner will 404 or
    redirect; the scraper surfaces that as PermanentProviderError.
    """
    head = city.split(",")[0].strip().lower()
    return re.sub(r"\s+", "-", head)


# --- per-card text parser ----------------------------------------------------
def _parse_card_text(lines: list[str], href: str) -> dict | None:
    """Map the observed line layout to fields. Returns None if the card
    doesn't look like a real restaurant (sponsored ads, malformed cards)."""
    clean = [ln.strip() for ln in lines if ln and ln.strip() and ln.strip() != "%"]
    if len(clean) < 4:
        return None  # not enough fields to be a real card

    # Identify rating (last line that's just a number 1-5 with optional decimal)
    rating: float | None = None
    while clean and _RATING_RE.match(clean[-1]):
        try:
            r = float(clean.pop())
            if 0.0 <= r <= 5.0:
                rating = r
                break
        except ValueError:
            break

    # Identify price (line matching ₹...for two)
    price: str | None = None
    for i, ln in enumerate(clean):
        if _PRICE_RE.search(ln):
            price = ln
            clean.pop(i)
            break

    # Strip leading deal/feature noise from the top
    while clean and (_DEAL_RE.search(clean[0]) or _FEATURE_RE.match(clean[0])):
        clean.pop(0)

    # After stripping, the remaining top lines should be: NAME, LOCALITY, CUISINE
    if len(clean) < 2:
        return None
    name = clean[0]
    locality = clean[1] if len(clean) > 1 else ""
    cuisine = clean[2] if len(clean) > 2 else "unknown"

    # The href identifier is the slug after the city segment.
    rid = href.rstrip("/").split("/")[-1].split("?")[0]
    return {
        "id": f"eazy_{rid}",
        "name": name,
        "cuisine": cuisine,
        "locality": locality,
        "price_for_two": price or "",
        "rating": rating if rating is not None else 0.0,
        "lat": 0.0,           # not provided by listing page
        "lng": 0.0,
        "distance_km": 0.0,   # sentinel; see module docstring
        "eazydiner_url": href,
        "source": "eazydiner",
    }


# --- the Playwright scrape (async) ------------------------------------------
async def _scrape_async(city_slug: str, pages: int, headless: bool) -> list[dict]:
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError as e:
        raise PermanentProviderError(
            f"playwright not installed ({e}). "
            "Run: pip install playwright && python -m playwright install chromium"
        ) from e

    results: list[dict] = []
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=headless)
        except Exception as e:  # noqa: BLE001 — Chromium binary missing, etc.
            raise PermanentProviderError(
                f"failed to launch chromium ({e}). "
                "Run: python -m playwright install chromium") from e

        try:
            context = await browser.new_context(
                user_agent=USER_AGENT,
                locale="en-IN",
                timezone_id="Asia/Kolkata",
            )
            page = await context.new_page()

            for page_num in range(1, pages + 1):
                url = (f"{BASE_URL}/{city_slug}/restaurants"
                       if page_num == 1
                       else f"{BASE_URL}/{city_slug}/restaurants?page={page_num}")
                log.info("scraping %s", url)
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded",
                                           timeout=45_000)
                except Exception as e:  # noqa: BLE001 — page nav timeout etc.
                    raise TransientProviderError(
                        f"eazydiner page nav failed ({e})") from e

                if resp is not None and resp.status == 404:
                    raise PermanentProviderError(
                        f"eazydiner has no listing for slug {city_slug!r} (404)")
                if resp is not None and 500 <= resp.status < 600:
                    raise TransientProviderError(
                        f"eazydiner returned {resp.status} for {url}")

                # Let lazy-loaded cards render; scroll triggers more if any
                await asyncio.sleep(6)
                await page.evaluate("window.scrollBy(0, 1500)")
                await asyncio.sleep(2)

                # Cloudflare / bot-wall sniff
                body_low = (await page.evaluate("() => document.body.innerText")).lower()
                if ("just a moment" in body_low or
                        "checking your browser" in body_low):
                    raise TransientProviderError(
                        "eazydiner served a bot-challenge page; aborting")

                # Pull every card by walking up from the name span.
                raw_cards = await page.evaluate("""(selector) => {
                    const names = Array.from(document.querySelectorAll(selector));
                    return names.map((nameEl) => {
                        let card = nameEl;
                        for (let i = 0; i < 8; i++) {
                            if (!card.parentElement) break;
                            const pText = (card.parentElement.innerText || '').length;
                            const cText = (card.innerText || '').length;
                            if (pText > cText * 3 + 200) break;
                            card = card.parentElement;
                        }
                        const a = card.querySelector('a[href*="/"]');
                        return {
                            href: a ? a.getAttribute('href') : '',
                            text: card.innerText || ''
                        };
                    });
                }""", NAME_SELECTOR)

                for raw in raw_cards:
                    href = raw.get("href") or ""
                    if not href or "/restaurant" in href or "?page=" in href:
                        # skip pagination links and footer category links
                        if "?page=" in href or not href.startswith(("http", "/")):
                            continue
                    href_full = href if href.startswith("http") else f"{BASE_URL}{href}"
                    lines = (raw.get("text") or "").split("\n")
                    parsed = _parse_card_text(lines, href_full)
                    if parsed and not any(r["id"] == parsed["id"] for r in results):
                        results.append(parsed)

                log.info("page %d: %d cumulative cards", page_num, len(results))

        finally:
            await browser.close()

    return results


# --- public entry point ------------------------------------------------------
def search_restaurants_eazydiner(city: str,
                                 cuisine: str | None = None,
                                 limit: int = 5,
                                 **_: object) -> list[dict]:
    """Return up to `limit` restaurants for `city` from EazyDiner.

    Filtering:
      - cuisine: case-insensitive substring match on the card's cuisine line.
                 None / "" disables the filter.

    Ranking: descending by rating (4.9 first), then by listing order
    (EazyDiner's featured/sponsored placement).

    Caching: results per city are cached for EAZYDINER_CACHE_MIN minutes
    (default 60) in memory and at data/eazydiner_cache_<slug>.json.
    """
    s = get_settings()
    slug = _city_slug(city)
    pages = int(os.environ.get("EAZYDINER_PAGES", "1"))
    headless = os.environ.get("EAZYDINER_HEADLESS", "true").lower() != "false"
    cache_min = int(os.environ.get("EAZYDINER_CACHE_MIN", "60"))
    cache_path = DATA_DIR / f"eazydiner_cache_{slug}.json"

    # --- cache lookup ---
    cached = _mem_cache.get(slug)
    now = time.time()
    if cached and now - cached[0] < cache_min * 60:
        log.info("eazydiner cache-hit (memory) for %s, %d entries",
                 slug, len(cached[1]))
        all_rows = cached[1]
    elif cache_path.exists() and (now - cache_path.stat().st_mtime < cache_min * 60):
        try:
            all_rows = json.loads(cache_path.read_text(encoding="utf-8"))
            log.info("eazydiner cache-hit (disk) for %s, %d entries",
                     slug, len(all_rows))
            _mem_cache[slug] = (now, all_rows)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("disk cache unreadable (%s); re-scraping", e)
            all_rows = []
    else:
        all_rows = []

    # --- live scrape if cache miss ---
    if not all_rows:
        log.info("eazydiner scraping live for %s (pages=%d, headless=%s)",
                 slug, pages, headless)
        try:
            all_rows = asyncio.run(_scrape_async(slug, pages, headless))
        except RuntimeError as e:
            if "already running" in str(e).lower():
                # Inside an existing event loop (e.g. Jupyter); rare for CLI.
                raise PermanentProviderError(
                    "cannot run playwright from inside a running event loop "
                    "(use a fresh terminal, not Jupyter)") from e
            raise
        if all_rows:
            _mem_cache[slug] = (now, all_rows)
            try:
                cache_path.write_text(
                    json.dumps(all_rows, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                log.info("wrote disk cache %s (%d entries)",
                         cache_path, len(all_rows))
            except OSError as e:
                log.warning("could not write disk cache (%s); continuing", e)

    # --- filter + rank + limit ---
    rows = all_rows
    if cuisine:
        cl = cuisine.lower()
        rows = [r for r in rows if cl in r.get("cuisine", "").lower()]
    rows = sorted(rows, key=lambda r: r.get("rating", 0.0), reverse=True)
    log.info("eazydiner returning %d/%d after filter cuisine=%r",
             min(limit, len(rows)), len(all_rows), cuisine)
    return rows[:limit]
