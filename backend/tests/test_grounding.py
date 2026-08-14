"""Offline tests for the description grounding guard (Step 9B).

Covers the deterministic grounding module (app.descriptions.grounding) and
its integration into the pipeline: unsupported factual claims are dropped
from generated copy, the affected fields are blanked, review reasons are
added, and the description stage is marked NEEDS_REVIEW (partial drop) or
FAILED (everything dropped). All providers are fakes; nothing touches the
network.
"""

from __future__ import annotations

import hashlib
import json

from app.core.domain import (
    AttributeValue,
    Descriptions,
    ProcessingStatus,
    ProductIdentity,
    SourceType,
)
from app.descriptions.grounding import (
    apply_grounding,
    find_violations,
    has_any_content,
)
from app.extraction import ExtractionOutput, ExtractionOutputItem
from app.llm import LLMClient
from app.pipeline.enrichment import (
    EnrichmentRequest,
    EnrichmentService,
    StageName,
    StageStatus,
)
from app.sources.candidates import DiscoveryMethod, SourceCandidate
from app.sources.retrieval import (
    EvidenceRecord,
    ExtractionStatus,
    RetrievalStatus,
)

ACME_PAGE = "https://www.acme.com/products/vac1000"
EVIDENCE_A = "ev-acme-page-0001"


class FakeProvider:
    name = "fake-search"
    kind = DiscoveryMethod.SEARCH

    def __init__(self, candidates: list[SourceCandidate]) -> None:
        self._candidates = candidates

    def discover(self, product, context):
        return list(self._candidates)


class FakeRetriever:
    def __init__(self, records: list[EvidenceRecord]) -> None:
        self.by_url = {record.url: record for record in records}

    def __call__(self, candidate: SourceCandidate) -> EvidenceRecord:
        return self.by_url.get(candidate.url, failed_record(candidate.url))


class FakeLLMClient(LLMClient):
    provider = "fake"

    def __init__(self, descriptions_json: str, output: ExtractionOutput) -> None:
        self._descriptions = descriptions_json
        self._output = output

    def _complete(self, prompt, **kwargs) -> str:
        if "PRODUCT IDENTITY" in prompt:
            return self._descriptions
        return json.dumps(self._output.model_dump())


def candidate(url: str) -> SourceCandidate:
    return SourceCandidate(
        id=f"cand-{hashlib.sha256(url.encode()).hexdigest()[:12]}",
        url=url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        title=url,
    )


def success_record(url: str) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=EVIDENCE_A,
        source_candidate_id=f"cand-{url}",
        url=url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        title=url,
        text="VAC-1000 Acme 18 V cordless vacuum, bare tool.",
        content_type="text/html",
        retrieval_status=RetrievalStatus.SUCCESS,
        extraction_status=ExtractionStatus.EXTRACTED,
    )


def failed_record(url: str) -> EvidenceRecord:
    return EvidenceRecord(
        source_candidate_id=f"cand-{url}",
        url=url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        retrieval_status=RetrievalStatus.FAILED,
        error_message="connection refused",
    )


def identity() -> ProductIdentity:
    return ProductIdentity(
        manufacturer="Acme Tools",
        brand="Acme",
        mpn="VAC-1000",
        raw_description="VAC-1000 Acme 18V cordless vacuum (bare tool).",
        sku=None,
    )


def attr(name: str, value: str, unit: str = "") -> AttributeValue:
    return AttributeValue(
        name=name,
        raw_value=value,
        value=value,
        unit=unit,
        confidence=0.9,
    )


def vacuume_attributes() -> dict[str, AttributeValue]:
    return {
        "Power Type": attr("Power Type", "Cordless"),
        "Product Type": attr("Product Type", "Vacuum"),
        "Voltage": attr("Voltage", "18", unit="V"),
    }


# --------------------------------------------------------------------------
# grounding unit tests
# --------------------------------------------------------------------------


