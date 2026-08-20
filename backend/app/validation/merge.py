"""Deterministic de-duplication of validated attributes.

The delivery format carries ONE value per attribute name, so same-name
candidates must collapse into a single winner. Collapsing is fully
deterministic and evidence-driven:

- Names are canonicalized (case-folded, whitespace-collapsed, trailing
  unit markers like "(mm)" / "(in)" / "(metric)" stripped) so imperial and
  metric variants of the same attribute group together.
- Within a group the winner is the highest-confidence candidate; ties are
  broken by evidence source rank (manufacturer pages/PDFs > catalogues >
  everything else), then by original position (first wins).
- Exact duplicates (same name, value and unit) collapse into one.
- Merged-away duplicates are reported via ``out_reasons`` so nothing is
  silently dropped and the run can surface a review note.
"""

from __future__ import annotations

import re

from app.core.domain import SourceType
from app.validation.types import ValidatedAttribute

# Trailing parenthetical markers that distinguish imperial/metric variants
# of the same attribute, e.g. "Width (in)" vs "Width (mm)", "Speed (metric)".
_TRACKING_MARKER_RE = re.compile(
    r"\s*\((?:\d+\s*)?(?:in|inch|inches|mm|cm|m|ft|foot|feet|yd|lb|lbs|oz|kg|g|mg|mil|ml|l|imperial|metric|us|usa|uk|eu|european|american)(?:s)?\)$",
    re.IGNORECASE,
)

# Evidence source rank used to break confidence ties: a value found on a
# manufacturer's own product page / datasheet / manual outranks the same
# claim on a catalogue, distributor or unknown page.
_SOURCE_RANK: dict[SourceType, int] = {
    SourceType.MANUFACTURER_PRODUCT_PAGE: 3,
    SourceType.MANUFACTURER_TECHNICAL_PDF: 3,
    SourceType.MANUFACTURER_MANUAL: 3,
    SourceType.MANUFACTURER_CATALOGUE: 2,
    SourceType.MANUFACTURER_DIGITAL_ASSET: 2,
}

_UNKNOWN_SOURCE_RANK = 1
_UNLINKED_RANK = 0


def canonical_attribute_name(name: str) -> str:
    """Case-folded, whitespace-collapsed name without trailing unit markers."""
    collapsed = " ".join((name or "").split()).casefold()
    return _TRACKING_MARKER_RE.sub("", collapsed).strip()


def _source_rank_for(
    attribute: ValidatedAttribute,
    evidence_rank: dict[str, int] | None,
) -> int:
    if not evidence_rank:
        return _UNLINKED_RANK
    for evidence_id in attribute.evidence_refs:
        rank = evidence_rank.get(evidence_id)
        if rank is not None:
            return rank
    return _UNLINKED_RANK


def evidence_source_rank(
    evidence: list,
    source_rank: dict[SourceType, int] | None = None,
) -> dict[str, int]:
    """Map evidence_id -> source rank for the records of one run."""
    rank_table = source_rank or _SOURCE_RANK
    by_id: dict[str, int] = {}
    for record in evidence:
        by_id[record.evidence_id] = rank_table.get(
            record.source_type, _UNKNOWN_SOURCE_RANK
        )
    return by_id


def _winner_key(
    attribute: ValidatedAttribute,
    evidence_rank: dict[str, int] | None,
    index: int,
) -> tuple:
    """(confidence, source rank, -position): higher confidence wins, then
    better evidence source, then the earliest candidate."""
    return (
        attribute.confidence,
        _source_rank_for(attribute, evidence_rank),
        -index,
    )


def merge_validated_attributes(
    items: list[ValidatedAttribute],
    *,
    evidence_rank: dict[str, int] | None = None,
    out_reasons: list[str] | None = None,
) -> list[ValidatedAttribute]:
    """Collapse same-name candidates into one deterministic winner each.

    Group order follows first appearance; the winning candidate keeps its
    original name and values. Merged-away duplicates are reported through
    ``out_reasons`` (when given) as one human-readable line each.
    """
    if not items:
        return []

    groups: dict[str, list[ValidatedAttribute]] = {}
    order: list[str] = []
    for item in items:
        key = canonical_attribute_name(item.name)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    merged: list[ValidatedAttribute] = []
    for key in order:
        group = groups[key]
        if len(group) == 1:
            merged.append(group[0])
            continue
        ranked = sorted(
            enumerate(group),
            key=lambda pair: _winner_key(pair[1], evidence_rank, pair[0]),
            reverse=True,
        )
        winner_index, winner = ranked[0]
        merged.append(winner)
        if out_reasons is not None:
            for index, other in ranked[1:]:
                other_value = other.raw_value or other.normalized_value
                out_reasons.append(
                    f"merged duplicate attribute {other.name!r} "
                    f"({other_value}{(' ' + other.unit) if other.unit else ''}, "
                    f"confidence {other.confidence:.0%}, position {index}) "
                    f"into {winner.name!r} ({winner.raw_value}"
                    f"{(' ' + winner.unit) if winner.unit else ''}, "
                    f"confidence {winner.confidence:.0%}, position {winner_index})"
                )
    return merged