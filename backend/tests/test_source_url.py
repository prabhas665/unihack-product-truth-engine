"""Tests for the optional manufacturer source URL (Step 8B).

Covers the request contract, request-scoped ManualUrlProvider injection
(never global), hostname derivation, SourcePolicy still deciding
ALLOWED/REJECTED (marketplace rejection first), retrieval refusing anything
that is not ALLOWED, and unchanged behavior without a URL.

Everything is offline: no real network calls and no real LLM provider.
TEST FIXTURES: made-up URLs (makitatools.example / acme.example) used ONLY
to exercise provider -> policy -> retrieval logic; not real manufacturer
data.
"""

from __future__ import annotations

import json

from app.api.routes.enrich import build_manual_source
from app.config import settings
from app.core.domain import (
    ProcessingStatus,
    ProductIdentity,
    SourceTrustLevel,
    SourceType,
)
from app.extraction import ExtractionOutput, ExtractionOutputItem
from app.llm import LLMClient
from app.pipeline.enrichment import (
    EnrichmentRequest,
    EnrichmentService,
    StageStatus,
)
from app.sources import (
    CandidateStatus,
    DiscoveryContext,
    ManufacturerRelationship,
    run_discovery,
)
from app.sources.candidates import SourceCandidate
from app.sources.providers.manual_url import ManualUrlProvider
from app.sources.retrieval import (
    EvidenceRecord,
    ExtractionStatus,
    RetrievalStatus,
)
from app.unihack.mapper import UniHackDeliveryMapper
from app.unihack.paths import delivery_fixture_path
from app.unihack.schema import DeliverySchema

MANUF_URL = "https://makitatools.example/products/details/XLC10ZW"
AMAZON_URL = "https://www.amazon.com/dp/B0TEST"
UNKNOWN_URL = "https://random-site.example/part"


def make_product() -> ProductIdentity:
    return ProductIdentity(
        manufacturer="Makita Usa Inc",
        brand="Makita",
        mpn="XLC10ZW",
        raw_description="XLC10ZW Makita 18V Cordless Vacuum (Bare)",
    )


def demo_request(source_url: str = "") -> EnrichmentRequest:
    return EnrichmentRequest(
        Mfg_Part_Num="XLC10ZW",
        Part_Desc="XLC10ZW Makita 18V Cordless Vacuum (Bare)",
        E1_Brand="-- Unbranded --",
        Unilog_Brand="-- No Unilog Brand --",
        DIB_Brand="-- No DIB Brand --",
        Part_Manuf="Makita Usa Inc (5142)",
        source_url=source_url,
    )


def make_evidence(
    url: str, *, evidence_id: str, text: str
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        source_candidate_id=f"cand-{evidence_id}",
        url=url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        title=url,
        text=text,
        content_type="text/html",
        retrieval_status=RetrievalStatus.SUCCESS,
        extraction_status=ExtractionStatus.EXTRACTED,
    )


class RecordingRetriever:
    """Returns canned records and records every candidate it is given.

    Raising instead of fetching proves retrieval never sees non-ALLOWED
    candidates: the pipeline passes only discovery.candidates to it.
    """

    def __init__(self, records: list[EvidenceRecord]) -> None:
        self.by_url = {record.url: record for record in records}
        self.calls: list[str] = []

    def __call__(self, candidate: SourceCandidate) -> EvidenceRecord:
        self.calls.append(candidate.url)
        return self.by_url[candidate.url]


