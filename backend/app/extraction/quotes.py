"""Deterministic evidence quotes for extracted attributes (Step 8B).

A quote is an EXACT short excerpt of the retrieved evidence text that
supports an extracted value. Quotes are never invented: resolution either
finds a verbatim (whitespace-tolerant) occurrence of the value in the
evidence text and returns the surrounding window, or reports that no quote
could be resolved (empty string).

Resolution order uses the most meaningful value first (normalized, then
raw) and always takes the earliest occurrence so results are stable.
"""

from __future__ import annotations

import re

# Hard caps so quotes stay short in the UI.
MAX_PREFIX_CHARS = 70
MAX_SUFFIX_CHARS = 90
MAX_QUOTE_CHARS = 200

_WHITESPACE_RE = re.compile(r"\s+")


def _collapse(text: str) -> str:
    """Replace every run of whitespace with a single space (display only)."""
    return _WHITESPACE_RE.sub(" ", text).strip()


def _find_value_text(text: str, value: str) -> int | None:
    """Earliest case-insensitive position of `value` in `text`.

    Tolerates whitespace differences (the extracted value may come from a
    page where words are separated by newlines). Positions refer to the
    collapsed text, which is exactly what the caller slices and displays,
    so indexes stay consistent. Returns None when the value does not appear.
    """
    if not text or not value:
        return None
    collapsed = _collapse(text)
    start = collapsed.lower().find(_collapse(value).lower())
    if start < 0:
        return None
    return start


def resolve_quote(text: str, *values: str) -> str:
    """Short, exact evidence quote containing the first matching value.

    Returns "" when no value can be located in the text (the caller then
    shows "Evidence quote unavailable"). Never invents content.
    """
    for value in values:
        if not (value or "").strip():
            continue
        start = _find_value_text(text, value)
        if start is None:
            continue
        collapsed = _collapse(text)
        window_start = max(0, start - MAX_PREFIX_CHARS)
        window_end = min(len(collapsed), start + len(_collapse(value)) + MAX_SUFFIX_CHARS)
        quote = collapsed[window_start:window_end]
        if len(quote) > MAX_QUOTE_CHARS:
            quote = quote[:MAX_QUOTE_CHARS].rstrip()
            if quote:
                quote = quote + "…"
        if window_start > 0:
            quote = "…" + quote
        return quote.strip()
    return ""