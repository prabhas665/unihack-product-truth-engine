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

from pydantic import ValidationError

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
    LLMProviderUnavailableError,
    LLMTimeoutError,
    StructuredCompletionRequest,
)

# Notes must stay user-safe and concise (never chain-of-thought).
MAX_NOTE_CHARS = 200
# Bound token use per evidence record.
MAX_CHARS_PER_RECORD = 6000

# Deterministic normalization for textual confidence values a model may emit
# instead of a number. Documented mapping used ONLY when the schema-required
# numeric confidence is missing: the mapped value is a fixed, documented
# constant - never fabricated from the evidence text.
CONFIDENCE_TEXT_MAP = {
    "high": 0.9,
    "medium": 0.6,
    "low": 0.3,
}

# Strict pattern for the bullet-list fallback: "- name: value [<id>[, <id>]]".
_BULLET_LINE_RE = re.compile(
    r"^\s*-\s*(?P<name>[A-Za-z0-9][A-Za-z0-9 _\-/.]*?):\s*(?P<value>.*)$"
)
_BULLET_IDS_RE = re.compile(r"\[([^\]]+)\]")


def _normalize_confidence(value: object) -> tuple[float | None, str | None]:
    """Map a raw confidence value to a schema-valid float.

    Returns (confidence, None) on success and (None, reason) when the
    attribute must be rejected. Numeric 0.0-1.0 passes through unchanged;
    None becomes the schema default 0.0; the documented textual values
    high/medium/low map to CONFIDENCE_TEXT_MAP; everything else (unknown
    strings, out-of-range numbers, bools) is rejected.
    """
    if value is None:
        return 0.0, None
    if isinstance(value, bool):
        return None, (
            f"confidence {value!r} is not a number in 0..1 and not a known "
            f"textual value (high/medium/low)"
        )
    if isinstance(value, (int, float)):
        numeric = float(value)
        if 0.0 <= numeric <= 1.0:
            return numeric, None
        return None, f"confidence {numeric} is outside the valid range 0..1"
    if isinstance(value, str):
        mapped = CONFIDENCE_TEXT_MAP.get(value.strip().lower())
        if mapped is not None:
            return mapped, None
        return None, (
            f"confidence {value!r} is not a number in 0..1 and not a known "
            f"textual value (high/medium/low)"
        )
    return None, (
        f"confidence {value!r} is not a number in 0..1 and not a known "
        f"textual value (high/medium/low)"
    )


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
    """Extracts evidence-bound candidate attributes via an LLM client.

    An optional ``fallback_client`` (Step LLM-8) is used ONLY when the
    primary call fails with a timeout or provider-unavailability: each
    attempt has its own bounded timeout, schema-invalid responses are
    handled locally (never by failover), and when both clients fail the
    caller receives the same typed ExtractionError it gets today (the
    pipeline maps it to NEEDS_REVIEW with evidence preserved).
    """

    def __init__(
        self,
        client: LLMClient,
        fallback_client: LLMClient | None = None,
        fallback_timeout_seconds: float | None = None,
    ) -> None:
        self._client = client
        self._fallback_client = fallback_client
        self._fallback_timeout_seconds = fallback_timeout_seconds

    def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        """Run one extraction over the supplied evidence records.

        Raises ExtractionError when the LLM output is unusable as a whole
        (malformed JSON, schema violations such as confidence outside 0..1,
        or provider failure). Evidence-binding problems are reported per
        attribute via `rejected`.

        A timeout or provider-unavailability is retried once against the
        optional fallback client (same evidence, same prompt, its own
        bounded timeout); schema-invalid application data never triggers a
        failover. When both attempts fail, ExtractionError(LLM_FAILED) is
        raised with the fallback error as the cause - the pipeline then
        marks the stage NEEDS_REVIEW without fabricating anything.

        A partially valid structured response (one malformed attribute among
        valid ones) is salvaged per attribute: valid items are kept with
        their confidence normalized (see CONFIDENCE_TEXT_MAP), and only the
        malformed items are rejected. This never triggers a second LLM call;
        the bullet-list fallback is used only when no JSON items are
        available at all.
        """
        known_ids = {record.evidence_id for record in request.evidence_records}
        records_by_id = {
            record.evidence_id: record for record in request.evidence_records
        }
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
        except (LLMTimeoutError, LLMProviderUnavailableError) as exc:
            if self._fallback_client is None:
                raise ExtractionError(
                    ExtractionErrorKind.LLM_FAILED, f"LLM call failed: {exc}"
                ) from exc
            try:
                output = self._fallback_client.structured_completion(
                    StructuredCompletionRequest(
                        system_prompt=SYSTEM_PROMPT,
                        user_prompt=prompt,
                        output_schema=ExtractionOutput,
                        timeout_seconds=self._fallback_timeout_seconds,
                    )
                )
            except LLMError as fallback_exc:
                raise ExtractionError(
                    ExtractionErrorKind.LLM_FAILED,
                    f"LLM call failed on both the primary and the fallback "
                    f"model: {exc}; fallback: {fallback_exc}",
                ) from fallback_exc
        except LLMInvalidResponseError as exc:
            raw_items = exc.raw.get("items") if isinstance(exc.raw, dict) else None
            if isinstance(raw_items, list):
                return self._salvage_items(raw_items, known_ids, records_by_id)
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

    def _salvage_items(
        self,
        raw_items: list[object],
        known_ids: set[str],
        records_by_id: dict[str, "EvidenceRecord"],
    ) -> ExtractionResponse:
        """Recover usable attributes from a partially invalid response.

        Every raw item is normalized and validated on its own: a confidence
        that is numeric 0.0-1.0 is kept unchanged, the documented textual
        values high/medium/low are mapped to CONFIDENCE_TEXT_MAP, and any
        other confidence (unknown string, out-of-range number, bool) rejects
        ONLY that attribute. Items failing the item schema or evidence
        binding are rejected individually; valid items are preserved
        verbatim (name, raw_value, normalized_value, unit, evidence_ids,
        notes) - nothing is fabricated.
        """
        attributes: list[CandidateAttribute] = []
        rejected: list[RejectedAttribute] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                rejected.append(
                    RejectedAttribute(
                        name="",
                        raw_value="",
                        reason="malformed attribute entry: not a JSON object",
                    )
                )
                continue
            name = str(raw_item.get("name", "")).strip()
            raw_value = str(raw_item.get("raw_value", ""))
            confidence, conf_reason = _normalize_confidence(raw_item.get("confidence"))
            if conf_reason is not None:
                rejected.append(
                    RejectedAttribute(name=name, raw_value=raw_value, reason=conf_reason)
                )
                continue
            if confidence is not None:
                raw_item = dict(raw_item)
                raw_item["confidence"] = confidence
            try:
                item = ExtractionOutputItem.model_validate(raw_item)
            except ValidationError as exc:
                rejected.append(
                    RejectedAttribute(
                        name=name,
                        raw_value=raw_value,
                        reason=f"attribute failed schema validation: {exc}",
                    )
                )
                continue
            candidate, reason = self._validate_item(item, known_ids)
            if candidate is not None:
                attributes.append(self._attach_quote(candidate, records_by_id))
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
