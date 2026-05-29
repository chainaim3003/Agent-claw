"""DOM inspection probe for EazyDiner Coimbatore page.

Why this exists:
  The first probe proved the page loads. But before writing a real scraper, we
  need to know what HTML classes/structures hold restaurant data. EazyDiner's
  markup isn't documented anywhere; the only honest path is: load the page,
  inspect the DOM, find candidate selectors, report them.

What this script does:
  1. Loads the EazyDiner Coimbatore listing page.
  2. Heuristically finds elements that look like restaurant cards using
     several strategies (class names containing 'restaurant', 'card', 'item').
  3. For the most promising container, prints:
       - the class name(s)
       - how many siblings exist (gives us 'how many restaurants per page')
       - the first card's text content
       - the first card's HTML structure (truncated)
  4. Saves the full page HTML to data/eazydiner_dump.html for offline review.

This is pure inspection. No data exfiltration, no booking attempts.
Run AFTER test_scraper_probe.py succeeded.

Usage:
    python test_scraper_inspect.py
"""
from __future__ import annotations
import asyncio
import sys
from collections import Counter
from pathlib import Path

PROBE_URL = "https://www.eazydiner.com/coimbatore/restaurants"
HTML_DUMP_PATH = Path("data/eazydiner_dump.html")
HTML_DUMP_PATH.parent.mkdir(parents=True, exist_ok=True)


async def main() -> int:
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:
        print("Playwright not installed.")
        return 1

    async with async_playwright() as p:
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
        await page.goto(PROBE_URL, wait_until="domcontentloaded", timeout=45_000)
        await asyncio.sleep(10)  # let lazy-loaded restaurant cards render

        # Scroll a bit to trigger any lazy-load
        await page.evaluate("window.scrollBy(0, 1000)")
        await asyncio.sleep(2)
        await page.evaluate("window.scrollBy(0, 1500)")
        await asyncio.sleep(2)

        # Save full HTML for offline inspection (so we don't need a third probe).
        html = await page.content()
        HTML_DUMP_PATH.write_text(html, encoding="utf-8")
        print(f"Full HTML dumped to: {HTML_DUMP_PATH.resolve()} ({len(html):,} chars)")

        # --- Strategy 1: find elements whose class contains 'restaurant' or 'card' ---
        print("\n=== Strategy 1: class-name heuristics ===")
        candidates = await page.evaluate("""() => {
            const all = document.querySelectorAll('[class]');
            const counts = {};
            for (const el of all) {
                for (const cls of el.classList) {
                    const lc = cls.toLowerCase();
                    if (lc.includes('restaurant') || lc.includes('card') ||
                        lc.includes('listing') || lc.includes('item')) {
                        counts[cls] = (counts[cls] || 0) + 1;
                    }
                }
            }
            return counts;
        }""")
        # Show classes that appear 5+ times (likely repeated cards), sorted by count desc.
        repeated = sorted([(c, n) for c, n in candidates.items() if n >= 5],
                          key=lambda x: -x[1])
        if not repeated:
            print("  (no class names matched 'restaurant/card/listing/item' heuristic)")
        for cls, n in repeated[:15]:
            print(f"  count={n:3d}   class={cls!r}")

        # --- Strategy 2: find the smallest repeating block that contains links ---
        print("\n=== Strategy 2: candidate selectors with restaurant-shaped content ===")
        # Look for anchor tags with href containing /restaurant/ or /coimbatore/
        anchors_info = await page.evaluate("""() => {
            const anchors = Array.from(document.querySelectorAll('a[href]'));
            const restaurant_anchors = anchors.filter(a =>
                /\\/restaurant|\\/coimbatore\\//.test(a.getAttribute('href') || ''));
            const parents = restaurant_anchors.slice(0, 30).map(a => {
                let p = a.parentElement;
                let depth = 0;
                while (p && depth < 5) {
                    if (p.className && p.className.length > 0) {
                        return {
                            href: a.getAttribute('href'),
                            text: (a.innerText || '').slice(0, 80),
                            parent_class: p.className,
                            depth: depth
                        };
                    }
                    p = p.parentElement;
                    depth++;
                }
                return {href: a.getAttribute('href'), text: a.innerText};
            });
            return {total_restaurant_anchors: restaurant_anchors.length, samples: parents};
        }""")
        print(f"  Total anchors linking to restaurant pages: {anchors_info['total_restaurant_anchors']}")
        print(f"  Sample of up to 30:")
        parent_classes = Counter()
        for s in anchors_info.get("samples", [])[:20]:
            text = s.get("text") or ""
            href = s.get("href") or ""
            parent_cls = s.get("parent_class", "")
            print(f"    text={text!r:50s}  href={href[:60]!r}")
            if parent_cls:
                parent_classes[parent_cls] += 1
        print(f"\n  Most common parent classes (these wrap restaurant cards):")
        for cls, n in parent_classes.most_common(5):
            print(f"    n={n:2d}  {cls!r}")

        # --- Strategy 3: first restaurant card's text & html ---
        print("\n=== Strategy 3: sample first restaurant card ===")
        sample = await page.evaluate("""() => {
            const anchors = Array.from(document.querySelectorAll('a[href]'))
                .filter(a => /\\/restaurant|\\/coimbatore\\//.test(a.getAttribute('href') || ''));
            if (anchors.length === 0) return null;
            // Walk up until we find a container with a reasonable amount of text.
            let el = anchors[0];
            for (let i = 0; i < 6; i++) {
                if (el.parentElement && (el.parentElement.innerText || '').length > 80) {
                    el = el.parentElement;
                } else { break; }
            }
            return {
                tag: el.tagName,
                class: el.className,
                text: (el.innerText || '').slice(0, 600),
                html_snippet: (el.outerHTML || '').slice(0, 2000)
            };
        }""")
        if sample:
            print(f"  tag:   {sample['tag']}")
            print(f"  class: {sample['class']!r}")
            print(f"  TEXT (first 600 chars):")
            for line in sample["text"].split("\n"):
                if line.strip():
                    print(f"    | {line}")
            print(f"\n  HTML snippet (first 2000 chars):")
            print("    " + sample["html_snippet"][:2000].replace("\n", "\n    "))
        else:
            print("  No restaurant-link anchors found on this page.")

        await asyncio.sleep(3)
        await browser.close()
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
