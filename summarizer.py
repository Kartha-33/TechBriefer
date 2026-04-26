"""Single grounded LLM call. Anti-hallucination by design:

- The LLM never sees URLs. It only sees `[i=N] [Source] Title\\nGrounding text`.
- It returns JSON keyed by integer index. Python maps `i` back to the story
  it sent in, so the LLM cannot invent a URL or a story.
- A validator rejects empty / overly long / hedging / refusal summaries and
  substitutes the raw RSS summary as a safe fallback.
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a science and technology news editor writing for a reader in AI, "
    "biotech, and startups. You receive a numbered list of stories. For each "
    "story you must write a summary using ONLY the grounding text given for "
    "that exact story id. STRICT RULES: "
    "(1) Use only information present in the grounding text for that story. "
    "(2) Never invent facts, statistics, dates, names, products, or quotes. "
    "(3) Never reference your training data or a knowledge cutoff. "
    "(4) Never hedge ('I think', 'may be', refusals). "
    "(5) If the grounding text is too thin to summarize, return the literal "
    "string 'INSUFFICIENT' as the summary for that story. "
    "(6) Output strict JSON only, no prose, no markdown, no commentary."
)

USER_TEMPLATE = (
    "Write exactly 2 sentences per story:\n"
    "  - Sentence 1: what the source reports concretely (use only the text below).\n"
    "  - Sentence 2: why it matters to a reader in AI, biotech, or startups.\n"
    "Assign a relevance score 1-10 (10 = major breakthrough, launch, or finding; "
    "1 = trivial / community chatter).\n\n"
    "Return JSON with this exact shape, one item per story id:\n"
    '{{"items": [{{"i": <int>, "score": <int>, "summary": "<two sentences>"}}, ...]}}\n\n'
    "STORIES:\n{stories}\n"
)

BAD_PHRASES = re.compile(
    r"\b(I think|I believe|I cannot|I'm not|I am not|knowledge cutoff|"
    r"as an AI|as a language model|sorry,? I|may be|might be)\b",
    re.IGNORECASE,
)
LOW_SIGNAL = re.compile(
    r"submitted\s+by|/u/[\w-]+|\[link\]|\[comments\]",
    re.IGNORECASE,
)
MAX_BATCH = 6  # smaller batches give the 3B model fewer chances to mix up indices


def _grounding_text(story) -> str:
    text = (story.body_text or story.summary or story.title or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:600]


def _is_valid(s: str) -> bool:
    if not s:
        return False
    s = s.strip()
    if s.upper() == "INSUFFICIENT":
        return False
    if len(s) < 40 or len(s) > 320:
        return False
    if BAD_PHRASES.search(s):
        return False
    if s.count(".") < 1:
        return False
    return True


def _fallback(story) -> str:
    raw = (story.summary or "").strip()
    raw = LOW_SIGNAL.sub("", raw).strip(" -—–|.,")
    if len(raw) < 40:
        raw = (story.title or "").strip()
    return raw[:240]


def _build_prompt(stories: list) -> str:
    lines = []
    for i, s in enumerate(stories, 1):
        lines.append(
            f"=== STORY {i} ===\n"
            f"id: {i}\n"
            f"source: {s.source}\n"
            f"title: {s.title}\n"
            f"grounding_text:\n{_grounding_text(s)}"
        )
    return USER_TEMPLATE.format(stories="\n\n".join(lines))


def _summarize_batch(stories: list, client, model: str) -> dict[int, dict]:
    """One LLM round-trip for up to MAX_BATCH stories. Returns items keyed by
    1-based story index in this batch."""
    prompt = _build_prompt(stories)
    items: dict[int, dict] = {}
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )
        content = (resp.choices[0].message.content or "").strip()
        data = json.loads(content)
        for item in data.get("items", []):
            try:
                idx = int(item.get("i"))
                if 1 <= idx <= len(stories):
                    items[idx] = item
            except Exception:
                continue
    except Exception as e:
        log.warning("LLM batch failed: %s", e)
    return items


def _coerce_summary(value) -> str:
    """LLMs occasionally return ``summary`` as a list of sentences or a dict.
    Coerce anything the model gives us into a plain string so the validator
    can do its job."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return " ".join(_coerce_summary(v) for v in value).strip()
    if isinstance(value, dict):
        return " ".join(_coerce_summary(v) for v in value.values()).strip()
    return str(value).strip()


def summarize(stories: list, client, model: str) -> dict:
    """Returns {url: (score:int, summary:str)} for every input story.

    Stories are processed in batches of MAX_BATCH so the small local model
    keeps every story's grounding context separated. Batches run concurrently
    against Ollama, which serves them from the already-loaded model."""
    if not stories:
        return {}

    batches = [stories[i : i + MAX_BATCH] for i in range(0, len(stories), MAX_BATCH)]
    results: list[dict[int, dict]] = [None] * len(batches)  # type: ignore

    def _run(idx: int) -> None:
        results[idx] = _summarize_batch(batches[idx], client, model)

    with ThreadPoolExecutor(max_workers=min(len(batches), 3)) as ex:
        list(ex.map(_run, range(len(batches))))

    out: dict = {}
    for batch_idx, batch in enumerate(batches):
        items = results[batch_idx] or {}
        for i, s in enumerate(batch, 1):
            item = items.get(i, {})
            summary_raw = _coerce_summary(item.get("summary"))
            try:
                score = int(item.get("score", 5))
            except Exception:
                score = 5
            if not _is_valid(summary_raw):
                summary_raw = _fallback(s)
                score = min(score, 5)
            out[s.url] = (score, summary_raw)
    return out
