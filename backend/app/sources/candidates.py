"""Source candidates produced by discovery providers.

A SourceCandidate is a *potential* source, before and after policy filtering.
The policy (policy.py) fills status/rejection_reason/relationship; ranking
(ranking.py) fills relevance_score. Converting candidates into Evidence
belongs to the future evidence retrieval stage, NOT here.
"""

from __future__ import annotations

from enum import Enum
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from app.core.domain import SourceTrustLevel, SourceType


class DiscoveryMethod(str, Enum):
    """How a candidate was discovered (see the future provider interfaces)."""

    SEARCH = "search"
    DIRECT_URL = "direct_url"
    DOCUMENT = "document"
    MANUAL = "manual"


class CandidateStatus(str, Enum):
    PENDING = "pending"
    ALLOWED = "allowed"
    PROHIBITED = "prohibited"
    REJECTED = "rejected"


class ManufacturerRelationship(str, Enum):
    """Relationship between the candidate's domain and the manufacturer."""

    OWNED = "owned"
    EXTERNAL = "external"  # not owned, but permitlisted (e.g. official distributor)
    UNKNOWN = "unknown"


class SourceCandidate(BaseModel):
    """One discovered candidate source for a product."""

    # Stable candidate id (set by discovery providers; used to link
    # retrieved evidence back to the candidate).
    id: str = ""
    url: str
    source_type: SourceType = SourceType.UNKNOWN
    title: str = ""
    # Normalized hostname; filled by the policy when not provided.
    domain: str = ""
    manufacturer_relationship: ManufacturerRelationship = (
        ManufacturerRelationship.UNKNOWN
    )
    trust_level: SourceTrustLevel = SourceTrustLevel.UNVERIFIED
    # Set by ranking (0..1); 0 until ranked.
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    discovery_method: DiscoveryMethod = DiscoveryMethod.SEARCH
    status: CandidateStatus = CandidateStatus.PENDING
    # Human-readable reason; always set for PROHIBITED/REJECTED.
    rejection_reason: str = ""


def normalize_domain(url: str) -> str:
    """Extract the lowercase hostname (minus 'www.') from a URL.

    Returns "" for unparseable URLs so callers can reject them safely.
    """
    try:
        host = urlsplit(url).netloc.lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    host = host.split(":")[0]
    return host
