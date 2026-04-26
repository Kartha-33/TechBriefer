"""Build the Obsidian Markdown brief and write it to the vault.

Python owns every character of structure. The LLM only contributes the
2-sentence prose inside `summaries[url]` -- never URLs, titles, sections,
images, dates, or tags."""
from __future__ import annotations

import html as _html
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from entities import extract_entities

CATEGORY_HEADERS = {
    "ai":       "## AI & Machine Learning",
    "biology":  "## Biology & Life Sciences",
    "startups": "## Startups & Venture Capital",
}

PAPER_DOMAINS = ("arxiv.org", "biorxiv.org", "medrxiv.org", "ssrn.com")


def _is_paper(s) -> bool:
    url = (getattr(s, "url", "") or "").lower()
    return any(d in url for d in PAPER_DOMAINS)


def _clean(text: str, n: int = 120) -> str:
    text = _html.unescape(text or "")
    text = re.sub(r"submitted\s+by\s+/u/[\w-]+\s*\[link\]\s*\[comments\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" -—–|.,")
    return text[:n]


def _story_entities(s) -> list[str]:
    return extract_entities((s.title or "") + " " + (s.summary or ""))


def _byline(s) -> str:
    """`*Source: TechCrunch -- by Maxwell Zeff* (★)` when an author bonus fired,
    `*Source: TechCrunch -- by Maxwell Zeff*` when we have an author but no
    bonus, plain `*Source: TechCrunch*` otherwise."""
    author = (getattr(s, "author", "") or "").strip()
    bonus = int(getattr(s, "author_bonus", 0) or 0)
    if author:
        line = f"*Source: {s.source} -- by {author}*"
        if bonus > 0:
            line += "  (signal-boosted author)"
        return line
    return f"*Source: {s.source}*"


def render_story(s, summaries: dict, show_image: bool = True) -> str:
    lines = [f"### [{s.title}]({s.url})"]
    if show_image and s.image_url and s.image_url.startswith("https://"):
        lines.append(f"![]({s.image_url})")
    score, summary = summaries.get(s.url, (0, ""))
    if not summary:
        summary = _clean(s.summary or s.title or "", 240)
    if summary:
        lines.append(summary)
    tags = _story_entities(s)
    if tags:
        lines.append("*" + " ".join(f"[[{t}]]" for t in tags) + "*")
    lines.append(_byline(s))
    return "\n\n".join(lines)


def _render_paper_pin(s, summaries: dict) -> str:
    """Featured-paper rendering: same content as `render_story` but with a
    'Featured paper' callout at the top so it visually anchors the section."""
    lines = ["> **Featured paper** -- highest-scored preprint in this category today."]
    lines.append(f"### [{s.title}]({s.url})")
    score, summary = summaries.get(s.url, (0, ""))
    if not summary:
        summary = _clean(s.summary or s.title or "", 320)
    if summary:
        lines.append(summary)
    tags = _story_entities(s)
    if tags:
        lines.append("*" + " ".join(f"[[{t}]]" for t in tags) + "*")
    lines.append(_byline(s))
    return "\n\n".join(lines)


def _top_paper_for(category: str, deep: list, quick_hits: list, paper_pool: list, used: set) -> object | None:
    """Highest-scored paper-domain story for `category` not already used.

    Searches the broader `paper_pool` (full ranked list) so a Friday arXiv
    paper isn't crushed by the prefilter even though Sunday's blog posts
    out-rank it on recency. Falls back to deep+quick_hits if `paper_pool`
    is empty."""
    pool = paper_pool if paper_pool else (deep + quick_hits)
    candidates = [
        s for s in pool
        if s.category == category and _is_paper(s) and s.url not in used
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda s: getattr(s, "base_score", 0.0), reverse=True)
    return candidates[0]


def _group_quick_hits(stories: list, max_groups: int = 6, min_size: int = 2) -> tuple[list[tuple[str, list]], list]:
    """Cluster quick-hit stories by shared entity.

    Returns ``(groups, ungrouped)`` where ``groups`` is an ordered list of
    ``(entity, stories)`` tuples (most popular first, capped at ``max_groups``)
    and ``ungrouped`` is everything that didn't slot into a group.

    Each story is placed into at most one group (its most-popular entity)."""
    if not stories:
        return [], []
    per_story = {s.url: _story_entities(s) for s in stories}
    counts: Counter[str] = Counter()
    for ents in per_story.values():
        for e in ents:
            counts[e] += 1
    shared = [e for e, c in counts.most_common() if c >= min_size][:max_groups]
    groups: list[tuple[str, list]] = []
    used_urls: set[str] = set()
    for e in shared:
        bucket = []
        for s in stories:
            if s.url in used_urls:
                continue
            if e in per_story[s.url]:
                bucket.append(s)
                used_urls.add(s.url)
        if len(bucket) >= min_size:
            groups.append((e, bucket))
    ungrouped = [s for s in stories if s.url not in used_urls]
    return groups, ungrouped


def _render_quick_hit_line(s) -> str:
    blurb = _clean(s.summary or "", 120)
    src = s.source
    if int(getattr(s, "author_bonus", 0) or 0) > 0 and (s.author or "").strip():
        src = f"{s.source} - {s.author}"
    tail = f"*({src})*"
    if blurb:
        return f"- **[{s.title}]({s.url})** -- {blurb} {tail}"
    return f"- **[{s.title}]({s.url})** {tail}"


def build_note(
    deep: list,
    summaries: dict,
    quick_hits: list,
    config: dict,
    total_sources: int,
    continuing_entities: list[str] | None = None,
    paper_pool: list | None = None,
) -> str:
    """Render the brief.

    `deep` -- top-of-pile stories with LLM summaries.
    `quick_hits` -- the rest of the prefilter pool. We draw on these to fill
                    section quotas the deep pool can't satisfy and to populate
                    the Quick Hits roundup at the bottom (RSS-only).
    `continuing_entities` -- entities that appeared in yesterday's brief and
                    again in today's top stories. Rendered as a one-line
                    "Continuing from yesterday" block at the top.
    `paper_pool` -- the full ranked list (pre-prefilter) used to find the
                    Featured Paper for AI and Biology, so a Friday arXiv paper
                    can be pinned even on a Sunday when fresher blog posts
                    out-score it."""
    now = datetime.now()
    used: set[str] = set()
    quotas = config.get("quotas", {})

    ai_paper = _top_paper_for("ai", deep, quick_hits, paper_pool or [], used)
    if ai_paper:
        used.add(ai_paper.url)
    bio_paper = _top_paper_for("biology", deep, quick_hits, paper_pool or [], used)
    if bio_paper:
        used.add(bio_paper.url)

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

    ai_section = take("ai", quotas.get("ai", 5))
    bio_section = take("biology", quotas.get("biology", 4))
    vc_section = take("startups", quotas.get("startups", 4))

    qh = [s for s in quick_hits if s.url not in used][: config.get("pipeline", {}).get("quick_hits", 30)]

    parts: list[str] = []
    parts.append(
        f"> *{now.strftime('%A, %B %-d, %Y')} -- "
        f"{len(deep) + len(qh)} stories from {total_sources} sources*\n"
    )

    if continuing_entities:
        chips = " · ".join(f"[[{e}]]" for e in continuing_entities[:8])
        parts.append(f"**Continuing from yesterday:** {chips}\n")

    parts.append("---\n")

    parts.append("## Top Stories\n")
    for s in top:
        parts.append(render_story(s, summaries, show_image=True))
        parts.append("\n---\n")

    if ai_paper or ai_section:
        parts.append(CATEGORY_HEADERS["ai"] + "\n")
        if ai_paper:
            parts.append(_render_paper_pin(ai_paper, summaries))
            parts.append("\n")
        for s in ai_section:
            parts.append(render_story(s, summaries, show_image=False))
            parts.append("")
        parts.append("\n---\n")

    if bio_paper or bio_section:
        parts.append(CATEGORY_HEADERS["biology"] + "\n")
        if bio_paper:
            parts.append(_render_paper_pin(bio_paper, summaries))
            parts.append("\n")
        if bio_section:
            for s in bio_section:
                parts.append(render_story(s, summaries, show_image=False))
                parts.append("")
        elif not bio_paper:
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
        groups, ungrouped = _group_quick_hits(qh)
        for entity, bucket in groups:
            parts.append(f"### {entity} ({len(bucket)})\n")
            for s in bucket:
                parts.append(_render_quick_hit_line(s))
            parts.append("")
        if ungrouped:
            if groups:
                parts.append("### Other\n")
            for s in ungrouped:
                parts.append(_render_quick_hit_line(s))
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
