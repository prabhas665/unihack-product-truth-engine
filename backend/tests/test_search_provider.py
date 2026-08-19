"""Tests for the real search discovery provider (Step 6B).

TEST FIXTURES: deterministic, made-up domains/products (acme-controls.example
etc.) used ONLY to exercise the provider -> policy -> ranking flow. These are
NOT UniHack data and NOT real manufacturers - do not treat them as ground
truth.

ZERO network calls: the search API client is driven by httpx.MockTransport
with canned JSON responses. The real provider is NEVER executed here (use
`python -m app.sources.providers.manual_check` for that).
"""

import httpx
import pytest

from app.config import settings
from app.core.domain import ProductIdentity, SourceTrustLevel, SourceType
from app.sources import (
    CandidateStatus,
    DiscoveryContext,
    DiscoveryMethod,
    ManufacturerRelationship,
    PROVIDERS,
    ProviderConfigurationError,
    ProviderError,
    ProviderErrorInfo,
    ProviderInvalidResponseError,
    ProviderUnavailableError,
    SearchApiClient,
    SearchProvider,
    SourcePolicyConfig,
    build_search_query,
    providers_from_settings,
    run_discovery,
)

ACME_DOMAINS = ["acme-controls.example"]


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


def mock_client(
    handler, **overrides
) -> SearchApiClient:
    """SearchApiClient pinned to an offline httpx.MockTransport."""
    return SearchApiClient(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **overrides,
    )


def organic_payload(*items: dict) -> dict:
    return {"organic": list(items)}


def provider_with(
    handler, **overrides
) -> SearchProvider:
    return SearchProvider(mock_client(handler, **overrides))


# ------------------------------------------------------------- query builder --

class TestQueryBuilder:
    def test_exact_mpn_quoted_and_preferred(self):
        product = ProductIdentity(
            manufacturer="Freud Inc", mpn="DCB518ASTS06G"
        )
        assert build_search_query(product) == 'Freud Inc "DCB518ASTS06G"'

    def test_brand_included_when_distinct(self):
        product = ProductIdentity(
            manufacturer="Acme Controls", brand="Acme", mpn="M1"
        )
        assert build_search_query(product) == 'Acme Controls "M1" Acme'

    def test_brand_fallback_when_no_manufacturer(self):
        product = ProductIdentity(brand="Acme", mpn="M1")
        assert build_search_query(product) == 'Acme "M1"'

    def test_description_tokens_when_no_mpn(self):
        product = ProductIdentity(
            manufacturer="Acme Controls",
            raw_description="The amazing industrial control valve",
        )
        assert build_search_query(product) == (
            "Acme Controls amazing industrial control valve"
        )

    def test_description_excludes_identity_words_and_stopwords(self):
        product = ProductIdentity(
            manufacturer="Acme Controls",
            raw_description="Acme M1 is the best valve",
        )
        assert build_search_query(product) == "Acme Controls best valve"

    def test_empty_identity_yields_empty_query(self):
        assert build_search_query(ProductIdentity()) == ""


# ------------------------------------------------------------ search API client --

