"""Deterministic source ranking. No LLM involved.

Each candidate gets a relevance_score in 0..1 as a weighted sum of
normalized factors; candidates are then sorted best-first. Pure function of
(candidate, product) - deterministic by design. The weights are documented
defaults and can be tuned later without touching providers or policy.
"""

from __future__ import annotations

import re

from app.core.domain import ProductIdentity, SourceTrustLevel, SourceType
from app.sources.candidates import (
    CandidateStatus,
    ManufacturerRelationship,
    SourceCandidate,
)

# Default weights (sum = 1.0).
RANKING_WEIGHTS: dict[str, float] = {
    "policy_status": 0.25,
    "manufacturer_domain": 0.25,
    "source_type": 0.15,
    "part_number": 0.15,
    "relevance": 0.10,
    "trust_level": 0.10,
}

STATUS_SCORES = {
    CandidateStatus.ALLOWED: 1.0,
    CandidateStatus.PENDING: 0.5,
    CandidateStatus.REJECTED: 0.0,
    CandidateStatus.PROHIBITED: 0.0,
}

RELATIONSHIP_SCORES = {
    ManufacturerRelationship.OWNED: 1.0,
    ManufacturerRelationship.EXTERNAL: 0.6,
    ManufacturerRelationship.UNKNOWN: 0.3,
}

SOURCE_TYPE_SCORES = {
    SourceType.MANUFACTURER_PRODUCT_PAGE: 1.0,
    SourceType.MANUFACTURER_TECHNICAL_PDF: 0.9,
    SourceType.MANUFACTURER_MANUAL: 0.85,
    SourceType.MANUFACTURER_CATALOGUE: 0.7,
    SourceType.MANUFACTURER_DIGITAL_ASSET: 0.5,
    SourceType.UNKNOWN: 0.1,
}

TRUST_SCORES = {
    SourceTrustLevel.MANUFACTURER_OFFICIAL: 1.0,
    SourceTrustLevel.OFFICIAL_DISTRIBUTOR: 0.7,
    SourceTrustLevel.UNVERIFIED: 0.4,
}


def _product_tokens(product: ProductIdentity) -> set[str]:
    """Lowercased alphanumeric tokens (>=2 chars) from the identity fields."""
    tokens: set[str] = set()
    for field in (
        product.manufacturer,
        product.brand,
        product.mpn,
        product.raw_description,
    ):
        for token in re.findall(r"[a-z0-9]+", field.lower()):
            if len(token) >= 2:
                tokens.add(token)
    return tokens


def _part_number_match(candidate: SourceCandidate, product: ProductIdentity) -> float:
    """1.0 when the exact (case-insensitive) part number appears in URL/title."""
    mpn = product.mpn.lower().strip()
    if not mpn or len(mpn) < 2:
        return 0.0
    haystack = (candidate.url + " " + candidate.title).lower()
    return 1.0 if mpn in haystack else 0.0


def _title_url_relevance(candidate: SourceCandidate, product: ProductIdentity) -> float:
    """Fraction of identity tokens present in the candidate URL/title."""
    tokens = _product_tokens(product)
    if not tokens:
        return 0.0
    haystack = (candidate.url + " " + candidate.title).lower()
    matched = sum(1 for token in tokens if token in haystack)
    return matched / len(tokens)


def score_candidate(
    candidate: SourceCandidate,
    product: ProductIdentity,
    weights: dict[str, float] | None = None,
) -> float:
    """Weighted relevance score in 0..1 (used as `relevance_score`)."""
    w = weights or RANKING_WEIGHTS
    return (
        w["policy_status"] * STATUS_SCORES[candidate.status]
        + w["manufacturer_domain"] * RELATIONSHIP_SCORES[candidate.manufacturer_relationship]
        + w["source_type"] * SOURCE_TYPE_SCORES[candidate.source_type]
        + w["part_number"] * _part_number_match(candidate, product)
        + w["relevance"] * _title_url_relevance(candidate, product)
        + w["trust_level"] * TRUST_SCORES[candidate.trust_level]
    )


def rank_candidates(
    candidates: list[SourceCandidate],
    product: ProductIdentity,
    weights: dict[str, float] | None = None,
) -> list[SourceCandidate]:
    """Return scored copies of the candidates, sorted best-first.

    Ties are broken by URL for full determinism.
    """
    scored = [
        candidate.model_copy(
            update={"relevance_score": score_candidate(candidate, product, weights)}
        )
        for candidate in candidates
    ]
    return sorted(scored, key=lambda c: (-c.relevance_score, c.url))
