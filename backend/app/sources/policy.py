"""Source policy: decides whether a candidate source is permitted.

Decision order:
  1. Marketplace hostname labels (Amazon, eBay, ...) -> PROHIBITED.
  2. Configurable prohibited patterns -> PROHIBITED.
  3. Manufacturer-owned domain + permitted source type -> ALLOWED.
  4. Allowlisted (external but trusted) domain + permitted type -> ALLOWED.
  5. Everything else -> REJECTED (unknown external domain).

Every decision records a human-readable reason. Configuration comes from
environment variables (SOURCE_ALLOWED_DOMAINS / SOURCE_PROHIBITED_DOMAINS,
see app/config.py) and the per-discovery manufacturer domain registry
(empty until the official UniHack manufacturer data is available). No
UniHack data is hard-coded here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.config import settings
from app.core.domain import SourceTrustLevel, SourceType
from app.sources.candidates import (
    CandidateStatus,
    ManufacturerRelationship,
    SourceCandidate,
    normalize_domain,
)

# Hostname labels that identify general marketplaces. Matching is on exact
# hostname labels so e.g. "amazonaws.com" (a hosting service) is NOT rejected.
MARKETPLACE_LABELS = frozenset({"amazon", "ebay", "aliexpress", "alibaba"})

# Source types permitted for manufacturer-owned (and allowlisted) domains.
# UNKNOWN is deliberately absent: unsupported source types are not allowed.
PERMITTED_SOURCE_TYPES = frozenset(
    {
        SourceType.MANUFACTURER_PRODUCT_PAGE,
        SourceType.MANUFACTURER_TECHNICAL_PDF,
        SourceType.MANUFACTURER_MANUAL,
        SourceType.MANUFACTURER_CATALOGUE,
        SourceType.MANUFACTURER_DIGITAL_ASSET,
    }
)


def policy_from_settings() -> "SourcePolicyConfig":
    """Build a policy config from the SOURCE_ALLOWED_/PROHIBITED_DOMAINS env vars."""
    return SourcePolicyConfig(
        allowed_domain_patterns=_parse_csv(settings.source_allowed_domains),
        prohibited_domain_patterns=_parse_csv(settings.source_prohibited_domains),
    )


def _parse_csv(raw: str) -> list[str]:
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


class SourcePolicyConfig(BaseModel):
    """Configurable domain patterns for the source policy.

    Patterns are plain domains (e.g. "acme.com"); a candidate matches when
    its domain equals the pattern or is a subdomain of it.
    """

    manufacturer_domains: list[str] = Field(default_factory=list)
    allowed_domain_patterns: list[str] = Field(default_factory=list)
    prohibited_domain_patterns: list[str] = Field(default_factory=list)
    allowed_source_types: frozenset[SourceType] = PERMITTED_SOURCE_TYPES
    prefer_manufacturer_owned: bool = True


class SourcePolicy:
    """Evaluates candidates against the config and records the outcome."""

    def __init__(self, config: SourcePolicyConfig | None = None) -> None:
        self.config = config or SourcePolicyConfig()

    def evaluate(self, candidate: SourceCandidate) -> SourceCandidate:
        """Decide the status of one candidate; returns an updated copy."""
        domain = candidate.domain or normalize_domain(candidate.url)
        relationship, owned = self._relationship(domain)
        trust_level = candidate.trust_level
        status = CandidateStatus.REJECTED
        reason = ""

        if self._is_marketplace(domain):
            status = CandidateStatus.PROHIBITED
            reason = f"prohibited marketplace domain '{domain}'"
        elif self._matches_any(domain, self.config.prohibited_domain_patterns):
            status = CandidateStatus.PROHIBITED
            reason = f"prohibited by configured pattern (domain '{domain}')"
        elif owned or self._matches_any(domain, self.config.allowed_domain_patterns):
            if candidate.source_type in self.config.allowed_source_types:
                status = CandidateStatus.ALLOWED
                if owned:
                    reason = (
                        f"manufacturer-owned domain; permitted source type "
                        f"{candidate.source_type.value}"
                    )
                    trust_level = SourceTrustLevel.MANUFACTURER_OFFICIAL
                else:
                    reason = (
                        f"allowlisted domain; permitted source type "
                        f"{candidate.source_type.value}"
                    )
                    trust_level = SourceTrustLevel.OFFICIAL_DISTRIBUTOR
            else:
                status = CandidateStatus.REJECTED
                reason = (
                    f"source type '{candidate.source_type.value}' is not in "
                    f"the permitted set"
                )
        else:
            status = CandidateStatus.REJECTED
            reason = (
                f"unknown external domain '{domain}': not manufacturer-owned "
                f"and not in the allowed patterns"
            )

        return candidate.model_copy(
            update={
                "domain": domain,
                "manufacturer_relationship": relationship,
                "trust_level": trust_level,
                "status": status,
                "rejection_reason": reason,
            }
        )

    def filter(
        self, candidates: list[SourceCandidate]
    ) -> tuple[list[SourceCandidate], list[SourceCandidate]]:
        """Return (allowed, rejected) after evaluating every candidate."""
        evaluated = [self.evaluate(candidate) for candidate in candidates]
        allowed = [c for c in evaluated if c.status == CandidateStatus.ALLOWED]
        rejected = [c for c in evaluated if c.status != CandidateStatus.ALLOWED]
        return allowed, rejected

    def _relationship(
        self, domain: str
    ) -> tuple[ManufacturerRelationship, bool]:
        if self._matches_any(domain, self.config.manufacturer_domains):
            return ManufacturerRelationship.OWNED, True
        if self._matches_any(domain, self.config.allowed_domain_patterns):
            return ManufacturerRelationship.EXTERNAL, False
        return ManufacturerRelationship.UNKNOWN, False

    @staticmethod
    def _is_marketplace(domain: str) -> bool:
        labels = {part for part in domain.split(".") if part}
        return bool(labels & MARKETPLACE_LABELS)

    @staticmethod
    def _matches_any(domain: str, patterns: list[str]) -> bool:
        domain = domain.lower()
        for pattern in patterns:
            p = pattern.lower().strip()
            if p.startswith("www."):
                p = p[4:]
            if domain == p or domain.endswith("." + p):
                return True
        return False
