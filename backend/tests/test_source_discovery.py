"""Unit tests for source discovery (Step 3).

TEST FIXTURES: deterministic, made-up domains/products used ONLY to exercise
policy and ranking logic. These are NOT UniHack data and NOT real
manufacturer data - do not treat them as ground truth.

All tests are offline: no network calls, no search APIs.
"""

import pytest

from app.config import settings
from app.core.domain import ProductIdentity, SourceTrustLevel, SourceType
from app.sources import (
    CandidateStatus,
    DiscoveryContext,
    DiscoveryMethod,
    DiscoveryResult,
    ManufacturerRelationship,
    PROVIDERS,
    SourceCandidate,
    SourcePolicy,
    SourcePolicyConfig,
    normalize_domain,
    policy_from_settings,
    rank_candidates,
    register_provider,
    run_discovery,
)

# --- TEST FIXTURES (not UniHack data, not real manufacturers) ----------------

ACME_DOMAINS = ["acme-controls.example"]


def make_candidate(
    url: str = "https://acme-controls.example/products/m1",
    source_type: SourceType = SourceType.MANUFACTURER_PRODUCT_PAGE,
    title: str = "M1 Controller",
    discovery_method: DiscoveryMethod = DiscoveryMethod.DIRECT_URL,
) -> SourceCandidate:
    return SourceCandidate(
        url=url, source_type=source_type, title=title, discovery_method=discovery_method
    )


def acme_policy(**overrides) -> SourcePolicy:
    return SourcePolicy(
        SourcePolicyConfig(manufacturer_domains=ACME_DOMAINS, **overrides)
    )


def make_product(
    mpn: str = "M1",
    manufacturer: str = "Acme Controls",
    brand: str = "Acme",
    description: str = "industrial controller",
) -> ProductIdentity:
    return ProductIdentity(
        manufacturer=manufacturer,
        brand=brand,
        mpn=mpn,
        raw_description=description,
    )


class FakeProvider:
    """TEST FIXTURE provider: deterministic candidates, zero network."""

    name = "fixture-provider"
    kind = DiscoveryMethod.SEARCH

    def discover(self, product, context) -> list[SourceCandidate]:
        return [
            make_candidate(),
            make_candidate("https://www.amazon.com/dp/B0000001"),
            make_candidate("https://random-shop.example.com/item/1"),
        ]


class TestDomainNormalization:
    def test_normalize_domain(self):
        assert normalize_domain("https://WWW.ACME-CONTROLS.Example/x") == "acme-controls.example"
        assert normalize_domain("https://acme-controls.example:8443/x") == "acme-controls.example"

    def test_normalize_domain_handles_garbage(self):
        assert normalize_domain("not a url") == ""


class TestPolicyAccepted:
    def test_manufacturer_domain_accepted(self):
        result = acme_policy().evaluate(make_candidate())
        assert result.status == CandidateStatus.ALLOWED
        assert result.manufacturer_relationship == ManufacturerRelationship.OWNED
        assert result.trust_level == SourceTrustLevel.MANUFACTURER_OFFICIAL
        assert "manufacturer-owned domain" in result.rejection_reason
        assert result.domain == "acme-controls.example"

    def test_manufacturer_pdf_accepted_when_domain_owned(self):
        candidate = make_candidate(
            "https://acme-controls.example/docs/m1-datasheet.pdf",
            source_type=SourceType.MANUFACTURER_TECHNICAL_PDF,
        )
        result = acme_policy().evaluate(candidate)
        assert result.status == CandidateStatus.ALLOWED
        assert result.source_type == SourceType.MANUFACTURER_TECHNICAL_PDF

    def test_subdomain_of_manufacturer_domain_accepted(self):
        candidate = make_candidate("https://support.acme-controls.example/m1")
        result = acme_policy().evaluate(candidate)
        assert result.status == CandidateStatus.ALLOWED

    def test_allowlisted_external_domain_accepted(self):
        candidate = make_candidate("https://dist.acme.example/m1")
        result = acme_policy(
            allowed_domain_patterns=["dist.acme.example"]
        ).evaluate(candidate)
        assert result.status == CandidateStatus.ALLOWED
        assert result.manufacturer_relationship == ManufacturerRelationship.EXTERNAL
        assert result.trust_level == SourceTrustLevel.OFFICIAL_DISTRIBUTOR
        assert "allowlisted domain" in result.rejection_reason