class FakeLLMClient(LLMClient):
    """Offline LLM client returning canned JSON output."""

    provider = "fake"

    def __init__(self, output: ExtractionOutput) -> None:
        self._output = output

    def _complete(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        temperature: float | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        return json.dumps(self._output.model_dump())


def service_for(
    request: EnrichmentRequest,
    *,
    records: list[EvidenceRecord],
    llm_output: ExtractionOutput,
    retriever: RecordingRetriever | None = None,
) -> tuple[EnrichmentService, RecordingRetriever]:
    providers, manufacturer_domains = build_manual_source(
        request.source_url, request.Part_Desc
    )
    recorded = retriever or RecordingRetriever(records)
    schema = DeliverySchema.from_reference_csv(delivery_fixture_path())
    return (
        EnrichmentService(
            providers=providers,
            manufacturer_domains=manufacturer_domains,
            retriever=recorded,
            llm_client=FakeLLMClient(llm_output),
            schema=schema,
            mapper=UniHackDeliveryMapper(schema),
        ),
        recorded,
    )


def belt_attributes(evidence_id: str, text: str) -> ExtractionOutput:
    return ExtractionOutput(
        items=[
            ExtractionOutputItem(
                name="belt_width",
                raw_value="0.5 inch",
                normalized_value="0.5 inch",
                unit="in",
                confidence=0.9,
                evidence_ids=[evidence_id],
                notes="stated on the product page",
            )
        ]
    )


PAGE_TEXT = (
    "The XLC10ZW cordless vacuum includes a 0.5 inch wide belt "
    "assembly with a dust canister."
)


# --------------------------------------------------------------------------
# request contract
# --------------------------------------------------------------------------


class TestSourceUrlRequestContract:
    def test_source_url_defaults_to_blank(self):
        request = demo_request()
        assert request.source_url == ""
        assert all(
            getattr(request, field)
            for field in (
                "Mfg_Part_Num",
                "Part_Desc",
                "E1_Brand",
                "Unilog_Brand",
                "DIB_Brand",
                "Part_Manuf",
            )
        )

    def test_source_url_is_optional_and_echoed_in_the_result(self):
        service, _ = service_for(
            demo_request(MANUF_URL),
            records=[
                make_evidence(
                    MANUF_URL, evidence_id="ev-manual-0001", text=PAGE_TEXT
                )
            ],
            llm_output=belt_attributes("ev-manual-0001", PAGE_TEXT),
        )
        result = service.run(demo_request(MANUF_URL))
        assert result.request.source_url == MANUF_URL
        assert result.input_row.mfg_part_num == "XLC10ZW"

    def test_source_url_never_enters_the_input_csv_row(self):
        request = demo_request(MANUF_URL)
        input_row = request.to_input_row()
        assert input_row.mfg_part_num == "XLC10ZW"
        assert input_row.part_manuf == "Makita Usa Inc (5142)"


# --------------------------------------------------------------------------
# request-scoped injection + hostname derivation
# --------------------------------------------------------------------------


class TestBuildManualSource:
    def test_absent_url_keeps_default_discovery(self):
        assert build_manual_source("") == (None, None)
        assert build_manual_source("   ") == (None, None)

    def test_valid_url_injects_provider_and_derives_hostname(self):
        providers, domains = build_manual_source(MANUF_URL, title="part")
        assert domains == ["makitatools.example"]
        assert len(providers) == 1
        assert providers[0].name == "manual_url"
        candidates = providers[0].discover(
            make_product(), DiscoveryContext(product=make_product())
        )
        assert len(candidates) == 1
        assert candidates[0].url == MANUF_URL
        assert candidates[0].status == CandidateStatus.PENDING
        assert candidates[0].domain == "makitatools.example"
        assert candidates[0].title == "part"

    def test_www_prefix_is_stripped_from_derived_domain(self):
        _, domains = build_manual_source(
            "https://www.makitatools.example/details/XLC10ZW"
        )
        assert domains == ["makitatools.example"]

    def test_missing_or_invalid_url_is_safe(self):
        providers, domains = build_manual_source("not a url")
        assert domains == []
        assert len(providers) == 1
        assert providers[0].discover(
            make_product(), DiscoveryContext(product=make_product())
        ) == []

    def test_non_http_scheme_emits_no_candidate(self):
        providers, _ = build_manual_source("ftp://makitatools.example/file.pdf")
        assert len(providers) == 1
        assert providers[0].discover(
            make_product(), DiscoveryContext(product=make_product())
        ) == []

    def test_manual_url_provider_is_never_registered_globally(self):
        from app.sources.discovery import PROVIDERS

        assert all(
            provider.name != "manual_url" for provider in PROVIDERS
        )


# --------------------------------------------------------------------------
# SourcePolicy still decides (url never bypasses the policy)
# --------------------------------------------------------------------------


class TestSourceUrlThroughPolicy:
    def test_manufacturer_domain_allows_the_manual_url(self):
        providers, domains = build_manual_source(MANUF_URL)
        result = run_discovery(
            make_product(),
            providers=providers,
            context=DiscoveryContext(
                product=make_product(), manufacturer_domains=domains
            ),
        )
        assert result.total_discovered == 1
        assert len(result.candidates) == 1
        candidate = result.candidates[0]
        assert candidate.status == CandidateStatus.ALLOWED
        assert (
            candidate.manufacturer_relationship
            == ManufacturerRelationship.OWNED
        )
        assert (
            candidate.trust_level == SourceTrustLevel.MANUFACTURER_OFFICIAL
        )

    def test_marketplace_url_is_prohibited_even_with_derived_domain(self):
        providers, domains = build_manual_source(AMAZON_URL)
        assert domains == ["amazon.com"]
        result = run_discovery(
            make_product(),
            providers=providers,
            context=DiscoveryContext(
                product=make_product(), manufacturer_domains=domains
            ),
        )
        assert result.candidates == []
        assert len(result.rejected) == 1
        rejected = result.rejected[0]
        assert rejected.status == CandidateStatus.PROHIBITED
        assert "marketplace" in rejected.rejection_reason

    def test_unknown_external_domain_is_rejected(self):
        providers, _ = build_manual_source(UNKNOWN_URL)
        result = run_discovery(
            make_product(),
            providers=providers,
            context=DiscoveryContext(product=make_product()),
        )
        assert result.candidates == []
        assert len(result.rejected) == 1
        assert result.rejected[0].status == CandidateStatus.REJECTED
        assert "unknown external domain" in result.rejected[0].rejection_reason

    def test_manual_url_candidate_starts_pending_before_policy(self):
        provider = ManualUrlProvider(MANUF_URL)
        candidates = provider.discover(
            make_product(), DiscoveryContext(product=make_product())
        )
        assert candidates[0].status == CandidateStatus.PENDING


# --------------------------------------------------------------------------
# request-scoped injection through the full pipeline
# --------------------------------------------------------------------------


class TestSourceUrlPipeline:
    def test_successful_manual_url_run_fetches_only_the_allowed_candidate(
        self,
    ):
        request = demo_request(MANUF_URL)
        record = make_evidence(
            MANUF_URL, evidence_id="ev-manual-0001", text=PAGE_TEXT
        )
        service, retriever = service_for(
            request,
            records=[record],
            llm_output=belt_attributes("ev-manual-0001", PAGE_TEXT),
        )
        result = service.run(request)

        assert result.processing.status == ProcessingStatus.COMPLETED
        discovery = result.discovery
        assert discovery.total_discovered == 1
        assert len(discovery.candidates) == 1
        assert discovery.candidates[0].status == CandidateStatus.ALLOWED
        assert retriever.calls == [MANUF_URL]
        assert len(result.evidence) == 1
        assert result.evidence[0].retrieval_status == RetrievalStatus.SUCCESS
        assert result.extraction is not None
        assert len(result.extraction.attributes) == 1
        attribute = result.extraction.attributes[0]
        assert attribute.name == "belt_width"
        assert attribute.evidence_ids == ["ev-manual-0001"]
        assert result.validation is not None
        assert result.delivery.column_count == 252
        assert result.delivery.headers[0] == "MFR URL"

    def test_url_rejected_by_policy_is_never_fetched(self):
        """A manual candidate on a host outside the derived manufacturer
        domain must be REJECTED by the SourcePolicy and never retrieved."""
        request = demo_request(UNKNOWN_URL)
        recorded = RecordingRetriever([])
        schema = DeliverySchema.from_reference_csv(delivery_fixture_path())
        service = EnrichmentService(
            # Provider emits UNKNOWN_URL; the request-scoped manufacturer
            # domain is a DIFFERENT host -> policy rejects the candidate.
            providers=[ManualUrlProvider(UNKNOWN_URL)],
            manufacturer_domains=["makitatools.example"],
            retriever=recorded,
            llm_client=FakeLLMClient(
                belt_attributes("ev-manual-0001", PAGE_TEXT)
            ),
            schema=schema,
            mapper=UniHackDeliveryMapper(schema),
        )
        result = service.run(request)

        assert recorded.calls == []
        assert len(result.discovery.rejected) == 1
        assert result.discovery.rejected[0].status == CandidateStatus.REJECTED
        assert result.evidence == []
        retrieval = next(
            s for s in result.stages if s.stage.value == "retrieval"
        )
        assert retrieval.status == StageStatus.SKIPPED
        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW

    def test_marketplace_url_is_never_fetched(self):
        request = demo_request(AMAZON_URL)
        service, retriever = service_for(
            request,
            records=[],
            llm_output=belt_attributes("ev-manual-0001", PAGE_TEXT),
        )
        result = service.run(request)

        assert retriever.calls == []
        assert len(result.discovery.rejected) == 1
        assert result.discovery.rejected[0].status == CandidateStatus.PROHIBITED
        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW

    def test_missing_url_preserves_default_discovery_behavior(self):
        request = demo_request()
        service, retriever = service_for(
            request,
            records=[],
            llm_output=belt_attributes("ev-manual-0001", PAGE_TEXT),
        )
        result = service.run(request)

        assert retriever.calls == []
        assert result.discovery.total_discovered == 0
        assert result.discovery.candidates == []
        assert result.evidence == []
        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW

    def test_invalid_url_is_safe_and_produces_no_source(self):
        request = demo_request("not a url")
        service, retriever = service_for(
            request,
            records=[],
            llm_output=belt_attributes("ev-manual-0001", PAGE_TEXT),
        )
        result = service.run(request)

        assert retriever.calls == []
        assert result.discovery.total_discovered == 0
        assert [
            state.stage.value for state in result.stages
            if state.status == StageStatus.SKIPPED
        ] == ["retrieval", "extraction", "validation", "description"]
        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW


# --------------------------------------------------------------------------
# SOURCE_ALLOWED_DOMAINS: an allowlisted external domain is permitted for
# automatic (Groq-style) discovery without the operator typing a source_url.
# The policy still decides (PENDING -> ALLOWED) and the relationship is
# EXTERNAL (OFFICIAL_DISTRIBUTOR), never manufacturer-owned.
# --------------------------------------------------------------------------


class TestAllowedExternalDomainViaSettings:
    def test_allowed_domain_pattern_permits_discovered_external(self, monkeypatch):
        monkeypatch.setattr(settings, "source_allowed_domains", "makitatools.example")
        # A discovered candidate on the allowlisted domain with NO
        # request-scoped manufacturer domain registry.
        providers, _ = build_manual_source(MANUF_URL)
        result = run_discovery(
            make_product(),
            providers=providers,
            context=DiscoveryContext(
                product=make_product(), manufacturer_domains=[]
            ),
        )
        assert result.total_discovered == 1
        candidate = result.candidates[0]
        assert candidate.status == CandidateStatus.ALLOWED
        assert (
            candidate.manufacturer_relationship == ManufacturerRelationship.EXTERNAL
        )
        assert candidate.trust_level == SourceTrustLevel.OFFICIAL_DISTRIBUTOR

    def test_allowlisted_domain_overrides_unknown_external_rejection(
        self, monkeypatch
    ):
        monkeypatch.setattr(settings, "source_allowed_domains", "random-site.example")
        providers, _ = build_manual_source(UNKNOWN_URL)
        result = run_discovery(
            make_product(),
            providers=providers,
            context=DiscoveryContext(
                product=make_product(), manufacturer_domains=[]
            ),
        )
        assert result.candidates == [] or all(
            c.status == CandidateStatus.ALLOWED for c in result.candidates
        )
        assert len(result.rejected) == 0
