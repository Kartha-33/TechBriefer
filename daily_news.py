#!/usr/bin/env python3
"""
Daily Tech News Agent
=====================
Fetches top tech/science news from Reddit, Twitter (Nitter), and blogs,
summarises everything with a local Ollama LLM, and writes an Obsidian note.

Run manually:  python daily_news.py
Run quietly:   python daily_news.py --quiet
Force today:   python daily_news.py --force
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.text import Text

# ── Globals ────────────────────────────────────────────────────────────────────

console = Console()
SCRIPT_DIR = Path(__file__).parent.resolve()


# ── Config Loading ─────────────────────────────────────────────────────────────

def load_config() -> dict:
    config_path = SCRIPT_DIR / "config.yaml"
    sources_path = SCRIPT_DIR / "sources.yaml"
    if not config_path.exists():
        console.print("[red]ERROR:[/red] config.yaml not found. Run setup.sh first.")
        sys.exit(1)
    if not sources_path.exists():
        console.print("[red]ERROR:[/red] sources.yaml not found. Run setup.sh first.")
        sys.exit(1)
    with open(config_path) as f:
        config = yaml.safe_load(f)
    with open(sources_path) as f:
        sources = yaml.safe_load(f)
    config["sources"] = sources
    return config


def resolve_path(path_str: str) -> Path:
    return Path(os.path.expanduser(path_str)).resolve()


# ── Logging ────────────────────────────────────────────────────────────────────

def setup_logging(config: dict, verbose: bool):
    log_file = resolve_path(config["advanced"]["log_file"])
    log_file.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout) if verbose else logging.NullHandler(),
        ],
    )


# ── HTTP Helpers ───────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def safe_get(url: str, timeout: int = 15) -> Optional[requests.Response]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp
    except Exception as e:
        logging.warning(f"Failed to fetch {url}: {e}")
        return None


def extract_article_text(url: str, max_chars: int = 3000) -> str:
    """Fetch a URL and extract the main readable text from the HTML."""
    resp = safe_get(url)
    if not resp:
        return ""
    try:
        soup = BeautifulSoup(resp.text, "lxml")
        # Remove navigation, ads, sidebars, scripts, styles
        for tag in soup(["script", "style", "nav", "header", "footer",
                          "aside", "form", "button", "iframe", "noscript"]):
            tag.decompose()
        # Try to find the main article body
        article = (
            soup.find("article")
            or soup.find(class_=re.compile(r"article|post|content|entry", re.I))
            or soup.find("main")
            or soup.body
        )
        if not article:
            return ""
        text = article.get_text(separator=" ", strip=True)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as e:
        logging.warning(f"Failed to parse article {url}: {e}")
        return ""


def extract_article_image(url: str) -> Optional[str]:
    """Extract the main image from an article (og:image or similar)."""
    resp = safe_get(url)
    if not resp:
        return None
    try:
        soup = BeautifulSoup(resp.text, "lxml")
        # Try Open Graph image first (most reliable)
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            return og_image["content"]
        # Try Twitter card image
        twitter_image = soup.find("meta", attrs={"name": "twitter:image"})
        if twitter_image and twitter_image.get("content"):
            return twitter_image["content"]
        # Try first large img tag as fallback
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if src and not any(x in src.lower() for x in ["icon", "logo", "avatar", "pixel"]):
                # Skip tiny images and logos
                width = img.get("width", "")
                if width and width.isdigit() and int(width) > 200:
                    return src
        return None
    except Exception as e:
        logging.warning(f"Failed to extract image from {url}: {e}")
        return None


# ── Data Structures ────────────────────────────────────────────────────────────

class Story:
    def __init__(self, title: str, url: str, source: str,
                 category: str = "tech", summary: str = "", score: int = 0,
                 body_text: str = "", image_url: str = ""):
        self.title = title
        self.url = url
        self.source = source
        self.category = category
        self.summary = summary      # from RSS feed description
        self.score = score          # Reddit upvotes or 0
        self.body_text = body_text  # full article text (if fetched)
        self.image_url = image_url  # article featured image
        self.ai_summary = ""        # filled in by LLM

    def __repr__(self):
        return f"Story({self.source!r}: {self.title[:60]!r})"


# ── Reddit Scraper ─────────────────────────────────────────────────────────────

def fetch_reddit(config: dict) -> list[Story]:
    stories = []
    cfg = config["sources"]["reddit"]
    subreddits = cfg.get("subreddits", [])
    limit = cfg.get("posts_per_subreddit", 5)
    min_score = cfg.get("min_score", 50)

    for sub in subreddits:
        url = f"https://www.reddit.com/r/{sub}/top.rss?t=day&limit={limit}"
        logging.info(f"Fetching r/{sub}...")
        feed = feedparser.parse(url)
        for entry in feed.entries[:limit]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "")
            content = entry.get("summary", entry.get("content", [{}])[0].get("value", "") if entry.get("content") else "")
            # Extract score from title if present (Reddit RSS sometimes includes it)
            score_match = re.search(r"score: (\d+)", content, re.I)
            score = int(score_match.group(1)) if score_match else 0
            if not title or not link:
                continue
            # Strip HTML from summary
            clean_summary = BeautifulSoup(content, "lxml").get_text(separator=" ", strip=True)[:500]
            stories.append(Story(
                title=title,
                url=link,
                source=f"r/{sub}",
                category="reddit",
                summary=clean_summary,
                score=score,
            ))
        time.sleep(config["advanced"].get("request_delay", 1.0))

    return stories


# ── Nitter / Twitter Scraper ───────────────────────────────────────────────────

def get_working_nitter(instances: list[str], timeout: int = 8) -> Optional[str]:
    """Try each Nitter instance and return the first one that responds."""
    for instance in instances:
        try:
            resp = requests.get(instance, timeout=timeout, headers=HEADERS)
            if resp.status_code == 200:
                return instance.rstrip("/")
        except Exception:
            continue
    return None


def fetch_twitter(config: dict) -> list[Story]:
    stories = []
    cfg = config["sources"]["twitter"]
    if not cfg.get("enabled", True):
        return stories

    accounts = cfg.get("accounts", [])
    tweets_per = cfg.get("tweets_per_account", 3)
    instances = cfg.get("nitter_instances", ["https://nitter.net"])

    base = get_working_nitter(instances)
    if not base:
        logging.warning("No Nitter instance available. Skipping Twitter sources.")
        console.print("[yellow]⚠ No Nitter instance reachable — skipping Twitter.[/yellow]")
        return stories

    logging.info(f"Using Nitter instance: {base}")

    for account in accounts:
        rss_url = f"{base}/{account}/rss"
        logging.info(f"Fetching @{account} from Nitter...")
        feed = feedparser.parse(rss_url)
        count = 0
        for entry in feed.entries:
            if count >= tweets_per:
                break
            title = entry.get("title", "").strip()
            link = entry.get("link", "")
            content = entry.get("summary", "")
            clean = BeautifulSoup(content, "lxml").get_text(separator=" ", strip=True)[:400]
            # Skip retweets of others (they start with "RT @")
            if title.startswith("RT @"):
                continue
            if not title:
                continue
            stories.append(Story(
                title=f"@{account}: {title[:120]}",
                url=link,
                source=f"@{account} (Twitter)",
                category="twitter",
                summary=clean,
            ))
            count += 1
        time.sleep(config["advanced"].get("request_delay", 0.5))

    return stories


# ── Blog RSS Scraper ───────────────────────────────────────────────────────────

def fetch_blogs(config: dict) -> list[Story]:
    stories = []
    cfg = config["sources"]["blogs"]
    feeds = cfg.get("feeds", [])
    articles_per = cfg.get("articles_per_feed", 3)
    timeout = config["advanced"].get("request_timeout", 15)
    fetch_full = config["brief"].get("fetch_full_articles", True)
    max_chars = config["brief"].get("max_article_chars", 3000)

    for feed_info in feeds:
        name = feed_info["name"]
        url = feed_info["url"]
        category = feed_info.get("category", "tech")
        logging.info(f"Fetching {name}...")

        feed = feedparser.parse(url)
        count = 0
        for entry in feed.entries:
            if count >= articles_per:
                break
            title = entry.get("title", "").strip()
            link = entry.get("link", "")
            if not title or not link:
                continue

            # Get summary from RSS first (fast)
            raw_summary = (
                entry.get("summary", "")
                or (entry.get("content", [{}])[0].get("value", "") if entry.get("content") else "")
            )
            clean_summary = BeautifulSoup(raw_summary, "lxml").get_text(separator=" ", strip=True)[:500]

            # Optionally fetch full article body and image
            body = ""
            image = ""
            if fetch_full:
                body = extract_article_text(link, max_chars)
                image = extract_article_image(link) or ""
                time.sleep(config["advanced"].get("request_delay", 1.0))

            stories.append(Story(
                title=title,
                url=link,
                source=name,
                category=category,
                summary=clean_summary,
                body_text=body,
                image_url=image,
            ))
            count += 1

    return stories


# ── LLM Client ─────────────────────────────────────────────────────────────────

def build_llm_client(config: dict):
    """
    Returns an OpenAI-compatible client pointed at either:
    - Ollama local server
    - Any OpenAI-compatible cloud API (Kimi, GLM, Groq, OpenAI, etc.)
    """
    from openai import OpenAI

    provider = config["llm"].get("provider", "ollama")

    if provider == "ollama":
        cfg = config["llm"]["ollama"]
        return OpenAI(
            base_url=cfg.get("base_url", "http://localhost:11434") + "/v1",
            api_key="ollama",  # Ollama doesn't need a real key
        ), cfg.get("model", "qwen2.5:7b")

    else:  # openai-compatible (Kimi, GLM, Groq, etc.)
        cfg = config["llm"]["openai"]
        api_key = cfg.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            console.print("[red]ERROR:[/red] No API key set. Add it to config.yaml under llm.openai.api_key")
            sys.exit(1)
        return OpenAI(
            base_url=cfg.get("base_url", "https://api.openai.com/v1"),
            api_key=api_key,
        ), cfg.get("model", "gpt-4o-mini")


def call_llm(client, model: str, prompt: str, system: str = "") -> str:
    """Call the LLM and return the response text."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.3,
            max_tokens=4096,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"LLM call failed: {e}")
        return f"[Summary unavailable: {e}]"