class TestPolicyRejected:
    @pytest.mark.parametrize(
        "url,label",
        [
            ("https://www.amazon.com/dp/B0000001", "amazon"),
            ("https://www.amazon.de/dp/B0000001", "amazon"),
            ("https://ebay.com/itm/123", "ebay"),
            ("https://www.ebay.co.uk/itm/123", "ebay"),
        ],
    )
    def test_marketplaces_prohibited(self, url, label):
        result = acme_policy().evaluate(make_candidate(url))
        assert result.status == CandidateStatus.PROHIBITED
        assert f"marketplace domain" in result.rejection_reason
        assert label in result.rejection_reason

    def test_unknown_external_source_rejected_safely(self):
        result = acme_policy().evaluate(
            make_candidate("https://random-shop.example.com/item/1")
        )
        assert result.status == CandidateStatus.REJECTED
        assert result.manufacturer_relationship == ManufacturerRelationship.UNKNOWN
        assert "unknown external domain" in result.rejection_reason
        assert result.rejection_reason

    def test_unknown_source_type_rejected_even_on_owned_domain(self):
        candidate = make_candidate(source_type=SourceType.UNKNOWN)
        result = acme_policy().evaluate(candidate)
        assert result.status == CandidateStatus.REJECTED
        assert "not in the permitted set" in result.rejection_reason

    def test_configured_prohibited_pattern_honored(self):
        candidate = make_candidate("https://suspicious.example.com/m1")
        result = acme_policy(
            prohibited_domain_patterns=["suspicious.example.com"]
        ).evaluate(candidate)
        assert result.status == CandidateStatus.PROHIBITED
        assert "configured pattern" in result.rejection_reason

    @pytest.mark.parametrize(
        "candidate,keyword",
        [
            (make_candidate("https://www.amazon.com/dp/B0000001"), "marketplace"),
            (make_candidate("https://ebay.de/itm/1"), "marketplace"),
            (make_candidate("https://random-shop.example.com/item/1"), "unknown external"),
            (make_candidate(source_type=SourceType.UNKNOWN), "permitted set"),
        ],
    )
    def test_rejection_reason_is_recorded(self, candidate, keyword):
        result = acme_policy().evaluate(candidate)
        assert result.status in (CandidateStatus.PROHIBITED, CandidateStatus.REJECTED)
        assert result.rejection_reason
        assert keyword in result.rejection_reason


class TestRanking:
    def test_part_number_relevance_improves_ranking(self):
        policy = acme_policy()
        with_mpn = policy.evaluate(
            make_candidate(
                "https://acme-controls.example/products/m1", title="Acme M1 Controller"
            )
        )
        without_mpn = policy.evaluate(
            make_candidate(
                "https://acme-controls.example/products/controller",
                title="Acme Controller",
            )
        )
        product = make_product(mpn="M1")

        ranked = rank_candidates([without_mpn, with_mpn], product)

        assert ranked[0].url == with_mpn.url
        assert ranked[0].relevance_score > ranked[1].relevance_score

    def test_ranking_is_deterministic(self):
        policy = acme_policy()
        candidates = [
            policy.evaluate(make_candidate(f"https://acme-controls.example/p/{i}"))
            for i in range(3)
        ]
        product = make_product(mpn="M1")
        first = [c.url for c in rank_candidates(candidates, product)]
        second = [c.url for c in rank_candidates(candidates, product)]
        assert first == second


class TestDiscoveryOrchestration:
    def test_run_discovery_end_to_end(self):
        product = make_product()
        context = DiscoveryContext(
            product=product, manufacturer_domains=ACME_DOMAINS
        )
        result = run_discovery(product, providers=[FakeProvider()], context=context)

        assert isinstance(result, DiscoveryResult)
        assert result.total_discovered == 3
        assert len(result.candidates) == 1
        assert result.candidates[0].status == CandidateStatus.ALLOWED
        assert result.candidates[0].url == make_candidate().url
        assert len(result.rejected) == 2
        assert all(c.rejection_reason for c in result.rejected)

    def test_provider_registry(self):
        fake = FakeProvider()
        register_provider(fake)
        try:
            assert fake in PROVIDERS
            result = run_discovery(
                make_product(),
                context=DiscoveryContext(product=make_product(), manufacturer_domains=ACME_DOMAINS),
            )
            assert result.total_discovered == 3
        finally:
            PROVIDERS.remove(fake)


class TestPolicyFromSettings:
    def test_env_config_parsed(self, monkeypatch):
        monkeypatch.setattr(
            settings,
            "source_allowed_domains",
            "dist.acme.example, parts.acme.example",
        )
        monkeypatch.setattr(settings, "source_prohibited_domains", "bad.example")
        config = policy_from_settings()
        assert config.allowed_domain_patterns == [
            "dist.acme.example",
            "parts.acme.example",
        ]
        assert config.prohibited_domain_patterns == ["bad.example"]
