"""Tests for the Gemini search discovery provider (Step 11).

TEST FIXTURES: deterministic, made-up domains/products (acme-controls.example
etc.) used ONLY to exercise the provider -> policy -> ranking flow. These are
NOT UniHack data and NOT real manufacturers - do not treat them as ground
truth.

ZERO network calls: the Gemini API client is driven by httpx.MockTransport
with canned JSON responses. The real provider is NEVER executed here.

The Gemini grounding API returns results under candidates[*].groundingMetadata
.groundingChunks[*].web.{uri,title}; every test here feeds that exact shape.
"""

import httpx
import json
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
    ProviderErrorInfo,
    ProviderInvalidResponseError,
    ProviderUnavailableError,
    providers_from_settings,
    run_discovery,
)
from app.sources.policy import SourcePolicyConfig
from app.sources.providers.gemini_search import (
    GeminiSearchApiClient,
    GeminiSearchProvider,
    build_search_query,
    guess_source_type,
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


def mock_client(handler) -> GeminiSearchApiClient:
    """GeminiSearchApiClient pinned to an offline httpx.MockTransport."""
    client = GeminiSearchApiClient(api_key="test-key")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def grounding_payload(*chunks: dict) -> dict:
    """Build a Gemini generateContent response with groundingChunks[*].web."""
    return {
        "candidates": [
            {
                "groundingMetadata": {
                    "groundingChunks": [{"web": c} for c in chunks]
                }
            }
        ]
    }


def provider_with(handler) -> GeminiSearchProvider:
    return GeminiSearchProvider(mock_client(handler))


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

    def test_empty_identity_yields_empty_query(self):
        assert build_search_query(ProductIdentity()) == ""


# ------------------------------------------------------------ gemini API client --


class TestGeminiSearchApiClient:
    def test_grounding_response_parsed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=grounding_payload(
                    {"uri": "https://acme-controls.example/m1", "title": "M1"},
                ),
            )

        chunks = mock_client(handler).grounding_request('Acme Controls "M1"')
        meta = chunks["candidates"][0]["groundingMetadata"]["groundingChunks"]
        assert meta[0]["web"]["uri"] == "https://acme-controls.example/m1"

    def test_no_grounding_chunks_returns_structure(self):
        handler = lambda request: httpx.Response(
            200, json={"candidates": [{"groundingMetadata": {}}]}
        )
        result = mock_client(handler).grounding_request("anything")
        assert "candidates" in result

    def test_http_401_maps_to_configuration_error(self):
        handler = lambda request: httpx.Response(401)
        with pytest.raises(ProviderConfigurationError, match="API key"):
            mock_client(handler).grounding_request("x")

    def test_http_429_maps_to_unavailable(self):
        handler = lambda request: httpx.Response(429)
        with pytest.raises(ProviderUnavailableError, match="rate limit"):
            mock_client(handler).grounding_request("x")

    def test_http_5xx_maps_to_unavailable(self):
        handler = lambda request: httpx.Response(503)
        with pytest.raises(ProviderUnavailableError):
            mock_client(handler).grounding_request("x")

    def test_non_json_response_raises(self):
        handler = lambda request: httpx.Response(200, content=b"<html>")
        with pytest.raises(ProviderInvalidResponseError, match="non-JSON"):
            mock_client(handler).grounding_request("x")

    def test_timeout_maps_to_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out")

        with pytest.raises(ProviderUnavailableError, match="timed out"):
            mock_client(handler).grounding_request("x")

    def test_api_key_required_at_construction(self):
        with pytest.raises(ProviderConfigurationError, match="GEMINI_API_KEY"):
            GeminiSearchApiClient(api_key="  ")

    def test_api_key_never_in_repr(self):
        client = GeminiSearchApiClient(api_key="sk-secret-12345")
        assert "sk-secret-12345" not in repr(client)


# ---------------------------------------------------------- request payload --


class TestRequestPayload:
    def test_payload_contains_tools_google_search_retrieval(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.read().decode())
            return httpx.Response(200, json=grounding_payload())

        mock_client(handler).grounding_request('Acme Controls "M1"')
        body = captured["body"]
        assert "tools" in body
        assert {"google_search_retrieval": {}} in body["tools"]

    def test_payload_does_not_contain_function_call(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.read().decode())
            return httpx.Response(200, json=grounding_payload())

        mock_client(handler).grounding_request('Acme Controls "M1"')
        body_str = json.dumps(captured["body"])
        assert "function_call" not in body_str

    def test_model_appears_in_request_url(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json=grounding_payload())

        mock_client(handler).grounding_request('Acme Controls "M1"')
        assert "/v1beta/models/gemini-flash-latest:generateContent" in captured["url"]


class TestModelConfigurability:
    def test_default_model_is_gemini_flash_latest(self):
        from app.sources.providers.gemini_search import DEFAULT_MODEL

        assert DEFAULT_MODEL == "gemini-flash-latest"

    def test_model_from_settings_used_in_url(self, monkeypatch):
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "secret")
        monkeypatch.setattr(settings, "GEMINI_MODEL", "custom-model-v1")
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json=grounding_payload())

        provider = GeminiSearchProvider.from_settings(settings)
        provider._api_client._client = httpx.Client(
            transport=httpx.MockTransport(handler)
        )
        provider._api_client.grounding_request('Acme Controls "M1"')
        assert "/v1beta/models/custom-model-v1:generateContent" in captured["url"]


class TestHttpErrors:
    def test_http_400_maps_to_invalid_response(self):
        handler = lambda request: httpx.Response(400)
        with pytest.raises(ProviderInvalidResponseError, match="HTTP 400"):
            mock_client(handler).grounding_request("x")


