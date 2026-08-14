"""Deterministic grounding guard for generated descriptions (Step 9B).

The LLM prompt already forbids inventing claims; this guard is the
deterministic second line of defense. It only ever *drops* text - it never
rewrites or fabricates anything.

How it works (conservative by design):

- A "grounded vocabulary" is built per product from the ONLY facts the
  pipeline possesses: product identity fields (manufacturer, brand, mpn,
  raw description, sku), the validated attribute names/values/units, and the
  evidence quotes shown to the model. Category trigger words (certification,
  warranty, dimensions, materials, performance, compatibility, accessories)
  are looked for in every generated description field.
- A claim is "unsupported" only when a trigger fires AND the claim terms
  captured alongside it (e.g. "24 inches" in "Measures 24 inches long", or
  "warranty" in "2-year warranty") are NOT part of the grounded vocabulary.
  Natural derivations like "Cordless vacuum" or "18 V cordless vacuum" are
  grounded in the attributes and pass untouched.
- Any field containing an unsupported claim is blanked (or the offending
  item_features entry is dropped) and a clear review reason is added; every
  other field stays exactly as generated.

This is a heuristic, not proof: it only catches claims phrased with the
category trigger words. It is intentionally strict when it does fire.
"""

from __future__ import annotations

import re

from app.core.domain import AttributeValue, Descriptions, ProductIdentity

# Claim categories with trigger patterns. Every pattern exposes the claim
# terms to verify through the named group ``claim``; when that group is
# missing, the whole match (the trigger word itself, e.g. "certified") is
# the claim term.
_CATEGORY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "certification",
        re.compile(
            r"(?P<claim>\b(?:certif[a-z]*|compliant[a-z]*|RoHS\b|"
            r"UL(?:/c)?\b|CE\b|ISO\s*\d{0,4}|ANSI\b|OSHA\b|"
            r"FDA(?:[- ]approved)?)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "warranty",
        re.compile(
            r"(?P<claim>\b(?:\d+[ -]?(?:\w+[ -]?)?)?"
            r"(?:warrant\w*|guarantee\w*)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "dimensions or weight",
        re.compile(
            r"\b(?:measures?|dimensions?|weighs?)\b\s+"
            r"(?P<claim>-?\d+(?:\.\d+)?(?:[xX×/\-]\s*\d+(?:\.\d+)?)*"
            r"\s*(?:inches?|in\.?|feet|ft\.?\b|centimeters?|cm\b|"
            r"millimeters?|mm\b|meters?|pounds?|lbs?\.?\b|kilograms?|"
            r"kg\b|ounces?|oz\b))",
            re.IGNORECASE,
        ),
    ),
    (
        "material",
        re.compile(
            r"\b(?:made of|constructed of|built from|composed of|"
            r"material is)\b\s+"
            r"(?P<claim>[a-z]+(?:\s+[a-z]+){0,4})",
            re.IGNORECASE,
        ),
    ),
    (
        "performance",
        re.compile(
            r"\b(?:up to|max(?:imum)?)\b\s*"
            r"(?P<claim>-?\d+(?:\.\d+)?(?:\s*-?\d+(?:\.\d+)?)?"
            r"\s*[a-z]{1,6})?",
            re.IGNORECASE,
        ),
    ),
    (
        "compatibility",
        re.compile(
            r"\b(?:compatible with|works with|for use with|fits)\b\s*"
            r"(?P<claim>[a-z0-9]+(?:\s+[a-z0-9\-]+){0,4})?",
            re.IGNORECASE,
        ),
    ),
    (
        "accessory",
        re.compile(
            r"\b(?:includes?|comes with)\b\s*"
            r"(?P<claim>[a-z0-9]+(?:\s+[a-z0-9\-]+){0,4})",
            re.IGNORECASE,
        ),
    ),
]

# Function words do not carry facts; they are ignored when checking whether
# a captured claim phrase is grounded.
_FUNCTION_WORDS = {
    "a", "an", "the", "of", "for", "with", "and", "or", "to", "in", "on",
    "at", "by", "from", "up", "per", "each", "x", "plus", "more", "less",
    "than", "as", "it", "its", "this", "that", "all", "any", "most", "some",
    "such", "both", "also", "be", "is", "are", "not", "no", "can", "may",
    "will", "would", "when", "while", "if", "then", "over", "under",
}

# Unit aliases so "18 in" (attribute) grounds "18 inches" (description).
_UNIT_ALIASES: dict[str, set[str]] = {
    "in": {"in", "inch", "inches"},
    "ft": {"ft", "foot", "feet"},
    "cm": {"cm", "centimeter", "centimeters"},
    "mm": {"mm", "millimeter", "millimeters"},
    "m": {"m", "meter", "meters"},
    "kg": {"kg", "kilogram", "kilograms"},
    "g": {"g", "gram", "grams"},
    "lb": {"lb", "lbs", "pound", "pounds"},
    "oz": {"oz", "ounce", "ounces"},
    "v": {"v", "volt", "volts"},
    "a": {"a", "amp", "amps", "ampere", "amperes"},
}

