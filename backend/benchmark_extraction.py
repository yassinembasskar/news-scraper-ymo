"""
One-off benchmark script (temporary, not part of the app) — re-runs the real
extraction pipeline (Playwright + trafilatura, identical to
services/extractor.py's _extract_one) against 100 random articles drawn from
both the "Successful" and "Failed" pools, purely to measure current-day
timing and failure rate. Read-only: never writes any result back to Mongo,
so it can't corrupt existing article state.

Run inside the backend container:
    docker exec news-scraper-ymo-backend-1 python /app/benchmark_extraction.py
"""

import random
import time

from playwright.sync_api import sync_playwright
import trafilatura

from app.database import articles

TIMEOUT_MS = 30000
SAMPLE_SIZE = 100
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
)


def extract_one(page, url: str) -> tuple:
    """Identical logic to extractor.py's _extract_one — kept as a standalone
    copy here so this benchmark has no dependency on the app's DB-writing
    extraction module."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        html = page.content()
    except Exception as e:
        return "Failed", f"Could not load page: {e}"

    text = trafilatura.extract(html)
    if not text or not text.strip():
        return "Failed", "Page loaded, but no main content could be extracted."

    return "Successful", text


def main():
    pool = list(
        articles.find(
            {"ingestion_status": {"$in": ["Successful", "Failed"]}},
            {"url": 1, "title": 1, "ingestion_status": 1},
        )
    )
    print(f"Eligible pool: {len(pool)} articles (Successful + Failed)", flush=True)

    sample = random.sample(pool, min(SAMPLE_SIZE, len(pool)))
    total = len(sample)
    print(f"Sampled {total} articles for the benchmark", flush=True)

    success_durations = []
    failed_durations = []
    results = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 800})
        page = context.new_page()

        run_start = time.monotonic()
        for i, doc in enumerate(sample, 1):
            url = doc["url"]
            t0 = time.monotonic()
            status, detail = extract_one(page, url)
            duration = time.monotonic() - t0

            if status == "Successful":
                success_durations.append(duration)
            else:
                failed_durations.append(duration)

            results.append({
                "title": doc.get("title", "")[:70],
                "original_status": doc.get("ingestion_status"),
                "new_status": status,
                "duration": duration,
            })
            print(
                f"[{i}/{total}] {status:10s} {duration:6.1f}s "
                f"(was {doc.get('ingestion_status')}) :: {doc.get('title', '')[:60]}",
                flush=True,
            )

        total_time = time.monotonic() - run_start
        browser.close()

    n_success = len(success_durations)
    n_failed = len(failed_durations)
    fail_pct = (n_failed / total * 100) if total else 0

    print("\n" + "=" * 60, flush=True)
    print("BENCHMARK RESULTS", flush=True)
    print("=" * 60, flush=True)
    print(f"Sample size:            {total}", flush=True)
    print(f"Total time:             {total_time:.1f}s ({total_time/60:.1f} min)", flush=True)
    print(f"Successful extractions: {n_success}", flush=True)
    print(f"Failed extractions:     {n_failed}", flush=True)
    print(f"Failure rate:           {fail_pct:.1f}%", flush=True)
    if n_success:
        print(
            f"Avg time / success:     {sum(success_durations)/n_success:.1f}s "
            f"(total {sum(success_durations):.1f}s)",
            flush=True,
        )
    if n_failed:
        print(
            f"Avg time / failure:     {sum(failed_durations)/n_failed:.1f}s "
            f"(total {sum(failed_durations):.1f}s)",
            flush=True,
        )
    print(f"Avg time / article:     {total_time/total:.1f}s", flush=True)


if __name__ == "__main__":
    main()
