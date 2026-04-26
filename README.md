# TechBriefer

A small, hallucination-proof daily news pipeline that pulls from ~50
high-trust RSS feeds, ranks stories by relevance + recency + source trust,
and writes a Markdown brief into your Obsidian vault every morning.

The whole thing runs locally on your machine. No paid APIs, no embeddings,
no cloud — just `feedparser`, `requests`, `BeautifulSoup`, and a local Ollama
model for the 2-sentence summaries.

## What you get

A note like `01 Daily Briefs/2026-04-26 Tech Brief.md` with five sections:

1. **Top Stories** — the 5 highest-scored stories of the day, with images
   and 2-sentence LLM summaries
2. **AI & Machine Learning** — 5 stories with summaries
3. **Biology & Life Sciences** — 4 stories with summaries
4. **Startups & Venture Capital** — 4 stories with summaries
5. **Quick Hits** — ~30 more stories from the prefilter pool, RSS-derived
   one-liners (no LLM, so no hallucination risk)

Stories link to the original sources, include `[[wikilinks]]` for entities
the regex finds (people, orgs, topics), and tag the source name so you can
trace anything you read.

## Why it doesn't hallucinate

- The LLM **never sees URLs or Markdown**. It only sees `id`, source name,
  title, and 600 chars of grounding text. Python maps the returned `i` back
  to the story it sent in.
- `temperature=0`, `response_format={"type": "json_object"}`, max
  320-character summaries.
- A validator rejects empty / too-short / too-long / hedging / refusal /
  Reddit-boilerplate / `INSUFFICIENT` outputs and falls back to the cleaned
  RSS summary.
- `og:image` / `twitter:image` are only accepted if they start with
  `https://`. The LLM never picks images.
- A 7-day SQLite dedup table (`history.db`, gitignored) prevents the same
  story from repeating across runs.
- Sources are a closed set in `sources.yaml`. The LLM can never widen it.

## Pipeline

```
scraper.fetch_all          (10-worker ThreadPool, ~5-10s for 50 feeds)
  └─> db.get_seen_urls(7d)
        └─> ranker.cluster_and_score   (SequenceMatcher 0.78 + heuristic)
              └─> ranker.apply_quotas  (ai 5 / biology 4 / startups 4)
                    └─> prefilter top 40
                          └─> enricher.enrich_all  (og:image + body 2000c)
                                └─> summarizer.summarize  (2 batches × 6
                                     stories, single grounded JSON call each)
                                      └─> validate
                                            └─> writer.build_note
                                                  └─> write to Obsidian vault
                                                        └─> db.mark_seen
```

The whole thing runs in 2–4 minutes on a Mac with `llama3.2` (3B) under
Ollama.

## Setup

### Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com) installed and running (`ollama serve`)
- The `llama3.2` model pulled: `ollama pull llama3.2`
- An Obsidian vault somewhere (anywhere — you'll point at it in
  `config.yaml`)

### Install

```bash
git clone https://github.com/Kartha-33/TechBriefer.git
cd TechBriefer
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Configure

Open `config.yaml` and update `obsidian.vault_path` to point at your vault:

```yaml
obsidian:
  vault_path: "~/Documents/Obsidian/MyVault"
  daily_notes_folder: "01 Daily Briefs"
```

The other defaults (LLM model, scraper timeouts, pipeline sizes, category
quotas) work out of the box. Edit `sources.yaml` to add or remove feeds and
to retune the per-feed `weight`.

### Try it once

```bash
.venv/bin/python3 main.py --force
```

`--force` ignores the 7-day seen-URL filter so you can run it twice in a row
while testing. `--dry-run` skips the LLM call and the file write.

After a couple of minutes you should see the brief at
`<your-vault>/01 Daily Briefs/YYYY-MM-DD Tech Brief.md`.

### Schedule it (macOS)

There's a `launchd` template at `com.secondbrain.dailynews.plist.template`.
Copy it to `~/Library/LaunchAgents/com.secondbrain.dailynews.plist`, replace
every `REPLACE_WITH_YOUR_PATH` with the absolute path to your local clone,
then load it:

```bash
cp com.secondbrain.dailynews.plist.template \
   ~/Library/LaunchAgents/com.secondbrain.dailynews.plist
# edit the file: replace REPLACE_WITH_YOUR_PATH with e.g. /Users/<you>/code/TechBriefer
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.secondbrain.dailynews.plist
```

It will fire at 07:00 every morning. Logs go to `/tmp/secondbrain.log` and
`/tmp/secondbrain.err`.

To stop it: `launchctl bootout gui/$(id -u)/com.secondbrain.dailynews`.

### Schedule it (Linux)

Add a crontab entry:

```cron
0 7 * * * cd /path/to/TechBriefer && .venv/bin/python3 main.py >> /tmp/secondbrain.log 2>&1
```

## Files

| File | Purpose |
|---|---|
| `sources.yaml` | 50+ trust-weighted RSS feeds + Reddit feeds with upvote gating |
| `config.yaml` | Vault path, Ollama settings, scraper / pipeline / quota config |
| `db.py` | SQLite seen-story history (7-day dedup, 30-day janitor) |
| `scraper.py` | Concurrent RSS fetch via `requests` + `feedparser`; HTML-decoded summaries; in-pass URL dedup |
| `ranker.py` | Heuristic-only score `w**1.3 / (h+2)**1.4 + cross_mention*3`; `SequenceMatcher` clustering at 0.78; category quotas |
| `enricher.py` | Concurrent article fetch; only when RSS summary < 250 chars; pulls `og:image` and 2000 chars of body |
| `summarizer.py` | 2 batches of 6 stories; strict JSON output; per-story grounding; Python-side validator and fallback |
| `entities.py` | Regex over PEOPLE / ORGS / TOPICS, up to 6 `[[wikilinks]]` per story |
| `writer.py` | Builds the Markdown brief; sections overflow into the quick pool with RSS summaries when the LLM pool runs short |
| `main.py` | Orchestrator with `--force` and `--dry-run`, prints stage timings |

## Tuning notes

- **Want more stories per section?** Bump `quotas.ai`, `quotas.biology`, or
  `quotas.startups` in `config.yaml`.
- **Want deeper LLM coverage?** Bump `pipeline.top_deep` (current: 12). Each
  extra batch of 6 stories adds roughly 90s to the run.
- **Want a different model?** Change `llm.model` in `config.yaml` to any
  Ollama-served model; the rest of the pipeline doesn't care.
- **A feed is failing?** Watch the `WARNING` log lines from the scraper.
  Most failures are stale URLs — search for the publication's current
  feed URL and update `sources.yaml`. Reddit feeds need a real browser
  User-Agent (already configured).

## License

MIT — see [LICENSE](LICENSE).
