"""Probe whether EazyDiner Coimbatore listing page is accessible from your IP.

Run this AFTER installing Playwright:
    pip install playwright
    python -m playwright install chromium

Then:
    python test_scraper_probe.py

What this script does:
  1. Opens a VISIBLE Chrome browser (not headless) so you can watch.
  2. Goes to EazyDiner's Coimbatore restaurant listing page.
  3. Waits up to 30 seconds for the page to settle.
  4. Reports what it sees:
     - 'OK' if a normal restaurant listing renders.
     - 'CLOUDFLARE' if a bot-challenge page appears.
     - 'OTHER' for anything else (and prints page title + first 200 chars).
  5. Takes a screenshot saved as data/probe_screenshot.png so we can both look at it.

No scraping happens here. No data collected. Just: does the page load?
"""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path

PROBE_URL = "https://www.eazydiner.com/coimbatore/restaurants"
SCREENSHOT_PATH = Path("data/probe_screenshot.png")


async def main() -> int:
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:
        print("Playwright not installed. Run:")
        print("  pip install playwright")
        print("  python -m playwright install chromium")
        return 1

    SCREENSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        # headless=False so YOU can watch what happens. Critical for the probe.
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )
        page = await context.new_page()

        print(f"Opening {PROBE_URL} ...")
        print("(A Chrome window will pop up. Don't close it.)")
        try:
            await page.goto(PROBE_URL, wait_until="domcontentloaded", timeout=45_000)
        except Exception as e:
            print(f"\nFAILED to load page: {e}")
            await browser.close()
            return 2

        # Let any client-side JS settle (Cloudflare challenges, React hydration, etc).
        await asyncio.sleep(8)

        title = await page.title()
        body_text = await page.evaluate("() => document.body.innerText")
        body_text_low = body_text.lower()

        await page.screenshot(path=str(SCREENSHOT_PATH), full_page=False)

        print(f"\nPage title:    {title!r}")
        print(f"First 200 chars of body text:\n  {body_text[:200]!r}")

        if "just a moment" in body_text_low or "checking your browser" in body_text_low \
                or "cloudflare" in body_text_low and "challenge" in body_text_low:
            print("\nVERDICT: CLOUDFLARE challenge detected.")
            print("EazyDiner is blocking automated access from your IP.")
            print("Stop here. See screenshot at:", SCREENSHOT_PATH.resolve())
            verdict = "CLOUDFLARE"
        elif "restaurant" in body_text_low or "book" in body_text_low or "coimbatore" in body_text_low:
            print("\nVERDICT: OK — page rendered normally.")
            print("Restaurant content is present. Safe to build a scraper provider.")
            verdict = "OK"
        else:
            print("\nVERDICT: OTHER — unfamiliar content.")
            print("Check the screenshot to see what loaded:", SCREENSHOT_PATH.resolve())
            verdict = "OTHER"

        print(f"\nScreenshot saved: {SCREENSHOT_PATH.resolve()}")
        print("Keep the browser open for 10 more seconds so you can look at it...")
        await asyncio.sleep(10)
        await browser.close()
        return 0 if verdict == "OK" else 3


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
