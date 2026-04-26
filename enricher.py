"""Concurrent enricher. For each story we try to fetch:
  - og:image / twitter:image (only if URL starts with https://)
  - article body text (only when the RSS summary is too thin)

We never block the pipeline on a slow source: any failure is logged and the
story passes through with whatever data it already had."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

THIN_SUMMARY_CHARS = 250
MAX_BODY_CHARS = 2000


def _fetch_image(soup: BeautifulSoup) -> str:
    for prop in ("og:image", "twitter:image"):
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if tag:
            content = (tag.get("content") or "").strip()
            if content.startswith("https://"):
                return content
    return ""


def _fetch_text(soup: BeautifulSoup) -> str:
    for t in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        t.decompose()
    container = soup.find("article") or soup.find("main") or soup.find("body")
    if not container:
        return ""
    text = container.get_text(" ", strip=True)
    text = " ".join(text.split())
    return text[:MAX_BODY_CHARS]


def _enrich_one(story, timeout: int) -> None:
    need_text = len(story.summary or "") < THIN_SUMMARY_CHARS
    try:
        r = requests.get(story.url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            return
        soup = BeautifulSoup(r.text, "html.parser")
        if not story.image_url:
            story.image_url = _fetch_image(soup)
        if need_text and not story.body_text:
            story.body_text = _fetch_text(soup)
    except Exception as e:
        log.debug("enrich failed [%s]: %s", story.url, e)


def enrich_all(stories: list, config: dict) -> list:
    timeout = int(config.get("scraper", {}).get("timeout_seconds", 10))
    workers = int(config.get("scraper", {}).get("max_workers", 10))
    if not stories:
        return stories
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_enrich_one, s, timeout) for s in stories]
        for _ in as_completed(futures):
            pass
    return stories
