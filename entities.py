"""Regex entity extraction for Obsidian [[wikilinks]]. Pure Python, no LLM."""
from __future__ import annotations

import re

PEOPLE = [
    "Sam Altman", "Ilya Sutskever", "Andrej Karpathy", "Yann LeCun",
    "Geoffrey Hinton", "Yoshua Bengio", "Andrew Ng", "Fei-Fei Li",
    "Demis Hassabis", "Dario Amodei", "Mustafa Suleyman", "François Chollet",
    "Chip Huyen", "Lilian Weng", "Sebastian Ruder",
    "Paul Graham", "Marc Andreessen", "Naval Ravikant", "Elad Gil",
    "Josh Wolfe", "David Baker", "Jennifer Doudna", "George Church",
    "Eric Topol", "Patrick Collison", "Noubar Afeyan", "Jensen Huang",
]

ORGS = [
    "OpenAI", "Anthropic", "DeepMind", "Google", "Meta", "Microsoft",
    "NVIDIA", "Apple", "Amazon", "Tesla", "Hugging Face",
    "Y Combinator", "Sequoia", "a16z", "Andreessen Horowitz",
    "Ginkgo Bioworks", "Moderna", "BioNTech", "Flagship Pioneering",
    "Broad Institute", "MIT", "Stanford", "Harvard", "Berkeley",
    "Allen Institute", "Recursion", "Insitro", "Mistral",
]

TOPICS = [
    "GPT-4", "GPT-5", "Claude", "Gemini", "Llama", "Mistral",
    "CRISPR", "AlphaFold", "Transformer", "RLHF", "RAG",
    "Diffusion Model", "Reinforcement Learning", "LLM",
    "AI Safety", "AI Alignment", "Multimodal", "Agentic AI",
    "Synthetic Biology", "mRNA", "CAR-T", "Genomics",
    "Drug Discovery", "Computer Vision",
]

_ALL = PEOPLE + ORGS + TOPICS


def extract_entities(text: str, max_entities: int = 6) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    for entity in _ALL:
        if entity in found:
            continue
        if re.search(rf"\b{re.escape(entity)}\b", text, re.IGNORECASE):
            found.append(entity)
            if len(found) >= max_entities:
                break
    return found