# Spelled-out quantities ground the matching digit token (e.g. "Six sanding
# belts" is grounded by "6pc" in the input or an evidence quote).
_NUMBER_WORDS: dict[str, str] = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
    "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
    "ninety": "90", "hundred": "100",
}

# The description fields that carry generated copy; every one is grounded
# individually. ``item_features`` is handled separately (per entry).
_TEXT_FIELDS = (
    "product_title",
    "short_description",
    "mobile_description",
    "invoice_description",
    "long_description",
    "retail_description",
    "marketing_description",
    "with_",
    "application",
    "includes",
    "product_name",
)

# The "with" and "includes" delivery fields ARE accessory claims by
# definition (the prompt only fills them when the facts say so), so the whole
# field content must be grounded - no trigger word needed.
_WHOLE_FIELD_CATEGORY = {"with_": "accessory", "includes": "accessory"}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _grounded_vocabulary(
    identity: ProductIdentity,
    attributes: dict[str, AttributeValue],
    quotes: list[str],
) -> set[str]:
    """Every significant fact term this product's copy may draw on."""
    parts: list[str] = [
        identity.manufacturer or "",
        identity.brand or "",
        identity.mpn or "",
        identity.raw_description or "",
        identity.sku or "",
    ]
    for attribute in attributes.values():
        parts.extend(
            [
                attribute.name,
                attribute.raw_value or "",
                attribute.value or "",
                attribute.unit or "",
            ]
        )
    parts.extend(quotes)

    vocabulary = _tokens(" ".join(parts))
    for token in list(vocabulary):
        aliases = _UNIT_ALIASES.get(token)
        if aliases:
            vocabulary.update(aliases)
        digit = _NUMBER_WORDS.get(token)
        if digit:
            vocabulary.add(digit)
    return vocabulary


def _token_grounded(token: str, vocabulary: set[str]) -> bool:
    """A claim token is grounded directly, via its number-word digit form,
    or via the singular form (``belts`` -> ``belt``)."""
    if token in vocabulary:
        return True
    digit = _NUMBER_WORDS.get(token)
    if digit and digit in vocabulary:
        return True
    if token.endswith("s") and len(token) > 1 and token[:-1] in vocabulary:
        return True
    return False


def _claim_grounded(phrase: str, vocabulary: set[str]) -> bool:
    """A captured claim phrase is grounded when every non-function token is."""
    tokens = _tokens(phrase) - _FUNCTION_WORDS
    if not tokens:
        return False
    return all(_token_grounded(token, vocabulary) for token in tokens)


def find_violations(text: str, vocabulary: set[str]) -> list[tuple[str, str]]:
    """(category, claim snippet) pairs for every unsupported claim found."""
    violations: list[tuple[str, str]] = []
    for category, pattern in _CATEGORY_PATTERNS:
        for match in pattern.finditer(text):
            phrase = match.group("claim") or match.group(0)
            if not _claim_grounded(phrase, vocabulary):
                violations.append((category, phrase.strip()))
    return violations


def apply_grounding(
    descriptions: Descriptions,
    *,
    identity: ProductIdentity,
    attributes: dict[str, AttributeValue],
    quotes: list[str] | None = None,
) -> tuple[Descriptions, list[str], int]:
    """Drop unsupported claims; returns (descriptions, reasons, drops).

    ``drops`` counts how many fields/entries were affected so the caller can
    decide the stage status (review when partial, failed when nothing
    remains).
    """
    vocabulary = _grounded_vocabulary(identity, attributes, quotes or [])
    reasons: list[str] = []
    drops = 0

    for field in _TEXT_FIELDS:
        value = getattr(descriptions, field) or ""
        if not value:
            continue
        if field in _WHOLE_FIELD_CATEGORY:
            tokens = _tokens(value) - _FUNCTION_WORDS
            if tokens and all(
                _token_grounded(token, vocabulary) for token in tokens
            ):
                continue
            violations = [(_WHOLE_FIELD_CATEGORY[field], value[:60])]
        else:
            violations = find_violations(value, vocabulary)
        if not violations:
            continue
        category, snippet = violations[0]
        setattr(descriptions, field, "")
        drops += 1
        reasons.append(
            f"description grounding: {field} contained an unsupported "
            f"{category} claim (\u201c{snippet}\u201d) and was left blank"
        )

    if descriptions.item_features:
        kept: list[str] = []
        for item in descriptions.item_features:
            violations = find_violations(item, vocabulary)
            if not violations:
                kept.append(item)
                continue
            category, snippet = violations[0]
            drops += 1
            reasons.append(
                f"description grounding: an item feature contained an "
                f"unsupported {category} claim (\u201c{snippet}\u201d) "
                f"and was dropped"
            )
        descriptions.item_features = kept

    return descriptions, reasons, drops


def has_any_content(descriptions: Descriptions) -> bool:
    """True when any description field still carries generated copy."""
    if descriptions.item_features:
        return True
    return any(getattr(descriptions, field) or "" for field in _TEXT_FIELDS)