class TestSearchApiClient:
    def test_manufacturer_result_parsed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/search"
            assert request.headers["X-API-KEY"] == "test-key"
            body = request.read().decode()
            assert '"num":10' in body
            return httpx.Response(
                200,
                json=organic_payload(
                    {
                        "title": "M1 Controller",
                        "link": "https://acme-controls.example/products/m1",
                        "snippet": "official product page",
                    }
                ),
            )

        results = mock_client(handler).search('Acme Controls "M1"')
        assert len(results) == 1
        assert results[0].url == "https://acme-controls.example/products/m1"
        assert results[0].title == "M1 Controller"
        assert results[0].snippet == "official product page"

    def test_no_results_returns_empty_list(self):
        handler = lambda request: httpx.Response(200, json=organic_payload())
        assert mock_client(handler).search("anything") == []

    def test_malformed_response_missing_organic_raises(self):
        handler = lambda request: httpx.Response(200, json={"foo": 1})
        with pytest.raises(ProviderInvalidResponseError, match="organic"):
            mock_client(handler).search("x")

    def test_malformed_response_organic_not_list_raises(self):
        handler = lambda request: httpx.Response(200, json={"organic": "x"})
        with pytest.raises(ProviderInvalidResponseError, match="not a list"):
            mock_client(handler).search("x")

    def test_non_json_response_raises(self):
        handler = lambda request: httpx.Response(200, content=b"<html>")
        with pytest.raises(ProviderInvalidResponseError, match="non-JSON"):
            mock_client(handler).search("x")

    def test_item_without_link_skipped_not_fatal(self):
        handler = lambda request: httpx.Response(
            200,
            json=organic_payload(
                {"title": "no link here"},
                {"title": "ok", "link": "https://acme-controls.example/m1"},
                "not-a-dict",
            ),
        )
        results = mock_client(handler).search("x")
        assert [r.url for r in results] == [
            "https://acme-controls.example/m1"
        ]

    def test_http_error_maps_to_unavailable(self):
        handler = lambda request: httpx.Response(500)
        with pytest.raises(ProviderUnavailableError, match="HTTP 500"):
            mock_client(handler).search("x")

    def test_timeout_maps_to_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out")

        with pytest.raises(ProviderUnavailableError, match="timed out"):
            mock_client(handler).search("x")

    def test_connection_error_maps_to_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(ProviderUnavailableError, match="unreachable"):
            mock_client(handler).search("x")

    def test_api_key_required_at_construction(self):
        with pytest.raises(ProviderConfigurationError, match="API_KEY"):
            SearchApiClient(api_key="  ")

    def test_results_limit_sent_to_api(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = request.read().decode()
            assert '"num":3' in body
            return httpx.Response(200, json=organic_payload())

        mock_client(handler, results_limit=3).search("x")


# ------------------------------------------------------------------ provider --

class TestSearchProvider:
    def test_from_settings_requires_api_key(self, monkeypatch):
        monkeypatch.setattr(settings, "search_provider_api_key", "")
        with pytest.raises(ProviderConfigurationError, match="API_KEY"):
            SearchProvider.from_settings(settings)

    def test_from_settings_builds_from_env(self, monkeypatch):
        monkeypatch.setattr(settings, "search_provider_api_key", "secret")
        monkeypatch.setattr(
            settings, "search_provider_base_url", "https://mock.example"
        )
        provider = SearchProvider.from_settings(settings)
        assert provider.name == "search"
        assert provider.kind == DiscoveryMethod.SEARCH

    def test_discover_builds_candidates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=organic_payload(
                    {
                        "title": "M1 Controller",
                        "link": "https://acme-controls.example/products/m1",
                        "snippet": "official",
                    },
                    {
                        "title": "M1 datasheet",
                        "link": "https://acme-controls.example/docs/m1.pdf",
                        "snippet": "",
                    },
                    {
                        "title": "M1 manual",
                        "link": "https://acme-controls.example/docs/m1-manual.pdf",
                        "snippet": "",
                    },
                ),
            )

        candidates = provider_with(handler).discover(
            make_product(), DiscoveryContext(product=make_product())
        )
        assert len(candidates) == 3
        first = candidates[0]
        assert first.id.startswith("search-")
        assert first.url == "https://acme-controls.example/products/m1"
        assert first.discovery_method == DiscoveryMethod.SEARCH
        assert first.status == CandidateStatus.PENDING  # policy decides next
        assert first.domain == "acme-controls.example"
        assert first.source_type == SourceType.MANUFACTURER_PRODUCT_PAGE
        assert (
            candidates[1].source_type == SourceType.MANUFACTURER_TECHNICAL_PDF
        )
        assert candidates[2].source_type == SourceType.MANUFACTURER_MANUAL
        assert first.manufacturer_relationship == ManufacturerRelationship.UNKNOWN
        assert first.trust_level == SourceTrustLevel.UNVERIFIED

    def test_candidate_ids_are_deterministic(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=organic_payload(
                    {"title": "t", "link": "https://acme-controls.example/m1"}
                ),
            )

        provider = provider_with(handler)
        first = provider.discover(
            make_product(), DiscoveryContext(product=make_product())
        )
        second = provider.discover(
            make_product(), DiscoveryContext(product=make_product())
        )
        assert first[0].id == second[0].id

    def test_non_http_urls_skipped(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=organic_payload(
                    {"title": "mail", "link": "mailto:sales@acme.example"},
                    {"title": "file", "link": "file:///C:/m1.pdf"},
                    {"title": "good", "link": "https://acme-controls.example/m1"},
                ),
            )

        candidates = provider_with(handler).discover(
            make_product(), DiscoveryContext(product=make_product())
        )
        assert [c.url for c in candidates] == [
            "https://acme-controls.example/m1"
        ]

    def test_empty_identity_returns_no_candidates_and_calls_nothing(self):
        called = {"hit": False}

        def handler(request: httpx.Request) -> httpx.Response:
            called["hit"] = True
            return httpx.Response(200, json=organic_payload())

        provider = provider_with(handler)
        assert (
            provider.discover(
                ProductIdentity(), DiscoveryContext(product=ProductIdentity())
            )
            == []
        )
        assert called["hit"] is False  # no API call for an empty query


