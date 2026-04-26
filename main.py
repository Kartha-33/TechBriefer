#!/usr/bin/env python3
"""Second Brain daily news orchestrator.

Usage:
  .venv/bin/python3 main.py            # normal daily run
  .venv/bin/python3 main.py --force    # ignore seen-URL filter
  .venv/bin/python3 main.py --dry-run  # skip LLM call and file write
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import yaml
from openai import OpenAI

import db
import enricher
import ranker
import scraper
import summarizer
import writer

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def _build_client(cfg: dict):
    llm = cfg["llm"]
    return OpenAI(base_url=llm["base_url"], api_key="ollama"), llm["model"]


def _build_weights(sources: list) -> dict:
    return {s["name"]: int(s.get("weight", 5)) for s in sources}


def run(force: bool = False, dry_run: bool = False) -> None:
    cfg = _load_config()
    pipeline = cfg.get("pipeline", {})
    quotas = cfg.get("quotas", {"ai": 5, "biology": 4, "startups": 4})

    t0 = time.time()
    print(f"[1/7] Scraping {len(scraper.load_sources())} feeds...")
    all_stories = scraper.fetch_all(cfg)
    print(f"      {len(all_stories)} unique stories ({time.time()-t0:.1f}s)")

    seen = set() if force else db.get_seen_urls(days=int(pipeline.get("history_days", 7)))
    fresh = [s for s in all_stories if s.url not in seen]
    print(f"[2/7] Fresh stories (not seen in {pipeline.get('history_days', 7)}d): {len(fresh)}"
          + (" [forced]" if force else ""))
    if not fresh:
        print("Nothing new today. Exiting.")
        return

    sources_def = scraper.load_sources()
    weights = _build_weights(sources_def)

    t1 = time.time()
    ranked = ranker.cluster_and_score(fresh, weights)
    print(f"[3/7] Ranked {len(ranked)} stories ({time.time()-t1:.1f}s)")

    quota_ordered = ranker.apply_quotas(ranked, quotas)
    prefilter_n = int(pipeline.get("prefilter_top", 35))
    candidates = quota_ordered[:prefilter_n]
    print(f"[4/7] Prefiltered to top {len(candidates)} candidates")

    deep_n = int(pipeline.get("top_deep", 12))
    deep = candidates[:deep_n]
    quick_pool = candidates[deep_n:]

    t2 = time.time()
    enricher.enrich_all(deep, cfg)
    print(f"[5/7] Enriched {len(deep)} deep stories ({time.time()-t2:.1f}s)")

    t3 = time.time()
    if dry_run:
        print("[6/7] --dry-run: skipping LLM call")
        summaries = {
            s.url: (5, (s.summary or s.title or "")[:240]) for s in deep
        }
    else:
        client, model = _build_client(cfg)
        print(f"[6/7] LLM summarize ({len(deep)} stories via {model})...")
        summaries = summarizer.summarize(deep, client, model)
    deep.sort(key=lambda s: summaries.get(s.url, (0, ""))[0], reverse=True)
    print(f"      summary stage: {time.time()-t3:.1f}s")

    t4 = time.time()
    content = writer.build_note(
        deep=deep,
        summaries=summaries,
        quick_hits=quick_pool,
        config=cfg,
        total_sources=len({s.source for s in all_stories}),
    )
    if dry_run:
        print("[7/7] --dry-run: not writing file. Preview:\n")
        print(content[:1500])
        print("\n... [truncated] ...")
        return

    note_path = writer.write_obsidian_note(content, cfg, deep + quick_pool)
    db.mark_seen(deep + quick_pool)
    print(f"[7/7] Wrote {note_path} ({time.time()-t4:.1f}s)")
    print(f"\nDone in {time.time()-t0:.1f}s total.")
    print(f"   {len(deep)} deep summaries + {len(quick_pool)} quick hits")
    print(f"   {len({s.source for s in deep + quick_pool})} unique sources")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Second Brain daily news brief")
    parser.add_argument("--force", action="store_true", help="ignore the 7-day seen-URL filter")
    parser.add_argument("--dry-run", action="store_true", help="skip the LLM call and file write")
    args = parser.parse_args()
    _setup_logging()
    run(force=args.force, dry_run=args.dry_run)