# ── Deduplication ──────────────────────────────────────────────────────────────

def deduplicate(stories: list[Story]) -> list[Story]:
    """Remove stories with very similar titles (basic dedup)."""
    seen_titles = set()
    unique = []
    for s in stories:
        # Normalise title for comparison
        norm = re.sub(r"[^\w\s]", "", s.title.lower()).strip()
        # Check if we've seen something very similar (first 60 chars)
        key = norm[:60]
        if key not in seen_titles:
            seen_titles.add(key)
            unique.append(s)
    return unique


def rank_stories(stories: list[Story]) -> list[Story]:
    """Sort stories: higher score first, then by category priority."""
    category_priority = {
        "ai": 0, 
        "biology": 1, 
        "startups": 2, 
        "reddit": 3, 
        "science": 4, 
        "tech": 5, 
        "news": 6,
        "twitter": 7
    }
    return sorted(
        stories,
        key=lambda s: (-s.score, category_priority.get(s.category, 10))
    )


# ── Summarisation ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a sharp, knowledgeable tech journalist writing a daily briefing for a smart, curious reader who loves technology, AI, and science. 

Your style:
- Clear, punchy, no fluff
- Explain WHY something matters, not just what happened
- Surface the most interesting/surprising angle
- Use plain English — no unnecessary jargon
- Be concise but insightful"""


def summarise_story(story: Story, client, model: str) -> str:
    """Ask the LLM to write a 2-3 sentence summary of a single story."""
    context = story.body_text or story.summary or story.title
    prompt = f"""Summarise this tech news story in 2-3 sentences. Focus on what's new and why it matters.