# ------------------------------------------------- discovery flow (policy+ranking) --

class TestDiscoveryFlowWithSearchProvider:
    def make_rich_handler(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=organic_payload(
                    {
                        "title": "M1 Controller",
                        "link": "https://acme-controls.example/products/m1",
                        "snippet": "",
                    },
                    {
                        "title": "M1 Controller on Amazon",
                        "link": "https://www.amazon.com/dp/B0000001",
                        "snippet": "",
                    },
                    {
                        "title": "random shop",
                        "link": "https://random-shop.example.com/item/1",
                        "snippet": "",
                    },
                ),
            )

        return handler

    def test_manufacturer_result_allowed_marketplace_and_unknown_rejected(self):
        provider = provider_with(self.make_rich_handler())
        result = run_discovery(
            make_product(),
            providers=[provider],
            context=DiscoveryContext(
                product=make_product(), manufacturer_domains=ACME_DOMAINS
            ),
        )

        assert result.total_discovered == 3
        assert len(result.candidates) == 1
        allowed = result.candidates[0]
        assert allowed.url == "https://acme-controls.example/products/m1"
        assert allowed.status == CandidateStatus.ALLOWED
        assert allowed.manufacturer_relationship == ManufacturerRelationship.OWNED
        assert allowed.trust_level == SourceTrustLevel.MANUFACTURER_OFFICIAL

        assert len(result.rejected) == 2
        by_domain = {c.domain: c for c in result.rejected}
        assert by_domain["amazon.com"].status == CandidateStatus.PROHIBITED
        assert "marketplace" in by_domain["amazon.com"].rejection_reason
        assert (
            by_domain["random-shop.example.com"].status
            == CandidateStatus.REJECTED
        )
        assert "unknown external domain" in by_domain[
            "random-shop.example.com"
        ].rejection_reason

    def test_search_results_are_never_pre_trusted(self):
        """Provider emits PENDING; ONLY the policy grants ALLOWED."""
        provider = provider_with(self.make_rich_handler())
        result = run_discovery(
            make_product(),
            providers=[provider],
            context=DiscoveryContext(
                product=make_product(),
                policy_config=SourcePolicyConfig(manufacturer_domains=[]),
            ),
        )
        assert result.candidates == []
        assert result.total_discovered == 3
        assert len(result.rejected) == 3

    def test_exact_mpn_match_ranks_first_among_allowed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=organic_payload(
                    {
                        "title": "Acme controller without part number",
                        "link": "https://acme-controls.example/products/controller",
                        "snippet": "",
                    },
                    {
                        "title": "Acme M1 - the exact part number",
                        "link": "https://acme-controls.example/products/M1",
                        "snippet": "",
                    },
                ),
            )

        provider = provider_with(handler)
        product = make_product(mpn="M1")
        result = run_discovery(
            product,
            providers=[provider],
            context=DiscoveryContext(product=product, manufacturer_domains=ACME_DOMAINS),
        )
        assert len(result.candidates) == 2
        assert result.candidates[0].url == "https://acme-controls.example/products/M1"
        assert result.candidates[0].relevance_score > (
            result.candidates[1].relevance_score
        )

    def test_provider_timeout_recorded_not_fatal(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("boom")

        provider = provider_with(handler)
        result = run_discovery(
            make_product(),
            providers=[provider],
            context=DiscoveryContext(product=make_product()),
        )
        assert result.total_discovered == 0
        assert result.candidates == []
        # Pass 1 allowed nothing, so pass 2 also ran and failed; both typed
        # errors are recorded - discovery never aborts, never fabricates.
        assert len(result.provider_errors) == 2
        error = result.provider_errors[0]
        assert error.provider_name == "search"
        assert error.error_kind == "unavailable"
        assert "timed out" in error.message

    def test_no_results_no_error(self):
        handler = lambda request: httpx.Response(200, json=organic_payload())
        provider = provider_with(handler)
        result = run_discovery(
            make_product(),
            providers=[provider],
            context=DiscoveryContext(product=make_product()),
        )
        assert result.total_discovered == 0
        assert result.provider_errors == []

    def test_discovery_continues_after_provider_failure(self):
        class HealthyProvider:
            name = "fixture-healthy"
            kind = DiscoveryMethod.DIRECT_URL

            def discover(self, product, context):
                from app.sources.candidates import SourceCandidate

                return [
                    SourceCandidate(
                        url="https://acme-controls.example/products/m1",
                        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
                        title="M1 Controller",
                        discovery_method=DiscoveryMethod.DIRECT_URL,
                    )
                ]

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        product = make_product()
        result = run_discovery(
            product,
            providers=[provider_with(handler), HealthyProvider()],
            context=DiscoveryContext(
                product=product, manufacturer_domains=ACME_DOMAINS
            ),
        )
        assert result.total_discovered == 1
        assert len(result.candidates) == 1
        assert len(result.provider_errors) == 1


# ------------------------------------------------------------ provider selection --

class TestProviderSelection:
    def test_unset_discovery_provider_uses_registry(self):
        assert providers_from_settings() == list(PROVIDERS)

    def test_search_selected_with_key(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_provider", "search")
        monkeypatch.setattr(settings, "search_provider_api_key", "secret")
        providers = providers_from_settings()
        assert len(providers) == 1
        assert providers[0].name == "search"
        assert isinstance(providers[0], SearchProvider)

    def test_search_selected_without_key_raises_lazily(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_provider", "search")
        monkeypatch.setattr(settings, "search_provider_api_key", "")
        with pytest.raises(ProviderConfigurationError, match="API_KEY"):
            providers_from_settings()

    def test_unknown_provider_name_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_provider", "bogus")
        with pytest.raises(ProviderConfigurationError, match="unknown discovery provider"):
            providers_from_settings()

    def test_run_discovery_uses_selected_provider(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_provider", "search")
        monkeypatch.setattr(settings, "search_provider_api_key", "secret")

        def fake_from_settings(s=None):
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    json=organic_payload(
                        {
                            "title": "M1 Controller",
                            "link": "https://acme-controls.example/products/m1",
                            "snippet": "",
                        }
                    ),
                )

            return SearchProvider(mock_client(handler))

        monkeypatch.setattr(SearchProvider, "from_settings", staticmethod(fake_from_settings))
        product = make_product()
        result = run_discovery(
            product,
            context=DiscoveryContext(
                product=product, manufacturer_domains=ACME_DOMAINS
            ),
        )
        # providers=None -> DISCOVERY_PROVIDER=search -> the search provider.
        assert result.total_discovered == 1
        assert result.candidates[0].domain == "acme-controls.example"
        assert result.candidates[0].status == CandidateStatus.ALLOWED

    def test_comma_separated_returns_multiple_providers(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_provider", "search,gemini")
        monkeypatch.setattr(settings, "search_provider_api_key", "serper-key")
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "gemini-key")
        providers = providers_from_settings()
        assert len(providers) == 2
        assert providers[0].name == "search"
        assert providers[1].name == "gemini"

    def test_comma_separated_preserves_order(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_provider", "gemini,search")
        monkeypatch.setattr(settings, "search_provider_api_key", "serper-key")
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "gemini-key")
        providers = providers_from_settings()
        assert len(providers) == 2
        assert providers[0].name == "gemini"
        assert providers[1].name == "search"

    def test_comma_separated_deduplicates(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_provider", "search,search")
        monkeypatch.setattr(settings, "search_provider_api_key", "serper-key")
        providers = providers_from_settings()
        assert len(providers) == 1
        assert providers[0].name == "search"

    def test_comma_separated_strips_whitespace_and_empties(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_provider", " search , , gemini ")
        monkeypatch.setattr(settings, "search_provider_api_key", "serper-key")
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "gemini-key")
        providers = providers_from_settings()
        assert len(providers) == 2
        assert providers[0].name == "search"
        assert providers[1].name == "gemini"

    def test_comma_separated_unknown_name_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_provider", "search,bogus")
        monkeypatch.setattr(settings, "search_provider_api_key", "serper-key")
        with pytest.raises(ProviderConfigurationError, match="unknown discovery provider"):
            providers_from_settings()


class TestProviderErrorInfo:
    def test_kind_mirrors_error_class(self):
        error = ProviderErrorInfo(
            provider_name="search",
            error_kind="configuration",
            message="no key",
        )
        assert error.provider_name == "search"
        assert error.error_kind == "configuration"
        assert isinstance(ProviderConfigurationError("search", "no key"), ProviderError)
