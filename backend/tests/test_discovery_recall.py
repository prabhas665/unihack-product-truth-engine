"""Discovery-2 two-pass recall tests (offline, deterministic).

TEST FIXTURES: made-up acme-controls.example domains/products ONLY - not
UniHack data. A stateful fixture provider simulates the Groq agentic-search
behavior: the biased pass-1 query can return nothing or externals while the
unbiased pass-2 query surfaces the official domain page.

ZERO network calls: run_discovery with a fixture provider never touches a
real API.
"""

import pytest

from app.core.domain import ProductIdentity, SourceType
from app.sources import (
    CandidateStatus,
    DiscoveryContext,
    DiscoveryMethod,
    SourceCandidate,
    run_discovery,
)
from app.sources.errors import ProviderUnavailableError

ACME_DOMAINS = ["acme-controls.example"]

OFFICIAL = "https://acme-controls.example/products/m1"
OFFICIAL_OTHER = "https://acme-controls.example/docs/m1.pdf"
EXTERNAL = "https://random-shop.example.com/item/1"
AMAZON = "https://www.amazon.com/dp/B0000001"


def make_candidate(
    url: str,
    source_type: SourceType = SourceType.MANUFACTURER_PRODUCT_PAGE,
) -> SourceCandidate:
    return SourceCandidate(
        url=url, title="M1", source_type=source_type, discovery_method=DiscoveryMethod.SEARCH
    )


def make_product(mpn: str = "M1", manufacturer: str = "Acme Controls") -> ProductIdentity:
    return ProductIdentity(manufacturer=manufacturer, brand="Acme", mpn=mpn)


class StatefulProvider:
    """Fixture provider returning per-pass result sets keyed on query_biased.

    Records every call's query_biased value so tests can assert pass
    ordering and that Pass 2 only runs when Pass 1 allowed nothing.
    """

    name = "fixture-recall"
    kind = DiscoveryMethod.SEARCH

    def __init__(self, biased, unbiased, errors=None):
        self.biased = list(biased)
        self.unbiased = list(unbiased)
        self.errors = errors or {}
        self.calls: list[bool] = []

    def discover(self, product, context):
        biased = bool(getattr(context, "query_biased", True))
        self.calls.append(biased)
        if self.errors.get(biased):
            raise ProviderUnavailableError(self.name, "boom")
        return [make_candidate(u) for u in (self.biased if biased else self.unbiased)]