class TestGroundingUnit:
    def test_natural_derivation_from_attributes_passes(self):
        descriptions = Descriptions(
            product_title="Cordless vacuum",
            short_description="Cordless vacuum.",
        )
        grounded, reasons, drops = apply_grounding(
            descriptions,
            identity=identity(),
            attributes=vacuume_attributes(),
        )
        assert drops == 0
        assert reasons == []
        assert grounded.product_title == "Cordless vacuum"

    def test_grounded_voltage_claim_passes(self):
        descriptions = Descriptions(short_description="18 V cordless vacuum.")
        grounded, _, drops = apply_grounding(
            descriptions,
            identity=identity(),
            attributes=vacuume_attributes(),
        )
        assert drops == 0
        assert grounded.short_description == "18 V cordless vacuum."

    def test_ungrounded_warranty_claim_is_dropped(self):
        descriptions = Descriptions(
            product_title="Cordless vacuum",
            long_description="Cordless vacuum with a 2-year warranty.",
        )
        grounded, reasons, drops = apply_grounding(
            descriptions,
            identity=identity(),
            attributes=vacuume_attributes(),
        )
        assert drops == 1
        assert grounded.long_description == ""
        assert has_any_content(grounded)
        assert any(
            "unsupported warranty claim" in reason
            and "\u201c2-year warranty\u201d" in reason
            for reason in reasons
        )

    def test_ungrounded_certification_claim_is_dropped(self):
        descriptions = Descriptions(
            marketing_description="UL certified cordless vacuum."
        )
        grounded, reasons, drops = apply_grounding(
            descriptions,
            identity=identity(),
            attributes=vacuume_attributes(),
        )
        assert drops == 1
        assert grounded.marketing_description == ""
        assert any(
            "unsupported certification claim" in reason
            for reason in reasons
        )

    def test_ungrounded_dimension_claim_is_dropped(self):
        descriptions = Descriptions(
            long_description="Cordless vacuum that measures 24 inches long."
        )
        grounded, reasons, drops = apply_grounding(
            descriptions,
            identity=identity(),
            attributes=vacuume_attributes(),
        )
        assert drops == 1
        assert grounded.long_description == ""
        assert any(
            "unsupported dimensions or weight claim" in reason
            for reason in reasons
        )

    def test_grounded_dimension_claim_is_kept(self):
        descriptions = Descriptions(
            long_description="Cordless vacuum that measures 18 inches long."
        )
        grounded, _, drops = apply_grounding(
            descriptions,
            identity=identity(),
            attributes={
                **vacuume_attributes(),
                "Length": attr("Length", "18", unit="in"),
            },
        )
        assert drops == 0
        assert grounded.long_description == (
            "Cordless vacuum that measures 18 inches long."
        )

    def test_ungrounded_accessory_claim_is_dropped(self):
        descriptions = Descriptions(
            includes="belt clip, spare battery and charger"
        )
        grounded, reasons, drops = apply_grounding(
            descriptions,
            identity=identity(),
            attributes=vacuume_attributes(),
        )
        assert drops == 1
        assert grounded.includes == ""

    def test_item_feature_dropped_individually(self):
        descriptions = Descriptions(
            item_features=["18 V", "2-year warranty", "cordless", "bare tool"]
        )
        grounded, reasons, drops = apply_grounding(
            descriptions,
            identity=identity(),
            attributes=vacuume_attributes(),
        )
        assert drops == 1
        assert grounded.item_features == ["18 V", "cordless", "bare tool"]
        assert any(
            "an item feature contained" in reason
            and "warranty" in reason
            for reason in reasons
        )

    def test_quotes_ground_claim_terms(self):
        descriptions = Descriptions(
            short_description="UL certified cordless vacuum."
        )
        grounded, _, drops = apply_grounding(
            descriptions,
            identity=identity(),
            attributes=vacuume_attributes(),
            quotes=["Certified by UL for safety.", "Bare tool."],
        )
        assert drops == 0
        assert grounded.short_description == "UL certified cordless vacuum."

    def test_find_violations_reports_category_and_snippet(self):
        violations = find_violations(
            "Great vacuum. 2-year warranty included.",
            set(),
        )
        assert any(
            category == "warranty" and snippet == "2-year warranty"
            for category, snippet in violations
        )


# --------------------------------------------------------------------------
# pipeline integration
# --------------------------------------------------------------------------


def canned_output() -> ExtractionOutput:
    return ExtractionOutput(
        items=[
            ExtractionOutputItem(
                name="power_type",
                raw_value="Cordless",
                normalized_value="Cordless",
                confidence=0.9,
                evidence_ids=[EVIDENCE_A],
            ),
            ExtractionOutputItem(
                name="product_type",
                raw_value="Vacuum",
                normalized_value="Vacuum",
                confidence=0.9,
                evidence_ids=[EVIDENCE_A],
            ),
            ExtractionOutputItem(
                name="voltage",
                raw_value="18 V",
                normalized_value="18 V",
                unit="V",
                confidence=0.9,
                evidence_ids=[EVIDENCE_A],
            ),
        ]
    )


