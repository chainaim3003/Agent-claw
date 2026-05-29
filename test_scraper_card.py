"""Focused inspection of a single EazyDiner restaurant card.

Strategy 1 in the previous probe revealed:
  - The class 'listing_res_name__uVIN8' appears 9 times -> 9 restaurant cards.
  - Anchor hrefs follow pattern: https://www.eazydiner.com/coimbatore/<slug>

This script targets THAT class, walks up to the full card container, and prints
each card's complete text + structure so we can identify where rating, cuisine,
location, price-for-two, and deal info sit.

Run after test_scraper_inspect.py.
"""
from __future__ import annotations
import asyncio
import sys

URL = "https://www.eazydiner.com/coimbatore/restaurants"


async def main() -> int:
    from playwright.async_api import async_playwright  # type: ignore

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"),
            locale="en-IN", timezone_id="Asia/Kolkata",
        )
        page = await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=45_000)
        await asyncio.sleep(8)
        await page.evaluate("window.scrollBy(0, 2500)")
        await asyncio.sleep(3)

        cards = await page.evaluate("""() => {
            const names = Array.from(document.querySelectorAll('[class*="listing_res_name"]'));
            return names.slice(0, 5).map((nameEl, idx) => {
                // Walk up to the largest container that's still about ONE restaurant.
                let card = nameEl;
                for (let i = 0; i < 8; i++) {
                    if (!card.parentElement) break;
                    const pText = (card.parentElement.innerText || '').length;
                    const cText = (card.innerText || '').length;
                    // Stop expanding once the parent's text is 3x ours (we hit a list container).
                    if (pText > cText * 3 + 200) break;
                    card = card.parentElement;
                }
                const anchor = card.querySelector('a[href*="/coimbatore/"]');
                return {
                    idx: idx,
                    card_class: card.className,
                    href: anchor ? anchor.getAttribute('href') : null,
                    name: (nameEl.innerText || '').trim(),
                    full_text: (card.innerText || '').trim(),
                    inner_classes: Array.from(new Set(
                        Array.from(card.querySelectorAll('[class]'))
                            .flatMap(el => Array.from(el.classList))
                            .filter(c => c.startsWith('listing_'))
                    )).slice(0, 20)
                };
            });
        }""")

        print(f"\nFound {len(cards)} cards. Showing first 5:\n")
        for c in cards:
            print("=" * 70)
            print(f"Card #{c['idx']}: {c['name']}")
            print(f"  href:        {c['href']}")
            print(f"  card class:  {c['card_class']!r}")
            print(f"  inner listing_* classes used inside card:")
            for cls in c["inner_classes"]:
                print(f"    - {cls}")
            print(f"  FULL CARD TEXT (line-by-line):")
            for line in c["full_text"].split("\n"):
                line = line.strip()
                if line:
                    print(f"    | {line}")
            print()

        await asyncio.sleep(3)
        await browser.close()
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