class TestTwoPassRecall:
    def test_pass1_empty_pass2_surfaces_official_domain(self):
        provider = StatefulProvider(biased=[], unbiased=[OFFICIAL])
        result = run_discovery(
            make_product(),
            providers=[provider],
            context=DiscoveryContext(
                product=make_product(), manufacturer_domains=ACME_DOMAINS
            ),
        )
        assert provider.calls == [True, False]
        assert [c.url for c in result.candidates] == [OFFICIAL]
        assert result.total_discovered == 1
        assert result.provider_errors == []

    def test_pass2_not_called_when_pass1_has_allowed_candidate(self):
        provider = StatefulProvider(biased=[OFFICIAL], unbiased=[OFFICIAL_OTHER])
        result = run_discovery(
            make_product(),
            providers=[provider],
            context=DiscoveryContext(
                product=make_product(), manufacturer_domains=ACME_DOMAINS
            ),
        )
        assert provider.calls == [True]
        assert [c.url for c in result.candidates] == [OFFICIAL]
        assert result.total_discovered == 1

    def test_pass2_called_when_pass1_has_candidates_but_none_allowed(self):
        provider = StatefulProvider(biased=[EXTERNAL, AMAZON], unbiased=[OFFICIAL])
        result = run_discovery(
            make_product(),
            providers=[provider],
            context=DiscoveryContext(
                product=make_product(), manufacturer_domains=ACME_DOMAINS
            ),
        )
        assert provider.calls == [True, False]
        assert [c.url for c in result.candidates] == [OFFICIAL]
        # Pass-1 candidates survive into the final rejected set.
        rejected_urls = {c.url for c in result.rejected}
        assert EXTERNAL in rejected_urls
        assert AMAZON in rejected_urls

    def test_duplicate_url_deduped_across_passes(self):
        provider = StatefulProvider(biased=[AMAZON], unbiased=[OFFICIAL, OFFICIAL])
        result = run_discovery(
            make_product(),
            providers=[provider],
            context=DiscoveryContext(
                product=make_product(), manufacturer_domains=ACME_DOMAINS
            ),
        )
        assert result.total_discovered == 2
        assert [c.url for c in result.candidates] == [OFFICIAL]
        assert len(result.rejected) == 1

    def test_pass2_provider_error_preserves_pass1_candidates(self):
        provider = StatefulProvider(
            biased=[EXTERNAL], unbiased=[OFFICIAL], errors={False: True}
        )
        result = run_discovery(
            make_product(),
            providers=[provider],
            context=DiscoveryContext(
                product=make_product(), manufacturer_domains=ACME_DOMAINS
            ),
        )
        assert provider.calls == [True, False]
        assert result.candidates == []
        assert {c.url for c in result.rejected} == {EXTERNAL}
        assert len(result.provider_errors) == 1
        assert result.provider_errors[0].provider_name == "fixture-recall"
        assert result.provider_errors[0].error_kind == "unavailable"

    def test_pass2_external_and_marketplace_still_rejected(self):
        provider = StatefulProvider(biased=[AMAZON], unbiased=[OFFICIAL, AMAZON, EXTERNAL])
        result = run_discovery(
            make_product(),
            providers=[provider],
            context=DiscoveryContext(
                product=make_product(), manufacturer_domains=ACME_DOMAINS
            ),
        )
        assert [c.url for c in result.candidates] == [OFFICIAL]
        by_domain = {c.domain: c for c in result.rejected}
        assert by_domain["amazon.com"].status == CandidateStatus.PROHIBITED
        assert "marketplace" in by_domain["amazon.com"].rejection_reason
        assert by_domain["random-shop.example.com"].status == CandidateStatus.REJECTED

    def test_both_pass_errors_surfaced(self):
        provider = StatefulProvider(
            biased=[], unbiased=[], errors={True: True, False: True}
        )
        result = run_discovery(
            make_product(),
            providers=[provider],
            context=DiscoveryContext(
                product=make_product(), manufacturer_domains=ACME_DOMAINS
            ),
        )
        assert provider.calls == [True, False]
        assert result.total_discovered == 0
        assert result.candidates == []
        assert len(result.provider_errors) == 2
        assert all(e.provider_name == "fixture-recall" for e in result.provider_errors)

    def test_both_passes_empty_returns_normal_no_source_result(self):
        provider = StatefulProvider(biased=[], unbiased=[])
        result = run_discovery(
            make_product(),
            providers=[provider],
            context=DiscoveryContext(
                product=make_product(), manufacturer_domains=ACME_DOMAINS
            ),
        )
        assert provider.calls == [True, False]
        assert result.total_discovered == 0
        assert result.candidates == []
        assert result.rejected == []
        assert result.provider_errors == []

    def test_pass2_uses_same_provider_object(self):
        provider = StatefulProvider(biased=[], unbiased=[OFFICIAL])
        run_discovery(
            make_product(),
            providers=[provider],
            context=DiscoveryContext(
                product=make_product(), manufacturer_domains=ACME_DOMAINS
            ),
        )
        # The SAME provider instance serves both passes (no new provider).
        assert provider.calls == [True, False]

    def test_policy_trust_unchanged_in_pass2(self):
        """Pass 2 must NOT loosen trust: official domain stays ALLOWED and
        nothing outside the registry becomes allowed."""
        provider = StatefulProvider(biased=[], unbiased=[OFFICIAL, EXTERNAL])
        result = run_discovery(
            make_product(),
            providers=[provider],
            context=DiscoveryContext(
                product=make_product(), manufacturer_domains=ACME_DOMAINS
            ),
        )
        assert [c.url for c in result.candidates] == [OFFICIAL]
        assert {c.url for c in result.rejected} == {EXTERNAL}