Title: {story.title}
Source: {story.source}
Content: {context[:2000]}

Write only the summary, nothing else."""
    return call_llm(client, model, prompt, SYSTEM_PROMPT)


def generate_full_brief(stories: list[Story], config: dict, client, model: str) -> str:
    """
    Ask the LLM to synthesise all stories into a structured daily brief.
    This is the main output — a full Obsidian-ready Markdown note.
    """
    today = datetime.now()
    date_str = today.strftime("%A, %B %-d %Y")
    length_guide = {
        "short": "~600 words",
        "medium": "~1200 words",
        "long": "~2000 words",
    }.get(config["brief"].get("length", "medium"), "~1200 words")

    # Build a compact JSON summary of all stories for the LLM
    stories_for_prompt = []
    for s in stories:
        stories_for_prompt.append({
            "title": s.title,
            "source": s.source,
            "category": s.category,
            "url": s.url,
            "image": s.image_url or "",
            "text": (s.body_text or s.summary or "")[:800],
        })

    prompt = f"""Today is {date_str}. You are writing a daily tech and science news brief for an Obsidian note.

Here are today's stories (JSON):
{json.dumps(stories_for_prompt, indent=2, ensure_ascii=False)}

Write a complete, well-structured Obsidian Markdown note ({length_guide}) with these sections:

1. **## 🔥 Top Stories** — The 3-5 most important stories of the day, each with a 2-3 sentence summary and a link. Explain why each story matters. If an image URL is available, include it using ![](image_url) syntax right after the story title.

2. **## 🤖 AI & Machine Learning** — Key AI news, research, and product launches. 3-5 items. Include images where available.

3. **## 🧬 Biology & Life Sciences** — Breakthroughs in biology, computational biology, genetics, neuroscience, and longevity research. 2-3 items. Include images where available.

4. **## 🚀 Startups & Venture Capital** — Funding rounds, product launches, VC insights, and startup news. 2-3 items. Include images where available.

5. **## 🔬 Science & Research** — Interesting science breakthroughs or papers not covered above. 2-3 items.

6. **## 💬 Reddit Buzz** — What the tech community is talking about on Reddit. 3-4 items with brief notes on the discussion vibe.

7. **## 🐦 Tweets Worth Reading** — Notable tweets and what they signal. Include 3-5 items (skip this section if no twitter stories were provided).

8. **## 🔭 Today's Deep Dive** — Pick the single most interesting/complex story and explain it properly. 2-3 paragraphs. Include the link. If an image is available, include it at the top of this section.

9. **## 📌 Quick Hits** — Everything else in bullet form (one line each with link).

Rules:
- Format links as [Title](URL)
- Format images as ![Story title](image_url) — place them right after the story headline or at the start of a section
- Use **bold** for important terms and names
- Each section header should use the emoji shown above
- Be insightful and opinionated — say WHY something matters
- Do NOT include a title or date header — that will be added automatically
- Do NOT add any intro or outro text outside the sections"""

    return call_llm(client, model, prompt, SYSTEM_PROMPT)


# ── Obsidian Note Writer ───────────────────────────────────────────────────────

def write_obsidian_note(brief_content: str, config: dict, stories: list[Story]) -> Path:
    """Write the final Markdown note to the Obsidian vault."""
    vault_path = resolve_path(config["obsidian"]["vault_path"])
    folder = vault_path / config["obsidian"]["daily_notes_folder"]
    folder.mkdir(parents=True, exist_ok=True)

    today = datetime.now()
    date_str = today.strftime(config["obsidian"]["note_date_format"])
    weekday = today.strftime("%A")
    date_human = today.strftime("%B %-d, %Y")

    title = config["obsidian"]["note_title_format"].format(
        weekday=weekday, date=date_human
    )
    filename = f"{date_str} Tech Brief.md"
    note_path = folder / filename

    # Build frontmatter (Obsidian metadata)
    source_list = list({s.source for s in stories})[:10]
    frontmatter = f"""---
title: "{title}"
date: {today.strftime("%Y-%m-%d")}
tags:
  - daily-brief
  - tech-news
  - ai
sources: {json.dumps(source_list)}
stories_count: {len(stories)}
generated_at: {today.strftime("%H:%M")}
---

"""

    header = f"# {title}\n\n"
    full_note = frontmatter + header + brief_content

    with open(note_path, "w", encoding="utf-8") as f:
        f.write(full_note)

    logging.info(f"Note written to: {note_path}")
    return note_path


# ── Main Orchestrator ──────────────────────────────────────────────────────────

def check_ollama(config: dict) -> bool:
    """Make sure Ollama is running before we start."""
    if config["llm"].get("provider") != "ollama":
        return True
    base_url = config["llm"]["ollama"].get("base_url", "http://localhost:11434")
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def check_model_available(config: dict) -> bool:
    """Check if the configured Ollama model is pulled."""
    if config["llm"].get("provider") != "ollama":
        return True
    base_url = config["llm"]["ollama"].get("base_url", "http://localhost:11434")
    model = config["llm"]["ollama"].get("model", "qwen2.5:7b")
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=5)
        data = resp.json()
        available = [m["name"] for m in data.get("models", [])]
        # Check for exact or partial match
        return any(model in m or m in model for m in available)
    except Exception:
        return False


def already_ran_today(config: dict) -> bool:
    """Check if today's note already exists (prevents duplicate runs)."""
    vault_path = resolve_path(config["obsidian"]["vault_path"])
    folder = vault_path / config["obsidian"]["daily_notes_folder"]
    today = datetime.now()
    date_str = today.strftime(config["obsidian"]["note_date_format"])
    filename = f"{date_str} Tech Brief.md"
    return (folder / filename).exists()