GOOD_DESCRIPTIONS_JSON = json.dumps(
    {
        "product_title": "VAC-1000 Cordless Vacuum",
        "short_description": "18 V cordless vacuum.",
        "long_description": "Cordless vacuum, bare tool.",
        "item_features": ["18 V", "cordless"],
        "with": "Bare tool",
    }
)

CLAIM_DESCRIPTIONS_JSON = json.dumps(
    {
        "product_title": "VAC-1000 Cordless Vacuum",
        "short_description": "Cordless vacuum with a 2-year warranty.",
        "long_description": "UL certified cordless vacuum.",
        "item_features": ["18 V", "measures 24 inches long"],
        "with": "Bare tool",
    }
)

ALL_CLAIMS_JSON = json.dumps(
    {
        "product_title": "VAC-1000 UL certified vacuum.",
        "short_description": "VAC-1000 with a 2-year warranty.",
        "long_description": "UL certified with a lifetime guarantee.",
        "item_features": ["UL certified", "measures 24 inches long"],
        "with": "Includes a lifetime warranty",
        "application": "UL certified application",
        "includes": "2-year warranty",
        "product_name": "UL certified",
    }
)


def request() -> EnrichmentRequest:
    return EnrichmentRequest(
        Mfg_Part_Num="VAC-1000",
        Part_Desc="VAC-1000 Acme 18V cordless vacuum (bare tool).",
        E1_Brand="Acme",
        Unilog_Brand="",
        DIB_Brand="",
        Part_Manuf="Acme Tools",
    )


def service(descriptions_json: str) -> EnrichmentService:
    return EnrichmentService(
        providers=[FakeProvider([candidate(ACME_PAGE)])],
        manufacturer_domains=["acme.com"],
        retriever=FakeRetriever([success_record(ACME_PAGE)]),
        llm_client=FakeLLMClient(descriptions_json, canned_output()),
    )


class TestGroundingPipeline:
    def test_grounded_copy_passes_unchanged(self):
        result = service(GOOD_DESCRIPTIONS_JSON).run(request())
        assert result.processing.status == ProcessingStatus.COMPLETED
        stage = next(
            s for s in result.stages if s.stage == StageName.DESCRIPTION
        )
        assert stage.status == StageStatus.COMPLETED
        assert result.product.descriptions.product_title == (
            "VAC-1000 Cordless Vacuum"
        )
        assert not any(
            "description grounding" in reason
            for reason in result.review_reasons
        )

    def test_unsupported_claims_dropped_stage_needs_review(self):
        result = service(CLAIM_DESCRIPTIONS_JSON).run(request())
        assert result.processing.status == ProcessingStatus.NEEDS_REVIEW
        stage = next(
            s for s in result.stages if s.stage == StageName.DESCRIPTION
        )
        assert stage.status == StageStatus.NEEDS_REVIEW
        assert "grounding blanked 3" in stage.note
        descriptions = result.product.descriptions
        assert descriptions.product_title == "VAC-1000 Cordless Vacuum"
        assert descriptions.short_description == ""
        assert descriptions.long_description == ""
        assert descriptions.item_features == ["18 V"]
        assert descriptions.with_ == "Bare tool"
        grounding_reasons = [
            reason
            for reason in result.review_reasons
            if "description grounding" in reason
        ]
        assert len(grounding_reasons) == 3

    def test_everything_dropped_marks_stage_failed(self):
        result = service(ALL_CLAIMS_JSON).run(request())
        assert result.processing.status == ProcessingStatus.FAILED
        stage = next(
            s for s in result.stages if s.stage == StageName.DESCRIPTION
        )
        assert stage.status == StageStatus.FAILED
        descriptions = result.product.descriptions
        assert descriptions.product_title == ""
        assert descriptions.short_description == ""
        assert descriptions.long_description == ""
        assert descriptions.item_features == []
        assert descriptions.with_ == ""
        assert descriptions.application == ""
        assert descriptions.includes == ""
        assert descriptions.product_name == ""
