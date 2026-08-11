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
from pydantic import BaseModel, Field, model_validator

from app.core.domain import (
    AttributeValue,
    ProductIdentity,
    ProductIntelligence,
    ProcessingError,
    ProcessingMetadata,
    ProcessingStatus,
    QualityScore,
    ConfidenceSummary,
)
from app.core.domain.common import utcnow
from app.extraction import (
    ExtractionError,
    ExtractionRequest,
    ExtractionResponse,
    ExtractionService,
)
from app.llm import LLMClient, LLMConfigurationError, get_client
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
from app.unihack.paths import delivery_reference_path
from app.unihack.schema import DeliverySchema
from app.unihack.writer import DeliveryCsvWriter
from app.validation.service import ValidationService, to_domain_attribute_value
from app.validation.types import (
    Severity,
    ValidatedAttribute,
    ValidationOutcome,
    ValidationSummary,
)


class StageName(str, Enum):
    INPUT = "input"
    DISCOVERY = "discovery"
    RETRIEVAL = "retrieval"
    EXTRACTION = "extraction"
    VALIDATION = "validation"
    PRODUCT_INTELLIGENCE = "product_intelligence"
    DELIVERY = "delivery"


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


_STAGE_ORDER: list[StageName] = [
    StageName.INPUT,
    StageName.DISCOVERY,
    StageName.RETRIEVAL,
    StageName.EXTRACTION,
    StageName.VALIDATION,
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


REQUEST_FIELDS: list[str] = [
    "Mfg_Part_Num",
    "Part_Desc",
    "E1_Brand",
    "Unilog_Brand",
    "DIB_Brand",
    "Part_Manuf",
]


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
        self._schema = schema or DeliverySchema.from_reference_csv(
            delivery_reference_path()
        )
        self._mapper = mapper or UniHackDeliveryMapper(self._schema)

    # -- public API --------------------------------------------------------

    def run(
        self,
        request: EnrichmentRequest,
        *,
        output_path: str | Path | None = None,
        on_stage: Callable[[StageName, StageStatus], None] | None = None,
    ) -> EnrichmentResult:
        """Run the pipeline; returns a reviewable result, never raises.

        ``output_path`` optionally writes the 252-column delivery row there
        (the official reference file is always refused). ``on_stage`` is an
        optional transition observer (stage, status).
        """
        if output_path is not None and Path(output_path).resolve() == delivery_reference_path().resolve():
            raise ValueError(
                "refusing to overwrite the official delivery reference file"
            )

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

        # -- discovery ------------------------------------------------------
        mark(StageName.DISCOVERY, StageStatus.RUNNING)
        discovery = run_discovery(
            product=input_row.to_identity(),
            providers=self._providers,
            context=DiscoveryContext(
                product=input_row.to_identity(),
                manufacturer_domains=self._manufacturer_domains,
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
        mark(
            StageName.DISCOVERY,
            StageStatus.COMPLETED,
            f"{len(discovery.candidates)} allowed of {discovery.total_discovered} discovered",
        )

        # -- retrieval + extraction + validation ----------------------------
        evidence: list[EvidenceRecord] = []
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
            mark(StageName.EXTRACTION, StageStatus.RUNNING)
            extraction, extraction_note, extraction_reasons = (
                self._extract(
                    discovery.product,
                    input_row,
                    usable,
                    review_reasons,
                )
            )
            review_reasons.extend(extraction_reasons)
            if extraction is not None:
                mark(StageName.EXTRACTION, StageStatus.COMPLETED, extraction_note)
                mark(StageName.VALIDATION, StageStatus.RUNNING)
                validation = self._validation_service.validate(
                    extraction.attributes,
                    {record.evidence_id for record in usable},
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

        # -- product intelligence -------------------------------------------
        mark(StageName.PRODUCT_INTELLIGENCE, StageStatus.RUNNING)
        validated = validation.attributes if validation is not None else []
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
        # deserves human review; otherwise the run completed. Informational
        # review reasons (blank values faithfully mapped from placeholders)
        # do NOT downgrade a completed run.
        failed = any(
            state.status == StageStatus.FAILED for state in states.values()
        )
        skipped = any(
            state.status == StageStatus.SKIPPED for state in states.values()
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
            if (skipped or retrieval_failed)
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

        return EnrichmentResult(
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
