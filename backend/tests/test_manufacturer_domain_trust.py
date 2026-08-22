"""Tests for per-product manufacturer-domain trust (no global domain allowlist).

Covers:
- The policy trusts a manufacturer domain ONLY when it is in the request's
  scoped manufacturer_domains list (policy path).
- EnrichmentService scopes manufacturer_domains to the *verified* manufacturer
  (curated registry seed), so a curated domain is trusted only for the product
  that actually verifies to that manufacturer (integration path).
- A source_url still injects its hostname as a request-scoped trusted domain.

Offline only: fakes for provider / retriever / LLM. No network.
"""

from __future__ import annotations

import json

import pytest

from app.core.domain import (
    ProcessingStatus,
    SourceTrustLevel,
    SourceType,
)
from app.extraction import ExtractionOutput, ExtractionOutputItem
from app.identity.mapping import VerifiedBrandLookup
from app.llm import LLMClient
from app.pipeline.enrichment import EnrichmentRequest, EnrichmentService
from app.sources import (
    CandidateStatus,
    DiscoveryMethod,
    ManufacturerRelationship,
    SourceCandidate,
    SourcePolicy,
    SourcePolicyConfig,
)
from app.sources.retrieval import EvidenceRecord, ExtractionStatus, RetrievalStatus

EVIDENCE_A = "ev-curated-0001"

DESCRIPTIONS_JSON = json.dumps(
    {
        "product_title": "Curated product",
        "short_description": "A curated manufacturer product.",
        "long_description": "Curated manufacturer product page.",
        "item_features": ["feature"],
    }
)


class FakeProvider:
    name = "fake-search"
    kind = DiscoveryMethod.SEARCH

    def __init__(self, candidates=None, error=None):
        self._candidates = candidates or []
        self._error = error

    def discover(self, product, context):
        if self._error is not None:
            raise self._error
        return list(self._candidates)


class FakeRetriever:
    def __init__(self, records):
        self.by_url = {r.url: r for r in records}

    def __call__(self, candidate: SourceCandidate) -> EvidenceRecord:
        return self.by_url.get(
            candidate.url,
            EvidenceRecord(
                source_candidate_id=candidate.id,
                url=candidate.url,
                source_type=candidate.source_type,
                retrieval_status=RetrievalStatus.FAILED,
                error_kind=None,
                error_message="no record",
            ),
        )


class FakeLLMClient(LLMClient):
    provider = "fake"

    def __init__(self, output=None, raw="", error=None, error_on_description=None):
        self._output = output
        self._raw = raw
        self._error = error
        self._error_on_description = error_on_description

    def _complete(self, prompt, *, system_prompt="", temperature=None, timeout_seconds=None):
        if self._error is not None:
            raise self._error
        if "PRODUCT IDENTITY" in prompt:
            if self._error_on_description is not None:
                raise self._error_on_description
            return DESCRIPTIONS_JSON
        if self._raw:
            return self._raw
        return json.dumps(self._output.model_dump())


def candidate(url: str, source_type: SourceType = SourceType.MANUFACTURER_PRODUCT_PAGE):
    return SourceCandidate(url=url, source_type=source_type, title=url, discovery_method=DiscoveryMethod.DIRECT_URL)


def success_record(url: str, evidence_id: str = EVIDENCE_A) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        source_candidate_id=f"cand-{url}",
        url=url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        title=url,
        text="Curated manufacturer product page content.",
        content_type="text/html",
        retrieval_status=RetrievalStatus.SUCCESS,
        extraction_status=ExtractionStatus.EXTRACTED,
    )


def canned_output(evidence_id: str = EVIDENCE_A) -> ExtractionOutput:
    return ExtractionOutput(
        items=[
            ExtractionOutputItem(
                name="model",
                raw_value="curated",
                normalized_value="curated",
                unit="",
                confidence=0.9,
                evidence_ids=[evidence_id],
                notes="from the product page",
            )
        ]
    )


def default_request(**overrides) -> EnrichmentRequest:
    payload = {
        "Mfg_Part_Num": "UNKNOWN-MPN",
        "Part_Desc": "Generic product",
        "E1_Brand": "-- Unbranded --",
        "Unilog_Brand": "-- No Unilog Brand --",
        "DIB_Brand": "-- No DIB Brand --",
        "Part_Manuf": "Some Distributor (XYZ)",
    }
    payload.update(overrides)
    return EnrichmentRequest(**payload)


def make_service(provider, url, records, manufacturer_domains, llm):
    return EnrichmentService(
        providers=[provider],
        manufacturer_domains=manufacturer_domains,
        retriever=FakeRetriever(records),
        llm_client=llm,
        verified_lookup=VerifiedBrandLookup.default(),
    )


