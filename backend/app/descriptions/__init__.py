"""Description generation: evidence-bound, LLM-powered copywriting."""

from app.descriptions.grounding import (
    apply_grounding,
    find_violations,
    has_any_content,
)
from app.descriptions.rules import apply_description_rules
from app.descriptions.service import DescriptionsService
from app.descriptions.types import GeneratedDescriptions

__all__ = [
    "DescriptionsService",
    "GeneratedDescriptions",
    "apply_grounding",
    "apply_description_rules",
    "find_violations",
    "has_any_content",
]
