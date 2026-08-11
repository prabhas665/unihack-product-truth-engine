"""Tests for the manual URL discovery provider (Step 7C).

TEST FIXTURES: made-up URLs (acme.example) used ONLY to exercise the
provider -> policy -> ranking flow. ZERO network calls: the provider only
emits a candidate; nothing is fetched here.
"""

import pytest

from app.core.domain import ProductIdentity, SourceTrustLevel, SourceType
from app.sources import (
    CandidateStatus,
    DiscoveryContext,
    DiscoveryMethod,
    ManufacturerRelationship,
    SourcePolicyConfig,
    run_discovery,
)
from app.sources.providers.manual_url import ManualUrlProvider, _candidate_id

TEST_URL = "https://makitatools.example/products/details/XLC10ZW"


def make_product() -> ProductIdentity:
    return ProductIdentity(
        manufacturer="Makita Usa Inc",
        brand="Makita",
        mpn="XLC10ZW",
        raw_description="XLC10ZW Makita 18V Cordless Vacuum (Bare)",
    )


class TestManualUrlProvider:
    def test_emits_one_direct_url_candidate(self):
        provider = ManualUrlProvider(TEST_URL)
        candidates = provider.discover(make_product(), DiscoveryContext(product=make_product()))
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.url == TEST_URL
        assert candidate.discovery_method == DiscoveryMethod.DIRECT_URL
        assert candidate.status == CandidateStatus.PENDING
        assert candidate.trust_level == SourceTrustLevel.UNVERIFIED
        assert candidate.domain == "makitatools.example"
        assert candidate.source_type == SourceType.MANUFACTURER_PRODUCT_PAGE

    def test_candidate_id_is_stable_and_url_derived(self):
        provider = ManualUrlProvider(TEST_URL)
        candidates = provider.discover(make_product(), DiscoveryContext(product=make_product()))
        assert candidates[0].id == _candidate_id(TEST_URL)
        assert candidates[0].id.startswith("manual-")

    def test_non_http_url_emits_nothing(self):
        provider = ManualUrlProvider("file:///C:/tmp/page.html")
        candidates = provider.discover(make_product(), DiscoveryContext(product=make_product()))
        assert candidates == []

    def test_source_type_and_title_are_configurable(self):
        provider = ManualUrlProvider(
            "https://acme.example/spec.pdf",
            source_type=SourceType.MANUFACTURER_TECHNICAL_PDF,
            title="spec sheet",
        )
        candidates = provider.discover(make_product(), DiscoveryContext(product=make_product()))
        assert candidates[0].source_type == SourceType.MANUFACTURER_TECHNICAL_PDF
        assert candidates[0].title == "spec sheet"


class TestManualUrlThroughPolicy:
    def test_manufacturer_domain_allows_candidate(self):
        context = DiscoveryContext(
            product=make_product(), manufacturer_domains=["makitatools.example"]
        )
        result = run_discovery(
            make_product(),
            providers=[ManualUrlProvider(TEST_URL)],
            context=context,
        )
        assert result.total_discovered == 1
        assert len(result.candidates) == 1
        candidate = result.candidates[0]
        assert candidate.status == CandidateStatus.ALLOWED
        assert candidate.manufacturer_relationship == ManufacturerRelationship.OWNED
        assert candidate.trust_level == SourceTrustLevel.MANUFACTURER_OFFICIAL
        assert result.rejected == []

    def test_unknown_domain_is_rejected_by_policy(self):
        result = run_discovery(
            make_product(),
            providers=[ManualUrlProvider(TEST_URL)],
            context=DiscoveryContext(product=make_product()),
        )
        assert result.total_discovered == 1
        assert result.candidates == []
        assert len(result.rejected) == 1
        assert result.rejected[0].status == CandidateStatus.REJECTED

    def test_explicit_policy_config_is_respected(self):
        policy_config = SourcePolicyConfig(
            manufacturer_domains=["makitatools.example"]
        )
        result = run_discovery(
            make_product(),
            providers=[ManualUrlProvider(TEST_URL)],
            context=DiscoveryContext(
                product=make_product(), policy_config=policy_config
            ),
        )
        assert len(result.candidates) == 1
        assert result.candidates[0].status == CandidateStatus.ALLOWED