# Curated (mpn, desc, part_manuf, trusted domain) from verified_brands.json.
CURATED = [
    ("WDTS7024RZ", "WDTS7024RZ Dishwasher SS - Display Only", "Appliance Dealers Cooperative (APPDE)", "whirlpool.com"),
    ("1700-1PK-BB40", "3M Sandpaper 1PK", "Some Distributor (XYZ)", "3m.com"),
    ("49-94-0013", "Milwaukee Pry Bar", "Some Distributor (XYZ)", "milwaukeetool.com"),
    ("XLC10ZW", "Makita Battery", "Some Distributor (XYZ)", "makitatools.com"),
    ("AVM6EV", "Malco AVM6EV", "Some Distributor (XYZ)", "malcotools.com"),
]


class TestPolicyDomainTrust:
    def test_curatated_domain_owned_when_in_scope(self):
        policy = SourcePolicy(SourcePolicyConfig(manufacturer_domains=["makitatools.com"]))
        res = policy.evaluate(candidate("https://makitatools.com/p/x"))
        assert res.status == CandidateStatus.ALLOWED
        assert res.manufacturer_relationship == ManufacturerRelationship.OWNED
        assert res.trust_level == SourceTrustLevel.MANUFACTURER_OFFICIAL

    def test_unknown_domain_rejected_by_policy(self):
        policy = SourcePolicy(SourcePolicyConfig(manufacturer_domains=["makitatools.com"]))
        res = policy.evaluate(candidate("https://random-shop.example.com/p/x"))
        assert res.status == CandidateStatus.REJECTED


class TestEnrichmentScopedDomains:
    @pytest.mark.parametrize("mpn,desc,part_manuf,domain", CURATED)
    def test_mpn_only_trusts_curated_registry_domain(self, mpn, desc, part_manuf, domain):
        url = f"https://{domain}/product/{mpn}"
        provider = FakeProvider(candidates=[candidate(url)])
        llm = FakeLLMClient(output=canned_output())
        service = make_service(provider, url, [success_record(url)], manufacturer_domains=[], llm=llm)
        request = default_request(Mfg_Part_Num=mpn, Part_Desc=desc, Part_Manuf=part_manuf)
        result = service.run(request)

        assert result.discovery.candidates, "curated domain should be allowed"
        assert any(domain in c.url for c in result.discovery.candidates)
        assert result.evidence, "retrieval should run for the trusted candidate"
        assert result.processing.status != ProcessingStatus.FAILED

    def test_unseeded_product_rejects_unrelated_curatated_domain(self):
        # Product B verifies to Whirlpool (WDTS7024RZ) -> registry domain is
        # whirlpool.com only. A malcotools.com candidate must be rejected
        # because it is NOT in this product's scoped manufacturer_domains.
        # Use malcotools.com (not in global SOURCE_ALLOWED_DOMAINS) to avoid
        # global distributor allowlist masking the per-product check.
        url = "https://malcotools.com/tool/x"
        provider = FakeProvider(candidates=[candidate(url)])
        llm = FakeLLMClient(output=canned_output())
        service = make_service(provider, url, [], manufacturer_domains=[], llm=llm)
        request = default_request(
            Mfg_Part_Num="WDTS7024RZ",
            Part_Desc="WDTS7024RZ Dishwasher SS - Display Only",
            Part_Manuf="Appliance Dealers Cooperative (APPDE)",
        )
        result = service.run(request)

        assert not result.discovery.candidates, "unrelated curated domain must be rejected"
        assert not result.evidence, "nothing should be retrieved for a rejected candidate"

    def test_source_url_injects_request_scoped_domain(self):
        # WDTS7024RZ verifies to Whirlpool. A user-supplied source_url on
        # operator.example.com is trusted ONLY for this request via the
        # request-scoped manufacturer_domains, alongside the registry domain.
        owned_url = "https://whirlpool.com/product/WDTS7024RZ"
        manual_url = "https://operator.example.com/manual/WDTS7024RZ"
        provider = FakeProvider(candidates=[candidate(owned_url), candidate(manual_url)])
        llm = FakeLLMClient(output=canned_output())
        service = make_service(
            provider,
            owned_url,
            [success_record(owned_url), success_record(manual_url)],
            manufacturer_domains=["operator.example.com"],
            llm=llm,
        )
        request = default_request(
            Mfg_Part_Num="WDTS7024RZ",
            Part_Desc="WDTS7024RZ Dishwasher SS - Display Only",
            Part_Manuf="Appliance Dealers Cooperative (APPDE)",
        )
        result = service.run(request)

        urls = {c.url for c in result.discovery.candidates}
        assert owned_url in urls
        assert manual_url in urls

    def test_source_url_trusted_even_without_verified_seed(self):
        # No verified seed for this MPN, but the source_url hostname is still
        # trusted as a request-scoped manufacturer domain.
        manual_url = "https://operator.example.com/manual/UNKNOWN-MPN"
        provider = FakeProvider(candidates=[candidate(manual_url)])
        llm = FakeLLMClient(output=canned_output())
        service = make_service(
            provider,
            manual_url,
            [success_record(manual_url)],
            manufacturer_domains=["operator.example.com"],
            llm=llm,
        )
        request = default_request(Mfg_Part_Num="UNKNOWN-MPN", Part_Desc="Generic")
        result = service.run(request)

        assert any(manual_url in c.url for c in result.discovery.candidates)
        assert result.evidence
