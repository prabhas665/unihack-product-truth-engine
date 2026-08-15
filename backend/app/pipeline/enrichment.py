"""Single-product enrichment pipeline orchestration (Step 6D).

Chains the existing components end-to-end for ONE product:

    UniHack input row -> ProductIdentity
        -> source discovery (providers) -> SourcePolicy -> ranking
        -> evidence retrieval (HTML/PDF fetchers)
        -> AI extraction (LLM via the existing ExtractionService)
        -> normalization + validation (existing ValidationService)
        -> ProductIntelligence aggregate
        -> 252-column UniHack delivery row (existing DeliveryMapper)

and returns a typed, fully reviewable EnrichmentResult. Every stage is
tracked with a StageStatus (PENDING/RUNNING/COMPLETED/FAILED/SKIPPED); a run
never raises for pipeline failures - problems surface as FAILED/SKIPPED
stages plus review reasons so a human can review instead of losing the run.
Facts are never invented: missing LLM configuration, empty discovery, failed
fetches, and unavailable official data all stay visible in the result.

This orchestrator lives alongside (not inside) the transformation-stage
registry in app.pipeline.base: that registry is for ProductIntelligence
record transformations, while this service moves artifacts between several
distinct components (discovery result, evidence records, extraction
response, validation summary, delivery row).
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable

import httpx
from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import settings

from app.core.domain import (
    AttributeValue,
    Descriptions,
    ProductIdentity,
    ProductIntelligence,
    ProcessingError,
    ProcessingMetadata,
    ProcessingStatus,
    QualityScore,
    ConfidenceSummary,
)
from app.core.domain.common import utcnow
from app.descriptions import (
    DescriptionsService,
    apply_grounding,
    apply_description_rules,
    has_any_content,
)
from app.identity.mapping import VerifiedBrandLookup, resolve_verified_identity
from app.extraction import (
    ExtractionError,
    ExtractionRequest,
    ExtractionResponse,
    ExtractionService,
)
from app.extraction.selection import select_extraction_evidence
from app.llm import (
    LLMClient,
    LLMConfigurationError,
    LLMError,
    LLMTimeoutError,
    get_client,
)
from app.sources.candidates import SourceCandidate
from app.sources.discovery import (
    DiscoveryContext,
    DiscoveryResult,
    SourceProvider,
    run_discovery,
)
from app.sources.retrieval import (
    EvidenceRecord,
    Fetcher,
    RetrievalLimits,
    RetrievalStatus,
    default_fetchers,
    retrieve_candidate,
)
from app.sources.retrieval.html import HtmlFetcher
from app.sources.retrieval.pdf import PdfFetcher
from app.unihack.mapper import UniHackDeliveryMapper
from app.unihack.models import DeliveryRow, UniHackInputRow
from app.unihack.parser import INPUT_COLUMNS, UniHackInputParser
from app.unihack.schema import DeliverySchema
from app.unihack.writer import DeliveryCsvWriter
from app.validation.service import ValidationService, to_domain_attribute_value
from app.validation.types import (
    Severity,
    ValidatedAttribute,
    ValidationOutcome,
    ValidationSummary,
)


# Review reason used when description generation cannot complete because the
# LLM provider exceeded its wall-clock timeout. The run must stay usable: the
# description fields are left blank (never fabricated) and the stage is marked
# NEEDS_REVIEW rather than FAILED so the rest of the result survives.
DESCRIPTION_TIMEOUT_REASON = "Description generation unavailable: OpenRouter timeout."


class StageName(str, Enum):
    INPUT = "input"
    DISCOVERY = "discovery"
    RETRIEVAL = "retrieval"
    EXTRACTION = "extraction"
    VALIDATION = "validation"
    DESCRIPTION = "description"
    PRODUCT_INTELLIGENCE = "product_intelligence"
    DELIVERY = "delivery"


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NEEDS_REVIEW = "needs_review"


_STAGE_ORDER: list[StageName] = [
    StageName.INPUT,
    StageName.DISCOVERY,
    StageName.RETRIEVAL,
    StageName.EXTRACTION,
    StageName.VALIDATION,
    StageName.DESCRIPTION,
    StageName.PRODUCT_INTELLIGENCE,
    StageName.DELIVERY,
]


class StageState(BaseModel):
    """One pipeline stage with its final status and a human note."""

    stage: StageName
    status: StageStatus = StageStatus.PENDING
    note: str = ""


class EnrichmentRequest(BaseModel):
    """The six official UniHack input fields for one product.

    Field names match the official CSV columns verbatim; placeholders keep
    their official meaning (see app/unihack/parser.py). At least one field
    must be non-blank.
    """

    Mfg_Part_Num: str = ""
    Part_Desc: str = ""
    E1_Brand: str = ""
    Unilog_Brand: str = ""
    DIB_Brand: str = ""
    Part_Manuf: str = ""
    # Optional operator-confirmed manufacturer-owned source URL (Step 8B).
    # When set, it becomes a PENDING SourceCandidate that still passes
    # through the SourcePolicy; its hostname is used only as this request's
    # manufacturer-domain candidate. The six official input columns above
    # are never affected and this field never enters the CSV row.
    source_url: str = ""

    @field_validator("Mfg_Part_Num", mode="before")
    @classmethod
    def _canonicalize_mpn(cls, value: object) -> str:
        """Use one display/key representation throughout a run and cache."""
        return canonicalize_mpn(value if isinstance(value, str) else "")

    @model_validator(mode="after")
    def _require_some_input(self) -> "EnrichmentRequest":
        if all(not getattr(self, field).strip() for field in REQUEST_FIELDS):
            raise ValueError(
                "enrichment request needs at least one non-blank input field"
            )
        return self

    def to_input_row(self) -> UniHackInputRow:
        """Build the internal input row through the real UniHack parser.

        Reusing the parser keeps placeholder semantics and raw-value
        preservation identical to batch processing.
        """
        buffer = io.StringIO(newline="")
        csv.writer(buffer).writerow(
            [getattr(self, field) for field in REQUEST_FIELDS]
        )
        text = ",".join(INPUT_COLUMNS) + "\n" + buffer.getvalue()
        result = UniHackInputParser().parse_text(text)
        if not result.rows:
            raise ValueError("enrichment request produced no input row")
        return result.rows[0]

    def to_identity(self) -> ProductIdentity:
        return self.to_input_row().to_identity()

    @classmethod
    def from_row(cls, row: UniHackInputRow) -> "EnrichmentRequest":
        """Build a request from a parsed dataset row (batch processing)."""
        return cls(
            Mfg_Part_Num=row.mfg_part_num,
            Part_Desc=row.part_desc,
            E1_Brand=row.e1_brand,
            Unilog_Brand=row.unilog_brand,
            DIB_Brand=row.dib_brand,
            Part_Manuf=row.part_manuf,
        )


REQUEST_FIELDS: list[str] = [
    "Mfg_Part_Num",
    "Part_Desc",
    "E1_Brand",
    "Unilog_Brand",
    "DIB_Brand",
    "Part_Manuf",
]


def canonicalize_mpn(value: str | None) -> str:
    """Canonical MPN for request, identity, delivery, persistence, and reuse."""
    return (value or "").strip().upper()


def _merge_domains(
    registry_domains: list[str] | None,
    request_domains: list[str] | None,
) -> list[str]:
    """Combine curated-registry and request-scoped (source_url) domains.

    Both sets are trusted for the current product: registry domains come from
    the verified manufacturer seed, request domains come from a user-supplied
    source URL. Neither is a global allowlist - trust stays scoped per product.
    """
    seen: set[str] = set()
    out: list[str] = []
    for d in (list(registry_domains or []) + list(request_domains or [])):
        norm = d.strip().lower()
        if norm.startswith("www."):
            norm = norm[4:]
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


class InputRowView(BaseModel):
    """The input row as the parser understood it (raw + semantic values)."""

    row_id: int
    mfg_part_num: str
    part_desc: str
    e1_brand: str
    unilog_brand: str
    dib_brand: str
    part_manuf: str
    mfg_part_num_value: str | None = None
    part_desc_value: str | None = None
    e1_brand_value: str | None = None
    unilog_brand_value: str | None = None
    dib_brand_value: str | None = None
    part_manuf_value: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    mfg_part_num_duplicate: bool = False
    duplicate_group_id: str | None = None

    @classmethod
    def from_row(cls, row: UniHackInputRow) -> "InputRowView":
        return cls(
            row_id=row.row_id,
            mfg_part_num=row.mfg_part_num,
            part_desc=row.part_desc,
            e1_brand=row.e1_brand,
            unilog_brand=row.unilog_brand,
            dib_brand=row.dib_brand,
            part_manuf=row.part_manuf,
            mfg_part_num_value=row.mfg_part_num_value,
            part_desc_value=row.part_desc_value,
            e1_brand_value=row.e1_brand_value,
            unilog_brand_value=row.unilog_brand_value,
            dib_brand_value=row.dib_brand_value,
            part_manuf_value=row.part_manuf_value,
            missing_fields=list(row.missing_fields),
            mfg_part_num_duplicate=row.mfg_part_num_duplicate,
            duplicate_group_id=row.duplicate_group_id,
        )


class DeliveryRowView(BaseModel):
    """The delivery row as a serializable view (values + official headers)."""

    values: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    column_count: int = 0
    headers: list[str] = Field(default_factory=list)

    @classmethod
    def from_delivery_row(
        cls, row: DeliveryRow, schema: DeliverySchema
    ) -> "DeliveryRowView":
        return cls(
            values=list(row.values),
            notes=list(row.notes),
            column_count=row.column_count,
            headers=list(schema.headers),
        )


class EnrichmentResult(BaseModel):
    """Full, reviewable outcome of one enrichment run."""

    request: EnrichmentRequest
    input_row: InputRowView
    processing: ProcessingMetadata
    # Stages in execution order, each with its final status.
    stages: list[StageState] = Field(default_factory=list)
    # The stage reached last (useful for progress display).
    current_stage: StageName = StageName.DELIVERY
    discovery: DiscoveryResult = Field(default_factory=DiscoveryResult)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    extraction: ExtractionResponse | None = None
    validation: ValidationSummary | None = None
    product: ProductIntelligence | None = None
    delivery: DeliveryRowView = Field(default_factory=DeliveryRowView)
    review_reasons: list[str] = Field(default_factory=list)
    quality: QualityScore = Field(default_factory=QualityScore)


class IdentityInvariantError(ValueError):
    """Raised when one response contains more than one product identity."""


def enrichment_identity_errors(
    result: EnrichmentResult, expected_mpn: str | None = None
) -> list[str]:
    """Return every violation of the single-MPN response contract.

    Values must already be canonical, not merely equivalent after trimming or
    case folding. This prevents a legacy payload from being returned with a
    display identity different from the request that selected it.
    """
    target = canonicalize_mpn(
        expected_mpn if expected_mpn is not None else result.request.Mfg_Part_Num
    )
    actuals: dict[str, str | None] = {
        "request.Mfg_Part_Num": result.request.Mfg_Part_Num,
        "input_row.mfg_part_num_value": result.input_row.mfg_part_num_value,
        "product.identity.mpn": (
            result.product.identity.mpn if result.product is not None else None
        ),
    }
    headers = result.delivery.headers
    values = result.delivery.values
    for header in ("Mfg_Part_Num", "PART_NUMBER"):
        try:
            actuals[f"delivery.{header}"] = values[headers.index(header)]
        except (ValueError, IndexError):
            actuals[f"delivery.{header}"] = None

    return [
        f"{field}={value!r} does not equal canonical MPN {target!r}"
        for field, value in actuals.items()
        if value != target
    ]


def require_enrichment_identity(
    result: EnrichmentResult, expected_mpn: str | None = None
) -> None:
    """Fail closed rather than return or persist a cross-product result."""
    errors = enrichment_identity_errors(result, expected_mpn)
    if errors:
        raise IdentityInvariantError("; ".join(errors))


class EnrichmentService:
    """Runs the whole enrichment pipeline for a single product.

    Every external component is injectable so the service is fully testable
    offline: discovery providers, the retrieval path (fetchers/transport/
    limits or a plain retriever callable), the LLM client, the validation
    service, and the delivery schema/mapper. Defaults come from settings and
    the official files, so the API and the manual CLI run the real pipeline.
    """

    def __init__(
        self,
        *,
        providers: list[SourceProvider] | None = None,
        manufacturer_domains: list[str] | None = None,
        retriever: Callable[[SourceCandidate], EvidenceRecord] | None = None,
        fetchers: list[Fetcher] | None = None,
        transport: httpx.BaseTransport | None = None,
        limits: RetrievalLimits | None = None,
        llm_client: LLMClient | None = None,
        validation_service: ValidationService | None = None,
        schema: DeliverySchema | None = None,
        mapper: UniHackDeliveryMapper | None = None,
        verified_lookup: VerifiedBrandLookup | None = None,
    ) -> None:
        # None -> run_discovery picks the settings-configured providers.
        self._providers = providers
        self._manufacturer_domains = list(manufacturer_domains or [])
        self._retriever = retriever
        self._fetchers = fetchers
        self._transport = transport
        self._limits = limits
        self._llm_client = llm_client
        self._validation_service = validation_service or ValidationService()
        self._schema = schema or DeliverySchema.frozen()
        self._mapper = mapper or UniHackDeliveryMapper(self._schema)
        self._verified_lookup = verified_lookup or VerifiedBrandLookup.default()

    # -- public API --------------------------------------------------------

    def run(
        self,
        request: EnrichmentRequest,
        *,
        output_path: str | Path | None = None,
        on_stage: Callable[[StageName, StageStatus], None] | None = None,
    ) -> EnrichmentResult:
        """Run the pipeline; returns a reviewable result, never raises.

        ``output_path`` optionally writes the 252-column delivery row there.
        ``on_stage`` is an optional transition observer (stage, status).
        """
        states: dict[StageName, StageState] = {
            stage: StageState(stage=stage) for stage in _STAGE_ORDER
        }
        review_reasons: list[str] = []
        errors: list[ProcessingError] = []
        started = utcnow()

        def mark(name: StageName, status: StageStatus, note: str = "") -> None:
            states[name] = StageState(stage=name, status=status, note=note)
            if on_stage is not None:
                on_stage(name, status)
            if status == StageStatus.FAILED and note:
                errors.append(
                    ProcessingError(
                        stage=name.value,
                        message=note,
                        occurred_at=utcnow(),
                        retryable=True,
                    )
                )

        # -- input ----------------------------------------------------------
        mark(StageName.INPUT, StageStatus.RUNNING)
        input_row = request.to_input_row()
        mark(StageName.INPUT, StageStatus.COMPLETED)

        # -- verified identity (BEFORE discovery) -------------------------
        # Resolve the OEM identity (MANUFACTURER_NAME/BRAND_NAME/TRADE_NAME)
        # from trusted sources only; never from raw input placeholders.
        # Resolved up-front so discovery can scope trusted manufacturer
        # domains to the verified manufacturer (no global domain trust).
        raw_identity = input_row.to_identity()
        verified = resolve_verified_identity(
            raw_identity.mpn,
            raw_identity.brand,
            input_row.dib_brand_value or "",
            input_row.part_manuf_value or "",
            self._verified_lookup,
        )
        discovery_product = raw_identity.model_copy(
            update={
                "verified_manufacturer": verified.manufacturer,
                "verified_brand": verified.brand,
                "verified_trade_name": verified.trade_name,
                "identity_provenance": verified.provenance,
                "manufacturer": verified.manufacturer or raw_identity.manufacturer,
                "brand": verified.brand or raw_identity.brand,
            }
        )
        registry_domains = self._verified_lookup.domains_for(
            raw_identity.mpn,
            raw_identity.brand,
            input_row.dib_brand_value or "",
            input_row.part_manuf_value or "",
        )
        merged_domains = _merge_domains(registry_domains, self._manufacturer_domains)

        # -- discovery ------------------------------------------------------
        mark(StageName.DISCOVERY, StageStatus.RUNNING)
        discovery = run_discovery(
            product=discovery_product,
            providers=self._providers,
            context=DiscoveryContext(
                product=discovery_product,
                manufacturer_domains=merged_domains,
            ),
        )
        for provider_error in discovery.provider_errors:
            review_reasons.append(
                f"discovery provider {provider_error.provider_name}: "
                f"{provider_error.error_kind}: {provider_error.message}"
            )
        for rejected in discovery.rejected:
            review_reasons.append(
                f"rejected candidate {rejected.url}: {rejected.rejection_reason}"
            )

        # -- verified identity (post-discovery, idempotent) ---------------
        discovery.product.verified_manufacturer = verified.manufacturer
        discovery.product.verified_brand = verified.brand
        discovery.product.verified_trade_name = verified.trade_name
        discovery.product.identity_provenance = verified.provenance
        if verified.provenance:
            review_reasons.append(
                f"verified identity ({verified.provenance}): "
                f"manufacturer={verified.manufacturer or '-'}, "
                f"brand={verified.brand or '-'}"
            )
        else:
            review_reasons.append(
                "verified identity: none found; MANUFACTURER_NAME/BRAND_NAME "
                "left blank (no trusted source)"
            )

        mark(
            StageName.DISCOVERY,
            StageStatus.COMPLETED,
            f"{len(discovery.candidates)} allowed of {discovery.total_discovered} discovered",
        )

        # -- retrieval + extraction + validation ----------------------------
        evidence: list[EvidenceRecord] = []
        usable: list[EvidenceRecord] = []
        extraction_evidence: list[EvidenceRecord] = []
        extraction: ExtractionResponse | None = None
        validation: ValidationSummary | None = None

        if not discovery.candidates:
            note = "no allowed source candidates: nothing to retrieve"
            mark(StageName.RETRIEVAL, StageStatus.RUNNING)
            mark(StageName.RETRIEVAL, StageStatus.SKIPPED, note)
            review_reasons.append(
                f"{note}; extraction and validation skipped as well"
            )
            mark(StageName.EXTRACTION, StageStatus.RUNNING)
            mark(
                StageName.EXTRACTION,
                StageStatus.SKIPPED,
                "no successfully retrieved evidence",
            )
            mark(StageName.VALIDATION, StageStatus.RUNNING)
            mark(
                StageName.VALIDATION,
                StageStatus.SKIPPED,
                "no extracted attributes",
            )
        else:
            mark(StageName.RETRIEVAL, StageStatus.RUNNING)
            evidence, retriever_failures = self._retrieve(discovery.candidates)
            for failure in retriever_failures:
                review_reasons.append(f"retrieval failed for {failure}")
            mark(
                StageName.RETRIEVAL,
                StageStatus.COMPLETED,
                f"retrieved {len(discovery.candidates)} candidate(s)",
            )

            usable = [
                record
                for record in evidence
                if record.retrieval_status == RetrievalStatus.SUCCESS
                and record.text.strip()
            ]
            # STEP 20: narrow the extraction evidence to the requested MPN and
            # enforce a hard context budget. Sibling manufacturer pages that
            # describe a DIFFERENT product (and never mention the requested
            # MPN) are dropped, and the remaining MPN-relevant records are kept
            # in priority order until the budget is reached. This keeps the
            # extraction prompt small (so a slow free-tier model cannot be
            # starved/timeout) and prevents cross-product attribute
            # contamination. The full evidence set is still used for delivery
            # and the evidence map; only the LLM input is filtered.
            selection = select_extraction_evidence(
                discovery.product,
                usable,
                budget_chars=settings.extraction_context_budget_chars,
            )
            extraction_evidence = selection.selected
            for reason in selection.dropped:
                review_reasons.append(reason)
            mark(StageName.EXTRACTION, StageStatus.RUNNING)
            extraction, extraction_note, extraction_reasons = (
                self._extract(
                    discovery.product,
                    input_row,
                    extraction_evidence,
                    review_reasons,
                )
            )
            review_reasons.extend(extraction_reasons)
            if extraction is not None:
                mark(StageName.EXTRACTION, StageStatus.COMPLETED, extraction_note)
                mark(StageName.VALIDATION, StageStatus.RUNNING)
                validation = self._validation_service.validate(
                    extraction.attributes,
                    {record.evidence_id for record in extraction_evidence},
                )
                mark(
                    StageName.VALIDATION,
                    StageStatus.COMPLETED,
                    f"{len(validation.attributes)} attribute(s) validated",
                )
                for attribute in validation.attributes:
                    for message in attribute.messages:
                        if message.severity in (Severity.WARNING, Severity.ERROR):
                            review_reasons.append(
                                f"validation {attribute.name!r}: "
                                f"{message.code}: {message.message}"
                            )
            else:
                mark(
                    StageName.EXTRACTION,
                    StageStatus.SKIPPED if extraction_note.startswith("skipped")
                    else StageStatus.FAILED,
                    extraction_note,
                )
                mark(StageName.VALIDATION, StageStatus.RUNNING)
                mark(
                    StageName.VALIDATION,
                    StageStatus.SKIPPED,
                    "no extracted attributes",
                )

        # -- description generation -----------------------------------------
        validated = validation.attributes if validation is not None else []
        mark(StageName.DESCRIPTION, StageStatus.RUNNING)
        descriptions, desc_note, desc_reasons, desc_hint = (
            self._generate_descriptions(
                discovery.product,
                validated,
                extraction_evidence,
            )
        )
        review_reasons.extend(desc_reasons)
        if descriptions is not None:
            if desc_hint is not None:
                mark(StageName.DESCRIPTION, desc_hint, desc_note)
            else:
                mark(StageName.DESCRIPTION, StageStatus.COMPLETED, desc_note)
        else:
            # A hint can arrive even without generated descriptions (e.g. the
            # LLM timed out): honor it so the timeout is non-fatal and the
            # stage becomes NEEDS_REVIEW instead of FAILED.
            if desc_hint is not None:
                mark(StageName.DESCRIPTION, desc_hint, desc_note)
            else:
                mark(
                    StageName.DESCRIPTION,
                    StageStatus.SKIPPED if desc_note.startswith("skipped")
                    else StageStatus.FAILED,
                    desc_note,
                )

        # -- product intelligence -------------------------------------------
        mark(StageName.PRODUCT_INTELLIGENCE, StageStatus.RUNNING)
        attributes: dict[str, AttributeValue] = {
            attribute.name: to_domain_attribute_value(attribute)
            for attribute in validated
        }
        product = ProductIntelligence(
            identity=discovery.product,
            attributes=attributes,
            evidence={
                record.evidence_id: record.to_domain_evidence()
                for record in evidence
                if record.retrieval_status == RetrievalStatus.SUCCESS
            },
            descriptions=descriptions or Descriptions(),
            quality=self._build_quality(validated),
            processing=ProcessingMetadata(
                status=ProcessingStatus.PENDING,
                created_at=started,
                updated_at=utcnow(),
                errors=list(errors),
            ),
        )
        mark(
            StageName.PRODUCT_INTELLIGENCE,
            StageStatus.COMPLETED,
            f"{len(product.attributes)} attribute(s), {len(product.evidence)} evidence record(s)",
        )

        # -- delivery -------------------------------------------------------
        mark(StageName.DELIVERY, StageStatus.RUNNING)
        delivery_row = self._mapper.map(product, input_row)
        if output_path is not None:
            target = Path(output_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            DeliveryCsvWriter(self._schema).write_path(target, [delivery_row])
        review_reasons.extend(
            f"delivery: {note}" for note in delivery_row.notes
        )
        mark(
            StageName.DELIVERY,
            StageStatus.COMPLETED,
            f"{delivery_row.column_count} columns",
        )

        # Processing status reflects the execution of the pipeline itself:
        # a FAILED stage means the run did not produce its usual output
        # (e.g. LLM down); SKIPPED stages mean the result is partial and
        # deserves human review; a NEEDS_REVIEW stage (unsupported claims
        # dropped by the grounding guard) also deserves review; otherwise
        # the run completed. Informational review reasons (blank values
        # faithfully mapped from placeholders) do NOT downgrade a completed
        # run.
        failed = any(
            state.status == StageStatus.FAILED for state in states.values()
        )
        skipped = any(
            state.status == StageStatus.SKIPPED for state in states.values()
        )
        needs_review_stage = any(
            state.status == StageStatus.NEEDS_REVIEW
            for state in states.values()
        )
        # A failed fetch means the result was built from incomplete evidence;
        # that deserves a review, just like skipped stages.
        retrieval_failed = any(
            record.retrieval_status == RetrievalStatus.FAILED
            for record in evidence
        )
        status = (
            ProcessingStatus.FAILED
            if failed
            else ProcessingStatus.NEEDS_REVIEW
            if (skipped or needs_review_stage or retrieval_failed)
            else ProcessingStatus.COMPLETED
        )

        product.processing.status = status
        product.processing.updated_at = utcnow()
        current_stage = next(
            (
                stage
                for stage in reversed(_STAGE_ORDER)
                if states[stage].status != StageStatus.PENDING
            ),
            StageName.DELIVERY,
        )

        final_result = EnrichmentResult(
            request=request,
            input_row=InputRowView.from_row(input_row),
            processing=product.processing,
            stages=[states[stage] for stage in _STAGE_ORDER],
            current_stage=current_stage,
            discovery=discovery,
            evidence=evidence,
            extraction=extraction,
            validation=validation,
            product=product,
            delivery=DeliveryRowView.from_delivery_row(delivery_row, self._schema),
            review_reasons=review_reasons,
            quality=product.quality,
        )
        require_enrichment_identity(final_result)
        return final_result

    # -- stage internals ----------------------------------------------------

    def _retrieve(
        self, candidates: list[SourceCandidate]
    ) -> tuple[list[EvidenceRecord], list[str]]:
        """Fetch every allowed candidate; returns (records, failure notes)."""
        retriever = self._retriever or self._default_retriever()
        records: list[EvidenceRecord] = []
        failures: list[str] = []
        for candidate in candidates:
            try:
                record = retriever(candidate)
            except Exception as exc:  # a custom retriever must not break the run
                record = EvidenceRecord(
                    source_candidate_id=candidate.id or candidate.url,
                    url=candidate.url,
                    source_type=candidate.source_type,
                    retrieval_status=RetrievalStatus.FAILED,
                    error_message=f"retriever raised: {exc}",
                )
            records.append(record)
            if record.retrieval_status == RetrievalStatus.FAILED:
                failures.append(
                    f"{record.url}: {record.error_message or record.error_kind or 'unknown error'}"
                )
        return records, failures

    def _default_retriever(self) -> Callable[[SourceCandidate], EvidenceRecord]:
        if self._fetchers is not None:
            fetchers = self._fetchers
        elif self._transport is not None:
            fetchers = [
                HtmlFetcher(self._transport),
                PdfFetcher(self._transport),
            ]
        else:
            fetchers = default_fetchers()

        def retriever(candidate: SourceCandidate) -> EvidenceRecord:
            return retrieve_candidate(
                candidate, fetchers=fetchers, limits=self._limits
            )

        return retriever

    def _extract(
        self,
        identity: ProductIdentity,
        input_row: UniHackInputRow,
        usable: list[EvidenceRecord],
        review_reasons: list[str],
    ) -> tuple[ExtractionResponse | None, str, list[str]]:
        """Run AI extraction; returns (response, note, extra review reasons).

        Missing LLM configuration and provider/validation failures turn into
        a FAILED stage with a review reason - never an exception.
        """
        reasons: list[str] = []
        if not usable:
            note = "skipped: no successfully retrieved evidence with extractable text"
            reasons.append(
                "extraction skipped: no successfully retrieved evidence "
                "with extractable text"
            )
            return None, note, reasons

        client = self._llm_client
        if client is None:
            try:
                client = get_client()
            except LLMConfigurationError as exc:
                note = f"failed: LLM not configured ({exc})"
                reasons.append(f"extraction failed: {exc}")
                return None, note, reasons

        service = ExtractionService(client)
        try:
            response = service.extract(
                ExtractionRequest(
                    identity=identity,
                    raw_description=input_row.part_desc_value or "",
                    evidence_records=usable,
                )
            )
        except ExtractionError as exc:
            note = f"failed ({exc.kind.value}): {exc.message}"
            reasons.append(f"extraction failed ({exc.kind.value}): {exc.message}")
            return None, note, reasons

        for rejected in response.rejected:
            reasons.append(
                f"extracted attribute {rejected.name!r} rejected: {rejected.reason}"
            )
        note = (
            f"{len(response.attributes)} accepted, "
            f"{len(response.rejected)} rejected"
        )
        return response, note, reasons

    def _generate_descriptions(
        self,
        identity: ProductIdentity,
        validated: list[ValidatedAttribute],
        usable: list[EvidenceRecord],
    ) -> tuple[Descriptions | None, str, list[str], StageStatus | None]:
        """Generate description variants from the validated attributes.

        Uses ONLY the extracted/validated facts; a missing LLM or a provider
        failure becomes a FAILED stage with a review reason - never an
        exception and never fabricated copy. Generated copy passes through
        the deterministic grounding guard (app.descriptions.grounding): any
        unsupported factual claim (certification, warranty, dimensions,
        material, performance, compatibility, accessory) not backed by the
        identity/attributes/quotes is dropped and its field blanked. The
        returned StageStatus hint tells the caller how to mark the stage:
        NEEDS_REVIEW when only part of the copy was dropped, FAILED when the
        guard left nothing at all.
        """
        reasons: list[str] = []
        if not validated:
            note = "skipped: no extracted attributes to describe"
            reasons.append(
                "description skipped: no extracted attributes to describe"
            )
            return None, note, reasons, None

        client = self._llm_client
        if client is None:
            try:
                client = get_client()
            except LLMConfigurationError as exc:
                note = f"failed: LLM not configured ({exc})"
                reasons.append(f"description generation failed: {exc}")
                return None, note, reasons, None

        service = DescriptionsService(client)
        attributes: dict[str, AttributeValue] = {
            attribute.name: to_domain_attribute_value(attribute)
            for attribute in validated
        }
        quotes = service.evidence_quotes(
            [record.text for record in usable if record.text.strip()]
        )
        try:
            descriptions = service.generate(
                identity=identity,
                attributes=attributes,
                quotes=quotes,
            )
        except LLMTimeoutError as exc:
            # Hard timeout: never fabricate copy and never fail the run.
            # Leave descriptions blank and mark the stage NEEDS_REVIEW so the
            # extracted attributes/evidence and 252-column delivery survive.
            note = DESCRIPTION_TIMEOUT_REASON
            reasons.append(DESCRIPTION_TIMEOUT_REASON)
            return None, note, reasons, StageStatus.NEEDS_REVIEW
        except LLMError as exc:
            note = f"failed ({type(exc).__name__}): {exc}"
            reasons.append(f"description generation failed: {exc}")
            return None, note, reasons, None

        descriptions, grounding_reasons, drops = apply_grounding(
            descriptions,
            identity=identity,
            attributes=attributes,
            quotes=quotes,
        )
        reasons.extend(grounding_reasons)

        descriptions, rule_reasons = apply_description_rules(descriptions)
        reasons.extend(rule_reasons)
        filled = [
            label
            for label, value in (
                ("title", descriptions.product_title),
                ("short", descriptions.short_description),
                ("mobile", descriptions.mobile_description),
                ("invoice", descriptions.invoice_description),
                ("long", descriptions.long_description),
                ("retail", descriptions.retail_description),
                ("marketing", descriptions.marketing_description),
            )
            if value
        ]
        note = (
            f"generated {len(filled)} variant(s): {', '.join(filled)}"
            if filled
            else "generated (all variants empty)"
        )
        if drops:
            note += f"; grounding blanked {drops} unsupported field(s)"
            status_hint = (
                StageStatus.FAILED
                if not has_any_content(descriptions)
                else StageStatus.NEEDS_REVIEW
            )
        else:
            status_hint = None
        return descriptions, note, reasons, status_hint

    def _build_quality(self, validated: list[ValidatedAttribute]) -> QualityScore:
        """Honest, derived quality metrics (never fabricated).

        ``overall`` stays 0.0: the official UniHack quality formula is not
        available yet. Coverage and confidence come from the real validated
        attributes of this run.
        """
        if not validated:
            return QualityScore()
        confidences = [attribute.confidence for attribute in validated]
        checkable = [
            attribute
            for attribute in validated
            if attribute.outcome != ValidationOutcome.NOT_VALIDATED
        ]
        return QualityScore(
            overall=0.0,
            evidence_coverage=(
                sum(1 for a in validated if a.evidence_refs) / len(validated)
            ),
            validation_coverage=len(checkable) / len(validated),
            confidence=ConfidenceSummary(
                count=len(confidences),
                min=min(confidences),
                max=max(confidences),
                mean=sum(confidences) / len(confidences),
            ),
        )
