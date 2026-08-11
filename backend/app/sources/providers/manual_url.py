"""Manual URL discovery provider (first controlled live test).

Emits ONE SourceCandidate for an operator-confirmed URL (e.g. a
manufacturer-owned product page), then lets the existing SourcePolicy decide:
without a matching manufacturer-domain/allowlist entry the candidate is
REJECTED, exactly like any other discovery result. The provider never decides
acceptability and never fetches content - discovery and retrieval stay
separate (app.sources.retrieval).

This provider is NOT registered in the global PROVIDERS registry: it is only
used when injected explicitly (see app/pipeline/manual_enrich.py --url), so
default discovery behavior is unchanged.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from app.core.domain import ProductIdentity, SourceTrustLevel, SourceType
from app.sources.candidates import (
    CandidateStatus,
    DiscoveryMethod,
    SourceCandidate,
    normalize_domain,
)

if TYPE_CHECKING:
    from app.sources.discovery import DiscoveryContext


def _is_retrievable_url(url: str) -> bool:
    """True only for http/https URLs; anything else can never be fetched.

    Transport-safety filter only: the candidate still passes through the
    SourcePolicy untouched.
    """
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return False
    return scheme in ("http", "https")


def _candidate_id(url: str) -> str:
    """Stable candidate id derived from the real URL (never fabricated)."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"manual-{digest}"


class ManualUrlProvider:
    """SourceProvider implementation for one operator-confirmed URL."""

    name = "manual_url"
    kind = DiscoveryMethod.DIRECT_URL

    def __init__(
        self,
        url: str,
        *,
        source_type: SourceType = SourceType.MANUFACTURER_PRODUCT_PAGE,
        title: str = "",
    ) -> None:
        self._url = (url or "").strip()
        self._source_type = source_type
        self._title = (title or "").strip()

    def discover(
        self, product: ProductIdentity, context: "DiscoveryContext"
    ) -> list[SourceCandidate]:
        """Return exactly one candidate for the confirmed URL (or none)."""
        if not _is_retrievable_url(self._url):
            return []
        return [
            SourceCandidate(
                id=_candidate_id(self._url),
                url=self._url,
                title=self._title,
                source_type=self._source_type,
                domain=normalize_domain(self._url),
                discovery_method=DiscoveryMethod.DIRECT_URL,
                status=CandidateStatus.PENDING,
                trust_level=SourceTrustLevel.UNVERIFIED,
            )
        ]
