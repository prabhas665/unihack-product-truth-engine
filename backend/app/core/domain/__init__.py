"""Internal domain model (Step 2A).

Pydantic models representing the product intelligence the application will
produce. Kept separate from the database (SQLAlchemy) models in app.db and
from the API schemas in app.core.schemas.

Official UniHack values (LOV lists, UOM rules, quality gates, delivery
format) are NOT known yet and are intentionally not present here.
"""

from app.core.domain.attributes import AttributeValue, CandidateValue
from app.core.domain.classification import Classification
from app.core.domain.descriptions import Descriptions
from app.core.domain.enums import (
    AttributeStatus,
    ConflictStatus,
    ProcessingStatus,
    ReviewDecision,
    SourceTrustLevel,
    SourceType,
    ValidationStatus,
    ValidationType,
)
from app.core.domain.evidence import Evidence
from app.core.domain.identity import ProductIdentity
from app.core.domain.metadata import ProcessingError, ProcessingMetadata
from app.core.domain.product import ProductIntelligence
from app.core.domain.quality import ConfidenceSummary, QualityScore
from app.core.domain.review import ReviewState
from app.core.domain.validation import ValidationResult

__all__ = [
    "AttributeStatus",
    "AttributeValue",
    "CandidateValue",
    "Classification",
    "ConfidenceSummary",
    "ConflictStatus",
    "Descriptions",
    "Evidence",
    "ProcessingError",
    "ProcessingMetadata",
    "ProcessingStatus",
    "ProductIdentity",
    "ProductIntelligence",
    "QualityScore",
    "ReviewDecision",
    "ReviewState",
    "SourceTrustLevel",
    "SourceType",
    "ValidationResult",
    "ValidationStatus",
    "ValidationType",
]
