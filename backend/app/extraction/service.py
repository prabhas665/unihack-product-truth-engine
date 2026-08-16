"""Evidence-based AI extraction service.

Turns supplied EvidenceRecords into candidate attributes. Every accepted
attribute is traceable to one or more evidence ids; claims the model makes
without usable evidence are rejected with a reason. Conflicts are NOT
resolved here - they are represented as multiple candidates for the future
validation stage.

The service is separate from the LLM provider (app.llm): it only talks to
the provider-agnostic LLMClient interface.
"""

from __future__ import annotations

import re

from app.core.domain import (
    AttributeStatus,
    AttributeValue,
    CandidateValue,
    ConflictStatus,
)
from app.extraction.prompt import SYSTEM_PROMPT, build_extraction_prompt
from app.extraction.quotes import resolve_quote
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
from app.llm import (
    CompletionRequest,
    LLMClient,
    LLMError,
    LLMInvalidResponseError,
    StructuredCompletionRequest,
)

# Notes must stay user-safe and concise (never chain-of-thought).
MAX_NOTE_CHARS = 200
# Bound token use per evidence record.
MAX_CHARS_PER_RECORD = 6000

# Strict pattern for the bullet-list fallback: "- name: value [<id>[, <id>]]".
_BULLET_LINE_RE = re.compile(
    r"^\s*-\s*(?P<name>[A-Za-z0-9][A-Za-z0-9 _\-/.]*?):\s*(?P<value>.*)$"
)
_BULLET_IDS_RE = re.compile(r"\[([^\]]+)\]")


def _parse_bullet_output(raw: str, known_ids: set[str]) -> ExtractionOutput | None:
    """Parse "- name: value [ev-<id>]" lines into schema items (strict).

    Only lines with a name, a non-empty value, and evidence ids that are all
    among the supplied evidence are kept; everything else is ignored. Returns
    None when nothing usable was found.
    """
    items: list[ExtractionOutputItem] = []
    for line in raw.splitlines():
        match = _BULLET_LINE_RE.match(line)
        if not match:
            continue
        name = match.group("name").strip()
        value = match.group("value").strip()
        ids_match = _BULLET_IDS_RE.search(value)
        ids: list[str] = []
        if ids_match:
            ids = [item.strip() for item in ids_match.group(1).split(",")]
            value = (value[: ids_match.start()] + value[ids_match.end():]).strip(" ,")
        if not name or not value:
            continue
        if not ids or not all(item_id in known_ids for item_id in ids):
            continue
        items.append(
            ExtractionOutputItem(
                name=name, raw_value=value, evidence_ids=ids
            )
        )
    if not items:
        return None
    return ExtractionOutput(items=items)


