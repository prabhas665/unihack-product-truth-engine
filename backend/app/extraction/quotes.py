"""Deterministic evidence quotes for extracted attributes (Step 8B).

A quote is an EXACT short excerpt of the retrieved evidence text that
supports an extracted value. Quotes are never invented: resolution either
finds a verbatim (whitespace-tolerant) occurrence of the value in the
evidence text and returns the surrounding window, or reports that no quote
could be resolved (empty string).

Resolution order uses the most meaningful value first (normalized, then
raw) and always takes the earliest occurrence so results are stable.

Claim support (P0 gate): a claim is only supported when the value occurs in
copy that belongs to the requested product. An occurrence is treated as the
requested product's own passage when it lies within
CLAIM_MPN_WINDOW_CHARS of an occurrence of the requested MPN. Otherwise the
occurrence must lie outside any other product's passage (no foreign
product-code token within the window); values that occur ONLY near other
products' codes are unsupported, so sibling-product contamination is never
turned into an accepted claim or a quote.
"""

from __future__ import annotations

import re

# Hard caps so quotes stay short in the UI.
MAX_PREFIX_CHARS = 70
MAX_SUFFIX_CHARS = 90
MAX_QUOTE_CHARS = 200

# A value occurrence within this many characters of a product-code token is
# considered to belong to that product's passage. Large enough to cover a
# full product listing sentence, small enough to never bridge into an
# adjacent listing on a category page.
CLAIM_MPN_WINDOW_CHARS = 100

_WHITESPACE_RE = re.compile(r"\s+")

# Product-code tokens (uppercased text): letters and/or digits with optional
# hyphen-separated groups. Candidate tokens must be >= 5 characters AND
# contain at least one letter AND one digit. The length floor keeps generic
# specification tokens out ("18V", "IP65", "18GA", "120V", "LXT", "2026",
# "CFM", plain words) while real product codes are almost always longer
# ("XLC10ZW", "GLC04Z", "ACME-1000", "DCB518ASTS06G").
_PRODUCT_TOKEN_RE = re.compile(r"[A-Z0-9]+(?:-[A-Z0-9]+)*")


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


def _find_all(text: str, needle: str) -> list[int]:
    """Every non-overlapping position of `needle` in `text` (ascending)."""
    positions: list[int] = []
    start = 0
    while True:
        pos = text.find(needle, start)
        if pos < 0:
            return positions
        positions.append(pos)
        start = pos + max(1, len(needle))


def _foreign_token_positions(text: str, mpn: str) -> list[int]:
    """Positions of product-code tokens that are NOT the requested MPN.

    Tokens that are pure digits, pure words, shorter than 5 characters, or
    spec-like ("18V", "IP65", "18GA", "120V", "LXT", "2026", "CFM",
    "MAKITA") are not product codes and do not mark a passage as belonging
    to another product.
    """
    mpn_norm = _collapse(mpn).upper() if mpn else ""
    positions: list[int] = []
    for match in _PRODUCT_TOKEN_RE.finditer(text):
        token = match.group(0)
        if len(token) < 5:
            continue
        if not any(ch.isdigit() for ch in token) or not any(
            ch.isalpha() for ch in token
        ):
            continue
        if mpn_norm and token == mpn_norm:
            continue
        positions.append(match.start())
    return positions


def _nearest_owner(
    pos: int,
    requested_positions: list[int],
    foreign_positions: list[int],
    window_chars: int,
) -> str:
    """Which passage an occurrence at `pos` belongs to: requested/generic.

    The nearest product-code token inside the window decides: the requested
    MPN wins ties (the product's own passage takes precedence); an
    occurrence with NO product-code token within the window is generic
    family copy and counts as supported copy.
    """
    best_requested = min(
        (abs(pos - p) for p in requested_positions), default=None
    )
    best_foreign = min(
        (abs(pos - p) for p in foreign_positions), default=None
    )
    if best_requested is not None and best_requested <= window_chars:
        if best_foreign is None or best_requested <= best_foreign:
            return "requested"
    if best_foreign is not None and best_foreign <= window_chars:
        return "foreign"
    return "generic"


def _quote_window(collapsed: str, start: int, value_len: int) -> str:
    """Surrounding window of an occurrence, formatted for display."""
    window_start = max(0, start - MAX_PREFIX_CHARS)
    window_end = min(
        len(collapsed), start + value_len + MAX_SUFFIX_CHARS
    )
    quote = collapsed[window_start:window_end]
    if len(quote) > MAX_QUOTE_CHARS:
        quote = quote[:MAX_QUOTE_CHARS].rstrip()
        if quote:
            quote = quote + "…"
    if window_start > 0:
        quote = "…" + quote
    return quote.strip()


def find_supported_quote(
    text: str,
    values: tuple[str, ...] | list[str],
    mpn: str,
    window_chars: int = CLAIM_MPN_WINDOW_CHARS,
) -> tuple[str, bool]:
    """Resolve a quote for one of `values` that is SUPPORTED for the product.

    Returns (quote, anchored):
    - anchored=True when the value occurrence lies within `window_chars` of
      the requested `mpn` (the product's own passage) - always preferred;
    - anchored=False when no such occurrence exists but the value does occur
      in copy not attributable to any other product (no foreign product-code
      token within the window);
    - ("", False) when the value is absent or every occurrence belongs to
      another product's passage (claim not found in cited evidence).

    `values` are tried in order (normalized first, then raw) so the most
    meaningful value wins, mirroring resolve_quote. All occurrences are
    searched, not just the earliest, so a late occurrence in the product's
    own passage still supports the claim.
    """
    if not text or not values:
        return "", False
    collapsed = _collapse(text)
    if not collapsed:
        return "", False
    upper = collapsed.upper()
    requested_positions = (
        _find_all(upper, _collapse(mpn).upper()) if mpn else []
    )
    foreign_positions = _foreign_token_positions(upper, mpn)
    best_generic: tuple[int, int] | None = None
    for value in values:
        needle = _collapse(value).upper()
        if not needle:
            continue
        for start in _find_all(upper, needle):
            owner = _nearest_owner(
                start, requested_positions, foreign_positions, window_chars
            )
            if owner == "requested":
                return _quote_window(collapsed, start, len(needle)), True
            if owner == "generic" and best_generic is None:
                best_generic = (start, len(needle))
    if best_generic is None:
        return "", False
    start, value_len = best_generic
    return _quote_window(collapsed, start, value_len), False


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
        return _quote_window(_collapse(text), start, len(_collapse(value)))
    return ""