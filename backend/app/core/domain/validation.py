"""Validation results attached to attribute values.

The LOV/UOM/rule details are placeholders. The official UniHack LOV values and
UOM rules will be loaded into validation/vocab.py later; the deterministic
quality gates come from the official UniHack rules. Nothing is evaluated here.
"""

from typing import Any

from pydantic import BaseModel, Field

from app.core.domain.enums import ValidationStatus, ValidationType


class ValidationResult(BaseModel):
    validation_type: ValidationType
    status: ValidationStatus = ValidationStatus.NOT_VALIDATED
    message: str = ""
    # Plug-in points (filled by future pipeline stages using official resources):
    #   LOV:  details = {"lov_key": <attribute name>, "value": <value checked>}
    #   UOM:  details = {"canonical_unit": <target unit>, "factor": <conversion factor>}
    #   RULE: details = {"rule_id": <official rule id>, "gate": <passed/blocked>}
    details: dict[str, Any] = Field(default_factory=dict)
