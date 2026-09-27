"""
One-off benchmark script (temporary, not part of the app) — runs the
title-gate LLM pre-filter (app.services.llm_insight.gate_title) against 100
random article titles from across the whole collection, purely to measure
timing (per-title, per-character) and the relevant/archived split. Read-only:
never writes anything back to Mongo.

Run inside the backend container:
    docker exec news-scraper-ymo-backend-1 python /app/benchmark_title_gate.py
"""

import random
import time

from app.database import articles
from app.services import llm_insight

SAMPLE_SIZE = 100


def main():
    pool = list(articles.find({}, {"title": 1, "language": 1}))
    print(f"Full pool: {len(pool)} articles", flush=True)

    sample = random.sample(pool, min(SAMPLE_SIZE, len(pool)))
    total = len(sample)
    print(f"Sampled {total} titles for the benchmark", flush=True)

    durations = []
    char_counts = []
    relevant_count = 0
    archived_count = 0
    error_count = 0

    run_start = time.monotonic()
    for i, doc in enumerate(sample, 1):
        title = doc.get("title", "")
        language = doc.get("language") or "en"

        t0 = time.monotonic()
        try:
            relevant = llm_insight.gate_title(title, language)
        except Exception as e:
            duration = time.monotonic() - t0
            error_count += 1
            print(f"[{i}/{total}] ERROR  {duration:5.1f}s :: {e} :: {title[:60]}", flush=True)
            continue
        duration = time.monotonic() - t0

        durations.append(duration)
        char_counts.append(len(title))

        if relevant:
            relevant_count += 1
            tag = "RELEVANT"
        else:
            archived_count += 1
            tag = "ARCHIVE "

        print(
            f"[{i}/{total}] {tag} {duration:5.1f}s ({len(title):3d} chars) :: {title[:60]}",
            flush=True,
        )

    total_time = time.monotonic() - run_start
    n = len(durations)
    total_chars = sum(char_counts)
    gated = relevant_count + archived_count

    print("\n" + "=" * 60, flush=True)
    print("TITLE-GATE BENCHMARK RESULTS", flush=True)
    print("=" * 60, flush=True)
    print(f"Sample size:              {total}", flush=True)
    print(f"Successfully gated:       {n}", flush=True)
    print(f"Errors:                   {error_count}", flush=True)
    print(f"Total time:               {total_time:.1f}s ({total_time/60:.1f} min)", flush=True)
    if n:
        print(f"Avg time / title:         {sum(durations)/n:.2f}s", flush=True)
        print(f"Total characters gated:   {total_chars}", flush=True)
        print(f"Avg time / character:     {sum(durations)/total_chars:.4f}s ({(sum(durations)/total_chars)*1000:.1f}ms)", flush=True)
    if gated:
        print(f"Relevant:                 {relevant_count} ({relevant_count/gated*100:.1f}%)", flush=True)
        print(f"Archived (not relevant):  {archived_count} ({archived_count/gated*100:.1f}%)", flush=True)


if __name__ == "__main__":
    main()
