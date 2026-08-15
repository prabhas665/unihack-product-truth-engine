"""Extraction evidence-selection policy (Step 20).

Before the LLM extraction call, the pipeline hands every usable evidence
record to the model. That is unsafe for two reasons:

1. Cross-product contamination - a manufacturer domain often serves several
   product pages (sibling MPNs). Sending five of them in one prompt lets the
   model attribute one product's specs to another, or cite the wrong evidence
   id, which is exactly the failure mode observed on the XLC10ZW run.
2. Context bloat - five full pages can blow past ~8k tokens, which on a slow
   free-tier model exceeds the hard wall-clock timeout and kills the whole run.

This module selects only the evidence that can plausibly describe the
requested product, then enforces a hard total-character budget. It is a pure,
deterministic, provider-agnostic filter: it never reads the LLM layer, never
touches the network, and never changes attribute values.

Policy
-----
PRIMARY   : the requested MPN appears (exact, case-insensitive) in the
            record URL or title -> strongest relevance.
SECONDARY : the requested MPN appears only in the record text, or the record
            carries no other product identity at all (a generic manufacturer
            page) -> include, but lower priority.
EXCLUDE   : the record clearly names a DIFFERENT product (a sibling MPN token
            appears in the URL slug or title) and never references the
            requested MPN -> drop it.

Budget
------
Records are included in (PRIMARY -> SECONDARY) order until adding the next
record would exceed ``budget_chars``. Each record's cost is
``min(len(text), max_chars_per_record) + header_len``.

Traceability is preserved: the selected subset keeps its original evidence
ids, and the extraction service still rejects any attribute that cites an id
outside the supplied set ("dangling"). The full retrieved evidence set remains
available to the rest of the pipeline (delivery, evidence map); only the LLM
input is filtered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.domain import ProductIdentity
from app.sources.retrieval import EvidenceRecord

# A product-like token: >=4 chars of A-Z0-9 containing at least one digit.
_MPN_TOKEN_RE = re.compile(r"[A-Z0-9]{4,}")
_DIGIT_RE = re.compile(r"\d")


def _mpn_tokens(value: str) -> set[str]:
    """Uppercased product-like tokens (contain a digit) from a string."""
    return {
        token
        for token in _MPN_TOKEN_RE.findall((value or "").upper())
        if _DIGIT_RE.search(token)
    }


def _header_len(record: EvidenceRecord) -> int:
    header = f"[{record.evidence_id}]"
    if record.title:
        header += f" {record.title}"
    if record.url:
        header += f" | {record.url}"
    return len(header)


@dataclass
class SelectionResult:
    """The evidence the model may see, plus human-readable drop reasons."""

    selected: list[EvidenceRecord] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def select_extraction_evidence(
    identity: ProductIdentity,
    records: list[EvidenceRecord],
    *,
    budget_chars: int = 12_000,
    max_chars_per_record: int = 6_000,
) -> SelectionResult:
    """Pick MPN-relevant evidence and fit it under the context budget.

    Returns the records to send to extraction plus a reason for every dropped
    record (sibling exclusion or budget overflow).
    """
    requested = (identity.mpn or "").strip().upper()

    def classify(record: EvidenceRecord) -> tuple[int, str]:
        """Return (rank, kind). rank 0=PRIMARY,1=SECONDARY,2=SIBLING."""
        url = record.url or ""
        title = record.title or ""
        text = record.text or ""
        url_u = url.upper()
        title_u = title.upper()
        text_u = text.upper()
        has_requested = bool(requested) and (
            requested in url_u or requested in title_u or requested in text_u
        )
        if has_requested:
            primary = requested in url_u or requested in title_u
            return (0 if primary else 1, "primary" if primary else "secondary")

        # No reference to the requested MPN: is this clearly a different product?
        # Match on the title/text only (not the URL slug): a real manufacturer
        # sibling page names its own MPN in its title/body, while a generic
        # page (or a fixture using a shared page) carries no foreign MPN there.
        sibling_tokens = _mpn_tokens(title) | _mpn_tokens(text)
        if requested:
            sibling_tokens.discard(requested)
        if sibling_tokens:
            return (2, "sibling:" + ",".join(sorted(sibling_tokens)))
        return (1, "secondary")

    ranked: list[tuple[int, int, EvidenceRecord]] = []
    siblings: list[tuple[EvidenceRecord, str]] = []
    for index, record in enumerate(records):
        rank, kind = classify(record)
        if rank == 2:
            siblings.append((record, kind))
            continue
        ranked.append((rank, index, record))

    # Stable sort: PRIMARY first (preserving original order), then SECONDARY.
    ranked.sort(key=lambda item: (item[0], item[1]))

    selected: list[EvidenceRecord] = []
    dropped: list[str] = []
    used_chars = 0
    for _rank, _index, record in ranked:
        cost = (
            min(len(record.text or ""), max_chars_per_record)
            + _header_len(record)
        )
        if selected and used_chars + cost > budget_chars:
            dropped.append(
                f"extraction evidence excluded by context budget: "
                f"{record.evidence_id} (~{cost} chars) would exceed "
                f"{budget_chars}-char budget"
            )
            continue
        selected.append(record)
        used_chars += cost

    for record, kind in siblings:
        tokens = kind.split(":", 1)[1] if ":" in kind else ""
        dropped.append(
            f"extraction evidence excluded (sibling product {tokens}): "
            f"{record.evidence_id} does not reference requested MPN "
            f"{requested or '<unknown>'}"
        )

    return SelectionResult(selected=selected, dropped=dropped)