# ------------------------------------------------------------------ provider --


class TestGeminiSearchProvider:
    def test_from_settings_requires_api_key(self, monkeypatch):
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
        with pytest.raises(ProviderConfigurationError, match="GEMINI_API_KEY"):
            GeminiSearchProvider.from_settings(settings)

    def test_from_settings_builds_from_env(self, monkeypatch):
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "secret")
        monkeypatch.setattr(settings, "GEMINI_BASE_URL", "https://mock.example")
        provider = GeminiSearchProvider.from_settings(settings)
        assert provider.name == "gemini"
        assert provider.kind == DiscoveryMethod.SEARCH

    def test_discover_builds_candidates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=grounding_payload(
                    {"uri": "https://acme-controls.example/products/m1",
                     "title": "M1 Controller"},
                    {"uri": "https://acme-controls.example/docs/m1.pdf",
                     "title": "M1 datasheet"},
                ),
            )

        candidates = provider_with(handler).discover(
            make_product(), DiscoveryContext(product=make_product())
        )
        assert len(candidates) == 2
        first = candidates[0]
        assert first.id.startswith("gemini-")
        assert first.url == "https://acme-controls.example/products/m1"
        assert first.discovery_method == DiscoveryMethod.SEARCH
        assert first.status == CandidateStatus.PENDING  # policy decides next
        assert first.domain == "acme-controls.example"
        assert first.source_type == SourceType.MANUFACTURER_PRODUCT_PAGE
        assert (
            candidates[1].source_type == SourceType.MANUFACTURER_TECHNICAL_PDF
        )
        assert first.manufacturer_relationship == ManufacturerRelationship.UNKNOWN
        assert first.trust_level == SourceTrustLevel.UNVERIFIED

    def test_candidate_ids_are_deterministic(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=grounding_payload(
                    {"uri": "https://acme-controls.example/m1", "title": "t"}
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
                json=grounding_payload(
                    {"uri": "mailto:sales@acme.example", "title": "mail"},
                    {"uri": "file:///C:/m1.pdf", "title": "file"},
                    {"uri": "https://acme-controls.example/m1", "title": "good"},
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
            return httpx.Response(200, json=grounding_payload())

        provider = provider_with(handler)
        assert (
            provider.discover(
                ProductIdentity(), DiscoveryContext(product=ProductIdentity())
            )
            == []
        )
        assert called["hit"] is False  # no API call for an empty query


# ------------------------------------------------- discovery flow (policy+ranking) --


class TestDiscoveryFlowWithGeminiProvider:
    def make_rich_handler(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=grounding_payload(
                    {"uri": "https://acme-controls.example/products/m1",
                     "title": "M1 Controller"},
                    {"uri": "https://www.amazon.com/dp/B0000001",
                     "title": "M1 on Amazon"},
                    {"uri": "https://random-shop.example.com/item/1",
                     "title": "random shop"},
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

    def test_gemini_results_are_never_pre_trusted(self):
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
        assert error.provider_name == "gemini"
        assert error.error_kind == "unavailable"
        assert "timed out" in error.message

    def test_no_results_no_error(self):
        handler = lambda request: httpx.Response(200, json=grounding_payload())
        provider = provider_with(handler)
        result = run_discovery(
            make_product(),
            providers=[provider],
            context=DiscoveryContext(product=make_product()),
        )
        assert result.total_discovered == 0
        assert result.provider_errors == []


# ------------------------------------------------------------ provider selection --


class TestProviderSelection:
    def test_unset_discovery_provider_uses_registry(self):
        assert providers_from_settings() == list(PROVIDERS)

    def test_gemini_selected_with_key(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_provider", "gemini")
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "secret")
        providers = providers_from_settings()
        assert len(providers) == 1
        assert providers[0].name == "gemini"
        assert isinstance(providers[0], GeminiSearchProvider)

    def test_gemini_selected_without_key_raises_lazily(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_provider", "gemini")
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
        with pytest.raises(ProviderConfigurationError, match="GEMINI_API_KEY"):
            providers_from_settings()

    def test_unknown_provider_name_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_provider", "bogus")
        with pytest.raises(ProviderConfigurationError, match="unknown discovery provider"):
            providers_from_settings()

    def test_run_discovery_uses_gemini_provider(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_provider", "gemini")
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "secret")

        def fake_from_settings(s=None):
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    json=grounding_payload(
                        {"uri": "https://acme-controls.example/products/m1",
                         "title": "M1 Controller"}
                    ),
                )

            return GeminiSearchProvider(mock_client(handler))

        monkeypatch.setattr(
            GeminiSearchProvider, "from_settings", staticmethod(fake_from_settings)
        )
        product = make_product()
        result = run_discovery(
            product,
            context=DiscoveryContext(
                product=product, manufacturer_domains=ACME_DOMAINS
            ),
        )
        # providers=None -> DISCOVERY_PROVIDER=gemini -> the gemini provider.
        assert result.total_discovered == 1
        assert result.candidates[0].domain == "acme-controls.example"
        assert result.candidates[0].status == CandidateStatus.ALLOWED


class TestProviderErrorInfo:
    def test_kind_mirrors_error_class(self):
        error = ProviderErrorInfo(
            provider_name="gemini",
            error_kind="configuration",
            message="no key",
        )
        assert error.provider_name == "gemini"
        assert error.error_kind == "configuration"
        assert isinstance(
            ProviderConfigurationError("gemini", "no key"), Exception
        )
