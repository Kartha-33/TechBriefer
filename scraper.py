"""Concurrent RSS scraper. Returns a flat list of Story objects."""
from __future__ import annotations

import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser
import requests
import yaml

log = logging.getLogger(__name__)

SOURCES_PATH = Path(__file__).parent / "sources.yaml"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HTTP_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
HTTP_TIMEOUT = 10


@dataclass
class Story:
    title: str
    url: str
    source: str
    category: str
    summary: str = ""
    published_at: datetime | None = None
    body_text: str = ""
    image_url: str = ""
    base_score: float = 0.0
    cross_mention: int = 0
    weight: int = 5
    author: str = ""
    author_bonus: int = 0


def _parse_date(entry) -> datetime | None:
    # Prefer feedparser's pre-parsed struct_time when present (already UTC).
    for attr in ("published_parsed", "updated_parsed"):
        v = getattr(entry, attr, None)
        if v:
            try:
                return datetime(*v[:6])
            except Exception:
                pass
    for attr in ("published", "updated"):
        v = getattr(entry, attr, None)
        if v:
            try:
                dt = parsedate_to_datetime(v)
                if dt and dt.tzinfo:
                    dt = dt.replace(tzinfo=None)
                return dt
            except Exception:
                pass
    return None


_REDDIT_BOILERPLATE = re.compile(
    r"submitted\s+by\s+/u/[\w-]+\s*\[link\]\s*\[comments\]",
    re.IGNORECASE,
)


def _clean_summary(raw: str) -> str:
    if not raw:
        return ""
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = _REDDIT_BOILERPLATE.sub("", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:600]


def _entry_summary(entry) -> str:
    raw = ""
    for attr in ("summary", "description"):
        v = getattr(entry, attr, None)
        if v:
            raw = v
            break
    if not raw:
        c = getattr(entry, "content", None)
        if isinstance(c, list) and c:
            raw = c[0].get("value", "")
    return _clean_summary(raw)


def _entry_author(entry) -> str:
    """Best-effort author extraction across feed flavors. Atom/arXiv puts the
    primary author in `entry.author` (string). Some RSS feeds expose
    `entry.authors` (list of dicts with 'name')."""
    a = getattr(entry, "author", None)
    if isinstance(a, str) and a.strip():
        return a.strip()
    authors = getattr(entry, "authors", None)
    if isinstance(authors, list) and authors:
        first = authors[0]
        if isinstance(first, dict):
            name = first.get("name") or ""
            if name:
                return name.strip()
        elif isinstance(first, str):
            return first.strip()
    return ""


def _reddit_score(entry, min_score: int) -> bool:
    """Return True if entry passes Reddit upvote gate. Best-effort; if score is
    unavailable we let the post through (better to over-include and let the
    ranker filter than to drop everything)."""
    if not min_score:
        return True
    text = " ".join(
        [
            getattr(entry, "summary", "") or "",
            getattr(entry, "title", "") or "",
        ]
    )
    m = re.search(r"\[\s*(\d+)\s+points?\s*\]", text)
    if m:
        try:
            return int(m.group(1)) >= min_score
        except Exception:
            pass
    return True


def _fetch_feed(src: dict, articles_per_feed: int, timeout: int) -> list[Story]:
    name = src["name"]
    url = src["url"]
    category = src["category"]
    weight = int(src.get("weight", 5))
    min_score = int(src.get("min_score", 0))
    out: list[Story] = []
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code >= 400:
            log.warning("Feed HTTP %d [%s] %s", resp.status_code, name, url)
            return out
        feed = feedparser.parse(resp.content)
        entries = list(feed.entries or [])
        if not entries:
            log.warning("Feed empty [%s]: bozo=%s", name, getattr(feed, "bozo_exception", None))
            return out
        for e in entries[:articles_per_feed]:
            title = (getattr(e, "title", "") or "").strip()
            link = (getattr(e, "link", "") or "").strip()
            if not title or not link:
                continue
            if min_score and not _reddit_score(e, min_score):
                continue
            out.append(
                Story(
                    title=title,
                    url=link,
                    source=name,
                    category=category,
                    summary=_entry_summary(e),
                    published_at=_parse_date(e),
                    weight=weight,
                    author=_entry_author(e),
                )
            )
    except requests.RequestException as ex:
        log.warning("Feed network error [%s]: %s", name, ex)
    except Exception as ex:
        log.warning("Feed failed [%s]: %s", name, ex)
    return out


def load_sources() -> list[dict]:
    return yaml.safe_load(SOURCES_PATH.read_text())["feeds"]


def fetch_all(config: dict) -> list[Story]:
    sources = load_sources()
    per_feed = int(config.get("scraper", {}).get("articles_per_feed", 5))
    workers = int(config.get("scraper", {}).get("max_workers", 10))
    timeout = int(config.get("scraper", {}).get("timeout_seconds", HTTP_TIMEOUT))

    stories: list[Story] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_fetch_feed, s, per_feed, timeout): s for s in sources}
        for fut in as_completed(futures):
            try:
                stories.extend(fut.result())
            except Exception as e:
                log.warning("Worker failed [%s]: %s", futures[fut].get("name", "?"), e)

    seen: set[str] = set()
    deduped: list[Story] = []
    for s in stories:
        if s.url in seen:
            continue
        seen.add(s.url)
        deduped.append(s)
    log.info("Scraped %d unique stories from %d feeds", len(deduped), len(sources))
    return deduped
