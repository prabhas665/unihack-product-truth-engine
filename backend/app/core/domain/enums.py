"""Enumerations used across the internal domain model.

Generic by design: the official UniHack values (LOV lists, UOM rules, quality
gate formulas) are not known yet and must not be invented here. Official values
plug into validation/vocab.py and the pipeline stages later.
"""

from enum import Enum


class SourceType(str, Enum):
    """Kind of permitted source a piece of evidence came from.

    Only permitted source kinds are represented; marketplace/distributor
    sources (Amazon, eBay, ...) are excluded per UniHack rules and never
    appear here. UNKNOWN exists so unsupported source types are explicit -
    the source policy whitelists the manufacturer types below and must not
    silently allow anything else.
    """

    MANUFACTURER_PRODUCT_PAGE = "manufacturer_product_page"
    MANUFACTURER_TECHNICAL_PDF = "manufacturer_technical_pdf"
    MANUFACTURER_MANUAL = "manufacturer_manual"
    MANUFACTURER_CATALOGUE = "manufacturer_catalogue"
    MANUFACTURER_DIGITAL_ASSET = "manufacturer_digital_asset"
    UNKNOWN = "unknown"


class SourceTrustLevel(str, Enum):
    """Generic source trust tiers used for conflict resolution.

    The exact trust hierarchy is defined by the official UniHack rules; these
    generic tiers exist so the model can already express trust. Map the official
    hierarchy onto these tiers when the rules become available.
    """

    MANUFACTURER_OFFICIAL = "manufacturer_official"
    OFFICIAL_DISTRIBUTOR = "official_distributor"
    UNVERIFIED = "unverified"


class AttributeStatus(str, Enum):
    """Lifecycle of a single attribute value through the pipeline."""

    EXTRACTED = "extracted"
    NORMALIZED = "normalized"
    VALIDATED = "validated"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class ValidationType(str, Enum):
    LOV = "lov"  # List-of-Values validation (official UniHack LOVs)
    UOM = "uom"  # unit-of-measure normalization/validation
    RULE = "rule"  # deterministic business rules (official UniHack gates)


class ValidationStatus(str, Enum):
    NOT_VALIDATED = "not_validated"
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class ConflictStatus(str, Enum):
    """State of disagreement between candidate values of one attribute."""

    AGREEMENT = "agreement"
    CONFLICT = "conflict"
    UNRESOLVED = "unresolved"


class ReviewDecision(str, Enum):
    APPROVED = "approved"
    CORRECTED = "corrected"
    REJECTED = "rejected"


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    FAILED = "failed"