def run(verbose: bool = True, force: bool = False):
    config = load_config()
    setup_logging(config, verbose)

    if verbose:
        console.print(Panel.fit(
            "[bold cyan]Daily Tech News Agent[/bold cyan]\n"
            "[dim]Fetching, summarising, and writing your Obsidian brief...[/dim]",
            border_style="cyan"
        ))

    # Guard: don't run twice on the same day
    if not force and already_ran_today(config):
        console.print("[green]✓[/green] Today's brief already exists. Use --force to regenerate.")
        return

    # Guard: make sure Ollama is running
    if config["llm"].get("provider") == "ollama":
        if not check_ollama(config):
            console.print(
                "[red]✗ Ollama is not running![/red]\n"
                "Start it with: [bold]ollama serve[/bold]\n"
                "Or install it from: https://ollama.com"
            )
            sys.exit(1)
        if not check_model_available(config):
            model = config["llm"]["ollama"].get("model", "qwen2.5:7b")
            console.print(
                f"[yellow]⚠ Model '{model}' not found.[/yellow]\n"
                f"Pull it with: [bold]ollama pull {model}[/bold]"
            )
            sys.exit(1)

    all_stories: list[Story] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:

        task = progress.add_task("Fetching Reddit...", total=None)
        reddit_stories = fetch_reddit(config)
        all_stories.extend(reddit_stories)
        progress.update(task, description=f"[green]✓[/green] Reddit: {len(reddit_stories)} posts")

        progress.update(task, description="Fetching Twitter/Nitter...")
        twitter_stories = fetch_twitter(config)
        all_stories.extend(twitter_stories)
        progress.update(task, description=f"[green]✓[/green] Twitter: {len(twitter_stories)} tweets")

        progress.update(task, description="Fetching blogs and news sites...")
        blog_stories = fetch_blogs(config)
        all_stories.extend(blog_stories)
        progress.update(task, description=f"[green]✓[/green] Blogs: {len(blog_stories)} articles")

    if verbose:
        console.print(f"\n[bold]Collected {len(all_stories)} raw items[/bold]")

    # Clean up and rank
    all_stories = deduplicate(all_stories)
    all_stories = rank_stories(all_stories)
    max_stories = config["brief"].get("max_stories", 20)
    all_stories = all_stories[:max_stories]

    if verbose:
        console.print(f"[dim]After dedup and ranking: {len(all_stories)} stories[/dim]\n")

    if not all_stories:
        console.print("[red]No stories collected. Check your internet connection.[/red]")
        sys.exit(1)

    # Build LLM client
    client, model = build_llm_client(config)

    if verbose:
        provider = config["llm"].get("provider", "ollama")
        console.print(f"[cyan]Generating brief with {provider} / {model}...[/cyan]")
        console.print("[dim](This takes 30–120 seconds depending on your hardware)[/dim]\n")

    # Generate the full brief in one LLM call
    brief_content = generate_full_brief(all_stories, config, client, model)

    # Write to Obsidian
    note_path = write_obsidian_note(brief_content, config, all_stories)

    if verbose:
        console.print(Panel.fit(
            f"[bold green]✓ Brief written![/bold green]\n\n"
            f"[dim]File:[/dim] {note_path}\n"
            f"[dim]Stories:[/dim] {len(all_stories)}\n"
            f"[dim]Sources:[/dim] Reddit, Nitter, {len(config['sources']['blogs']['feeds'])} blogs",
            border_style="green",
            title="Done"
        ))


# ── CLI Entry Point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate a daily tech news brief in your Obsidian vault."
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress progress output (useful for scheduled runs)"
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Regenerate even if today's brief already exists"
    )
    args = parser.parse_args()

    run(verbose=not args.quiet, force=args.force)
