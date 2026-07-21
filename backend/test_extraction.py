"""
Standalone extraction test script.

Proof-of-concept for the Playwright + trafilatura content extraction
pipeline — run this manually against a hardcoded list of article URLs
before the same logic gets wired into the backend's ingestion service.
Each URL's extracted text is saved to its own .txt file for review.

Run with:
    python test_extraction.py

Requirements:
    pip install playwright trafilatura
    playwright install chromium
"""

from pathlib import Path

import trafilatura
from playwright.sync_api import sync_playwright

# Replace these with the 4 real article URLs you want to test
URLS = [
    "https://telquel.ma/instant-t/2022/09/29/automobile-le-groupe-japonais-yazaki-inaugure-sa-4eme-usine-au-maroc-a-kenitra_1785998/",
    "https://telquel.ma/instant-t/2026/07/13/e-paiements-bank-al-maghrib-abaisse-a-05-ht-les-frais-dinterchange-domestique-a-compter-du-1er-octobre-2026_1998695/",
    "https://www.france24.com/en/middle-east/20260712-live-us-attacks-iran-over-ship-being-hit-in-strait-of-hormuz",
    "https://www.aljazeera.net/politics/2026/4/22/%D9%87%D8%B1%D9%85%D8%B2-%D9%82%D8%B5%D8%A9-%D9%85%D8%B6%D9%8A%D9%82-%D9%81%D8%AA%D8%AD%D9%87-%D8%A3%D8%B5%D8%B9%D8%A8-%D9%85%D9%86-%D8%BA%D9%84%D9%82%D9%87",
]

OUTPUT_DIR = Path("extracted_articles")
TIMEOUT_MS = 30000

# A realistic user agent reduces the odds of a site treating an obviously
# headless browser differently than a normal visitor.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
)


def extract_one(page, url: str):
    """Returns (status, text) for one URL. Never raises — failures are
    captured and returned as a 'Failed' status instead, matching the
    ingestion_status values the real backend will use."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        html = page.content()
    except Exception as e:
        return "Failed", f"Could not load page: {e}"

    text = trafilatura.extract(html)
    if not text or not text.strip():
        return "Failed", (
            "Page loaded, but no main content could be extracted "
            "(possible paywall, cookie wall, or unsupported page layout)."
        )

    return "Successful", text


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 800})
        page = context.new_page()

        for i, url in enumerate(URLS, start=1):
            print(f"[{i}/{len(URLS)}] Extracting: {url}")
            status, text = extract_one(page, url)

            output_file = OUTPUT_DIR / f"article_{i}.txt"
            output_file.write_text(f"URL: {url}\nSTATUS: {status}\n\n{text}", encoding="utf-8")

            print(f"    -> {status} ({len(text)} chars) -> {output_file}")

        browser.close()

    print(f"\nDone. Check the '{OUTPUT_DIR}/' folder for results.")


if __name__ == "__main__":
    main()