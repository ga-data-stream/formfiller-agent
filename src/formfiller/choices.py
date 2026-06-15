"""Deterministic matching of a free-text value to a fixed set of choice options.

Used both at fill time (which radio to click) and at mapping time (snapping the
LLM's descriptive value to an exact option before the confidence gate). Kept
dependency-free so the field mapper can use it without importing Playwright.
"""
from __future__ import annotations

import re
from typing import Optional, Sequence


def normalize_choice(text: str) -> str:
    """Collapse a choice label/value to comparable form: lowercase, alphanumerics
    only. So 'SIREN + SIRET', 'SIREN_SIRET' and 'siren siret' all match, while
    'SIREN' stays distinct from 'SIREN_SIRET' ('siren' != 'sirensiret')."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def match_choice(labels: Sequence[str], value: str) -> Optional[int]:
    """Index of the choice whose label matches `value`, or None. Exact match
    first (across all labels), then a normalized match. Never substring-matches,
    so a short value can't be swallowed by a longer option."""
    for i, label in enumerate(labels):
        if label.strip() == value.strip():
            return i
    target = normalize_choice(value)
    if not target:
        return None
    for i, label in enumerate(labels):
        if normalize_choice(label) == target:
            return i
    return None
