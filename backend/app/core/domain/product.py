"""Aggregate root of the internal product intelligence model.

One ProductIntelligence instance represents everything the pipeline knows
about one product. This generic internal model is the source of truth; a
future mapper translates it into the official UniHack Delivery Format when the
official schema becomes available.
"""

from pydantic import BaseModel, Field, model_validator

from app.core.domain.attributes import AttributeValue
from app.core.domain.classification import Classification
from app.core.domain.descriptions import Descriptions
from app.core.domain.evidence import Evidence
from app.core.domain.identity import ProductIdentity
from app.core.domain.metadata import ProcessingMetadata
from app.core.domain.quality import QualityScore


class ProductIntelligence(BaseModel):
    identity: ProductIdentity = Field(default_factory=ProductIdentity)
    classification: Classification = Field(default_factory=Classification)
    # Attribute name -> AttributeValue.
    attributes: dict[str, AttributeValue] = Field(default_factory=dict)
    # Evidence id -> Evidence.
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    descriptions: Descriptions = Field(default_factory=Descriptions)
    quality: QualityScore = Field(default_factory=QualityScore)
    processing: ProcessingMetadata = Field(default_factory=ProcessingMetadata)

    @model_validator(mode="after")
    def _validate_evidence_references(self) -> "ProductIntelligence":
        """Every evidence_ref must point at a stored evidence record.

        Keeps the model consistent: no orphan references, so 'why this value?'
        and evidence attachments always resolve later.
        """
        known = set(self.evidence)
        missing: set[str] = set()
        for attribute in self.attributes.values():
            for ref in attribute.evidence_refs:
                if ref not in known:
                    missing.add(ref)
            for candidate in attribute.candidates:
                for ref in candidate.evidence_refs:
                    if ref not in known:
                        missing.add(ref)
        if missing:
            raise ValueError(
                f"evidence references to unknown evidence ids: {sorted(missing)}"
            )
        return self
