"""Pure-heuristic ranker. No LLM call here.

Score = source_weight / (hours_old + 2)^1.8 + cross_mention * 3

The cross_mention bonus surfaces stories covered by multiple feeds (e.g.,
arXiv + Hugging Face write-up of the same paper)."""
from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher

CLUSTER_THRESHOLD = 0.78
DEFAULT_WEIGHT = 5


def _normalize(title: str) -> str:
    return re.sub(r"[^\w\s]", "", title.lower()).strip()


def _score(story, weights: dict) -> float:
    """source_weight raised to 1.3 so high-trust feeds dominate, with a softer
    time decay (exponent 1.4) so a 1-day-old top journal can still beat a
    fresh low-trust blog. Cross-feed mentions add a flat bonus."""
    w = weights.get(story.source, story.weight or DEFAULT_WEIGHT)
    if story.published_at:
        hours = max(0.0, (datetime.now() - story.published_at).total_seconds() / 3600.0)
    else:
        hours = 48.0
    return (w ** 1.3) / ((hours + 2.0) ** 1.4) + story.cross_mention * 3.0


def _weight_of(story, weights: dict) -> int:
    return int(weights.get(story.source, story.weight or DEFAULT_WEIGHT))


def cluster_and_score(stories: list, weights: dict) -> list:
    """O(N^2) title clustering at SequenceMatcher >= 0.78.

    Within each cluster the **highest-weighted** source represents it; lower-
    weighted near-duplicates (e.g., a TechCrunch recap of a DeepMind blog post)
    are dropped from the output so the brief always points at the primary
    source. The representative still keeps a ``cross_mention`` bonus equal to
    the number of dropped peers, so cross-feed coverage continues to surface
    in the score."""
    norms = [_normalize(s.title) for s in stories]
    used: set[int] = set()
    out = []
    for i in range(len(stories)):
        if i in used:
            continue
        cluster = [i]
        for j in range(i + 1, len(stories)):
            if j in used:
                continue
            if SequenceMatcher(None, norms[i], norms[j]).ratio() >= CLUSTER_THRESHOLD:
                cluster.append(j)
                used.add(j)
        rep_idx = max(cluster, key=lambda k: _weight_of(stories[k], weights))
        rep = stories[rep_idx]
        rep.cross_mention = len(cluster) - 1
        rep.base_score = _score(rep, weights)
        out.append(rep)
    out.sort(key=lambda s: s.base_score, reverse=True)
    return out


def apply_quotas(stories: list, quotas: dict) -> list:
    """Pull the first `quotas[cat]` items per category off the top of the
    scored list, then append everything else in score order. Guarantees each
    section has its minimum coverage even if a few categories are score-heavy."""
    chosen = []
    counts: dict[str, int] = {}
    rest = []
    for s in stories:
        if counts.get(s.category, 0) < quotas.get(s.category, 0):
            chosen.append(s)
            counts[s.category] = counts.get(s.category, 0) + 1
        else:
            rest.append(s)
    chosen.extend(rest)
    return chosen