class ExtractionService:
    """Extracts evidence-bound candidate attributes via an LLM client."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        """Run one extraction over the supplied evidence records.

        Raises ExtractionError when the LLM output is unusable as a whole
        (malformed JSON, schema violations such as confidence outside 0..1,
        or provider failure). Evidence-binding problems are reported per
        attribute via `rejected`.
        """
        known_ids = {record.evidence_id for record in request.evidence_records}
        prompt = build_extraction_prompt(
            request, max_chars_per_record=MAX_CHARS_PER_RECORD
        )

        try:
            output = self._client.structured_completion(
                StructuredCompletionRequest(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=prompt,
                    output_schema=ExtractionOutput,
                )
            )
        except LLMInvalidResponseError as exc:
            fallback = self._fallback_extract(request, prompt)
            if fallback is None:
                raise ExtractionError(
                    ExtractionErrorKind.SCHEMA_INVALID,
                    f"LLM output failed schema validation: {exc}",
                ) from exc
            output = fallback
        except LLMError as exc:
            raise ExtractionError(
                ExtractionErrorKind.LLM_FAILED, f"LLM call failed: {exc}"
            ) from exc

        attributes: list[CandidateAttribute] = []
        rejected: list[RejectedAttribute] = []
        records_by_id = {record.evidence_id: record for record in request.evidence_records}
        for item in output.items:
            candidate, reason = self._validate_item(item, known_ids)
            if candidate is not None:
                candidate = self._attach_quote(candidate, records_by_id)
                attributes.append(candidate)
            else:
                rejected.append(
                    RejectedAttribute(
                        name=item.name, raw_value=item.raw_value, reason=reason
                    )
                )

        return ExtractionResponse(
            attributes=attributes,
            rejected=rejected,
            evidence_ids_used=sorted(
                {eid for attribute in attributes for eid in attribute.evidence_ids}
            ),
        )

    def _fallback_extract(
        self, request: ExtractionRequest, prompt: str
    ) -> ExtractionOutput | None:
        """Tolerate providers that answer with a bullet list instead of JSON.

        Strictly parses lines like "- name: value [ev-<id>]" and reuses the
        exact same evidence-binding rules as the JSON path (the parsed items
        still go through _validate_item below). Never used to invent values:
        items without usable evidence ids are dropped. Confidence stays at the
        schema default 0.0 - a bullet answer carries no confidence.
        """
        try:
            raw = self._client.complete(
                CompletionRequest(system_prompt=SYSTEM_PROMPT, user_prompt=prompt)
            )
        except LLMError:
            return None
        known_ids = {record.evidence_id for record in request.evidence_records}
        return _parse_bullet_output(raw, known_ids)

    @staticmethod
    def _attach_quote(
        candidate: CandidateAttribute,
        records_by_id: dict[str, "EvidenceRecord"],
    ) -> CandidateAttribute:
        """Resolve the exact supporting quote from the first evidence record.

        Uses the evidence text already attached to the retrieved record;
        quotes are derived verbatim or left empty - never invented.
        """
        record = records_by_id.get(candidate.evidence_ids[0])
        if record is None:
            return candidate
        quote = resolve_quote(
            record.text, candidate.normalized_value, candidate.raw_value
        )
        if not quote:
            return candidate
        return candidate.model_copy(update={"quote": quote})

    @staticmethod
    def _validate_item(
        item: ExtractionOutputItem, known_ids: set[str]
    ) -> tuple[CandidateAttribute | None, str]:
        """Semantic validation: evidence binding and value presence."""
        used = [eid for eid in item.evidence_ids if eid]
        if not used:
            return (
                None,
                "no evidence_ids: the claimed value is not traceable to any supplied evidence",
            )
        dangling = [eid for eid in used if eid not in known_ids]
        if dangling:
            return (
                None,
                f"dangling evidence id(s) {sorted(set(dangling))}: not among the supplied evidence records",
            )
        if not (item.raw_value.strip() or item.normalized_value.strip()):
            return None, "empty value"
        return (
            CandidateAttribute(
                name=item.name,
                raw_value=item.raw_value.strip(),
                normalized_value=item.normalized_value.strip(),
                unit=item.unit.strip(),
                confidence=item.confidence,
                evidence_ids=used,
                notes=item.notes.strip()[:MAX_NOTE_CHARS],
            ),
            "",
        )


def to_domain_attribute_values(
    response: ExtractionResponse,
) -> dict[str, AttributeValue]:
    """Group accepted candidates by name into domain AttributeValue records.

    Conflicts are NOT resolved: multiple distinct candidate values produce
    ConflictStatus.CONFLICT so the future validation stage can act on them.
    All evidence_refs point at evidence ids supplied in the request.
    """
    grouped: dict[str, list[CandidateAttribute]] = {}
    for attribute in response.attributes:
        grouped.setdefault(attribute.name, []).append(attribute)

    result: dict[str, AttributeValue] = {}
    for name, candidates in grouped.items():
        first = candidates[0]
        distinct_values = {candidate.raw_value for candidate in candidates}
        conflict_status = (
            ConflictStatus.CONFLICT
            if len(distinct_values) > 1
            else ConflictStatus.AGREEMENT
        )
        result[name] = AttributeValue(
            name=name,
            raw_value=first.raw_value,
            value=first.normalized_value,
            unit=first.unit,
            confidence=first.confidence,
            status=AttributeStatus.EXTRACTED,
            evidence_refs=[
                eid for candidate in candidates for eid in candidate.evidence_ids
            ],
            candidates=[
                CandidateValue(
                    value=candidate.raw_value,
                    normalized_value=candidate.normalized_value,
                    unit=candidate.unit,
                    confidence=candidate.confidence,
                    evidence_refs=candidate.evidence_ids,
                )
                for candidate in candidates
            ],
            conflict_status=conflict_status,
        )
    return result
