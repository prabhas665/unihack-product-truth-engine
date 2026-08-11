"""Evidence: one quoted, retrievable source statement.

Every evidence record is stored with a stable id so attributes can reference
it without duplicating data. Only permitted sources are represented (see
SourceType in enums.py).
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.domain.common import utcnow
from app.core.domain.enums import SourceTrustLevel, SourceType


class Evidence(BaseModel):
    id: str
    source_url: str
    source_type: SourceType
    source_title: str = ""
    # Extracted evidence: the quoted text that backs an attribute value.
    snippet: str = ""
    retrieved_at: datetime = Field(default_factory=utcnow)
    trust_level: SourceTrustLevel = SourceTrustLevel.UNVERIFIED
    # Attribute names this evidence supports.
    supports_attributes: list[str] = Field(default_factory=list)
    # Digital assets found on the source, keyed by delivery column name
    # (e.g. "Product Image", "Alternate Image 1", "Specification Sheet").
    # Only assets actually present on the source are ever recorded; nothing
    # is invented. Empty when no assets were found.
    assets: dict[str, str] = Field(default_factory=dict)
