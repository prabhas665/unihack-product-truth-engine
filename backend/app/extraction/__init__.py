"""Evidence-based AI extraction package.

Sits between the LLM provider layer (app.llm) and the domain model
(app.core.domain). Extracts candidate attributes from supplied
EvidenceRecords, requiring evidence-id traceability for every claim.

Strictly separated from source discovery/retrieval (app.sources), official
LOV/UOM validation (future stage), and description generation (future).
"""

from app.extraction.prompt import SYSTEM_PROMPT, build_extraction_prompt
from app.extraction.service import (
    MAX_CHARS_PER_RECORD,
    MAX_NOTE_CHARS,
    ExtractionService,
    to_domain_attribute_values,
)
from app.extraction.types import (
    CandidateAttribute,
    ExtractionError,
    ExtractionErrorKind,
    ExtractionOutput,
    ExtractionOutputItem,
    ExtractionRequest,
    ExtractionResponse,
    RejectedAttribute,
)

__all__ = [
    "CandidateAttribute",
    "ExtractionError",
    "ExtractionErrorKind",
    "ExtractionOutput",
    "ExtractionOutputItem",
    "ExtractionRequest",
    "ExtractionResponse",
    "ExtractionService",
    "MAX_CHARS_PER_RECORD",
    "MAX_NOTE_CHARS",
    "RejectedAttribute",
    "SYSTEM_PROMPT",
    "build_extraction_prompt",
    "to_domain_attribute_values",
]
