"""Build the Obsidian Markdown brief and write it to the vault.

Python owns every character of structure. The LLM only contributes the
2-sentence prose inside `summaries[url]` -- never URLs, titles, sections,
images, dates, or tags."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from entities import extract_entities

CATEGORY_HEADERS = {
    "ai":       "## AI & Machine Learning",
    "biology":  "## Biology & Life Sciences",
    "startups": "## Startups & Venture Capital",
}


import html as _html


def _clean(text: str, n: int = 120) -> str:
    text = _html.unescape(text or "")
    text = re.sub(r"submitted\s+by\s+/u/[\w-]+\s*\[link\]\s*\[comments\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" -—–|.,")
    return text[:n]


def render_story(s, summaries: dict, show_image: bool = True) -> str:
    lines = [f"### [{s.title}]({s.url})"]
    if show_image and s.image_url and s.image_url.startswith("https://"):
        lines.append(f"![]({s.image_url})")
    score, summary = summaries.get(s.url, (0, ""))
    if not summary:
        summary = _clean(s.summary or s.title or "", 240)
    if summary:
        lines.append(summary)
    tags = extract_entities(s.title + " " + (s.summary or ""))
    if tags:
        lines.append("*" + " ".join(f"[[{t}]]" for t in tags) + "*")
    lines.append(f"*Source: {s.source}*")
    return "\n\n".join(lines)


def build_note(
    deep: list,
    summaries: dict,
    quick_hits: list,
    config: dict,
    total_sources: int,
) -> str:
    """Render the brief.

    `deep` -- top-of-pile stories with LLM summaries.
    `quick_hits` -- the rest of the prefilter pool. We draw on these to fill
                    section quotas the deep pool can't satisfy and to populate
                    the Quick Hits roundup at the bottom (RSS-only)."""
    now = datetime.now()
    used: set[str] = set()

    def take(category: str, n: int) -> list:
        out = []
        for s in deep:
            if s.url in used:
                continue
            if s.category == category and len(out) < n:
                out.append(s)
                used.add(s.url)
        if len(out) < n:
            for s in quick_hits:
                if s.url in used:
                    continue
                if s.category == category and len(out) < n:
                    out.append(s)
                    used.add(s.url)
        return out

    top: list = []
    for s in deep[:5]:
        if s.url not in used:
            top.append(s)
            used.add(s.url)
    ai_section = take("ai", config.get("quotas", {}).get("ai", 5))
    bio_section = take("biology", config.get("quotas", {}).get("biology", 4))
    vc_section = take("startups", config.get("quotas", {}).get("startups", 4))

    qh = [s for s in quick_hits if s.url not in used][: config.get("pipeline", {}).get("quick_hits", 30)]

    parts: list[str] = []
    parts.append(
        f"> *{now.strftime('%A, %B %-d, %Y')} -- "
        f"{len(deep) + len(qh)} stories from {total_sources} sources*\n"
    )
    parts.append("---\n")

    parts.append("## Top Stories\n")
    for s in top:
        parts.append(render_story(s, summaries, show_image=True))
        parts.append("\n---\n")

    if ai_section:
        parts.append(CATEGORY_HEADERS["ai"] + "\n")
        for s in ai_section:
            parts.append(render_story(s, summaries, show_image=False))
            parts.append("")
        parts.append("\n---\n")

    parts.append(CATEGORY_HEADERS["biology"] + "\n")
    if bio_section:
        for s in bio_section:
            parts.append(render_story(s, summaries, show_image=False))
            parts.append("")
    else:
        parts.append("> *No biology stories cleared the quota threshold today.*\n")
    parts.append("\n---\n")

    if vc_section:
        parts.append(CATEGORY_HEADERS["startups"] + "\n")
        for s in vc_section:
            parts.append(render_story(s, summaries, show_image=False))
            parts.append("")
        parts.append("\n---\n")

    if qh:
        parts.append("## Quick Hits\n")
        for s in qh:
            blurb = _clean(s.summary or "", 120)
            line = f"- **[{s.title}]({s.url})** — {blurb}" if blurb else f"- **[{s.title}]({s.url})**"
            parts.append(line)
        parts.append("")

    return "\n".join(parts)


def write_obsidian_note(content: str, config: dict, all_stories: list) -> Path:
    vault = Path(config["obsidian"]["vault_path"]).expanduser()
    folder = vault / config["obsidian"]["daily_notes_folder"]
    folder.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    filename = f"{now.strftime('%Y-%m-%d')} Tech Brief.md"
    path = folder / filename

    sources_list = sorted({s.source for s in all_stories})[:12]
    frontmatter = (
        "---\n"
        f"title: \"Second Brain Brief — {now.strftime('%A, %B %-d, %Y')}\"\n"
        f"date: {now.strftime('%Y-%m-%d')}\n"
        "tags:\n  - daily-brief\n  - second-brain\n"
        f"sources: {json.dumps(sources_list)}\n"
        f"stories_count: {len(all_stories)}\n"
        f"generated_at: {now.strftime('%H:%M')}\n"
        "---\n\n"
        f"# Second Brain Brief — {now.strftime('%A, %B %-d, %Y')}\n\n"
    )
    path.write_text(frontmatter + content, encoding="utf-8")
    return path
