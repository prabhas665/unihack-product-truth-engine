"""Tests for the Groq Web Search discovery provider (Step 12B).

TEST FIXTURES: deterministic, made-up domains/products (acme-controls.example
etc.) used ONLY to exercise the provider -> policy -> ranking flow. These are
NOT UniHack data and NOT real manufacturers - do not treat them as ground
truth.

ZERO network calls: the Groq API client is driven by httpx.MockTransport
with canned JSON responses. The real provider is NEVER executed here, and the
GROQ_API_KEY value is never inspected, printed, or sent anywhere but the
mock transport.

Groq returns web results under message.executed_tools[*].search_results[*]
with {title, url, content, score}; every test feeds that exact shape.
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
from app.sources.providers.groq_search import (
    GroqSearchApiClient,
    GroqSearchProvider,
    GroqSearchResult,
    _parse_search_results,
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


def mock_client(handler, *, api_key: str = "test-key") -> GroqSearchApiClient:
    """GroqSearchApiClient pinned to an offline httpx.MockTransport."""
    return GroqSearchApiClient(
        api_key=api_key,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def search_payload(*results: dict) -> dict:
    """Build a Groq chat/completions response with executed_tools search_results."""
    return {
        "message": {
            "role": "assistant",
            "content": "Synthesized answer that must NEVER become a candidate.",
            "executed_tools": [
                {
                    "type": "web_search",
                    "search_results": list(results),
                }
            ],
        }
    }


def provider_with(handler) -> GroqSearchProvider:
    return GroqSearchProvider(mock_client(handler))


# ------------------------------------------------------------- query builder --


class TestQueryBuilder:
    def test_exact_mpn_quoted_and_preferred(self):
        product = ProductIdentity(manufacturer="Freud Inc", mpn="DCB518ASTS06G")
        assert build_search_query(product) == 'Freud Inc "DCB518ASTS06G"'

    def test_brand_included_when_distinct(self):
        product = ProductIdentity(manufacturer="Acme Controls", brand="Acme", mpn="M1")
        assert build_search_query(product) == 'Acme Controls "M1" Acme'

    def test_brand_fallback_when_no_manufacturer(self):
        product = ProductIdentity(brand="Acme", mpn="M1")
        assert build_search_query(product) == 'Acme "M1"'

    def test_empty_identity_yields_empty_query(self):
        assert build_search_query(ProductIdentity()) == ""


# ------------------------------------------------------------ groq API client --


class TestGroqSearchApiClient:
    def test_endpoint_is_openai_chat_completions(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json=search_payload())

        mock_client(handler).web_search('Acme Controls "M1"')
        assert captured["url"].endswith("/openai/v1/chat/completions")

    def test_bearer_authentication(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("Authorization")
            captured["version"] = request.headers.get("Groq-Model-Version")
            return httpx.Response(200, json=search_payload())

        mock_client(handler).web_search('Acme Controls "M1"')
        assert captured["auth"] == "Bearer test-key"
        assert captured["version"] == "2025-07-23"

    def test_model_in_body(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.read().decode())
            return httpx.Response(200, json=search_payload())

        mock_client(handler).web_search('Acme Controls "M1"')
        assert captured["body"]["model"] == "groq/compound-mini"

    def test_request_body_shape(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.read().decode())
            return httpx.Response(200, json=search_payload())

        mock_client(handler).web_search('Acme Controls "M1"')
        body = captured["body"]
        assert body["messages"] == [
            {"role": "user", "content": 'Acme Controls "M1"'}
        ]
        # Groq rejects a web_search tool_choice object; only auto/none or a
        # function-style object are accepted, so the request omits tool_choice.
        assert "tool_choice" not in body
        assert body["n"] == 1
        assert body["temperature"] == 0

    def test_no_web_search_tools_block(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.read().decode())
            return httpx.Response(200, json=search_payload())

        mock_client(handler).web_search('Acme Controls "M1"')
        # Groq's OpenAI-compatible endpoint rejects the web_search tool primitive
        # (tools[].type must be function|mcp) and any tool_choice object, so the
        # provider sends a plain chat request and relies on native agentic search.
        assert "tools" not in captured["body"]
        assert "tool_choice" not in captured["body"]

    def test_search_settings_max_results(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.read().decode())
            return httpx.Response(200, json=search_payload())

        mock_client(handler).web_search('Acme Controls "M1"')
        # max_results / include_domains are no longer sent via a tools block.
        assert "tools" not in captured["body"]

    def test_include_domains_from_context(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.read().decode())
            return httpx.Response(200, json=search_payload())

        client = mock_client(handler)
        client.web_search(
            'Acme Controls "M1"',
            include_domains=["MakitaTools.com", "acme-controls.example"],
        )
        # include_domains cannot be passed to the search API for this model;
        # SourcePolicy enforces the restriction instead.
        assert "tools" not in captured["body"]

    def test_include_domains_empty_when_unknown(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.read().decode())
            return httpx.Response(200, json=search_payload())

        mock_client(handler).web_search('Acme Controls "M1"', include_domains=[])
        assert "tools" not in captured["body"]

    def test_successful_search_results_parsing(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=search_payload(
                    {
                        "title": "M1 Controller",
                        "url": "https://acme-controls.example/products/m1",
                        "content": "datasheet text",
                        "score": 0.91,
                    }
                ),
            )

        results = mock_client(handler).web_search('Acme Controls "M1"')
        assert len(results) == 1
        r = results[0]
        assert isinstance(r, GroqSearchResult)
        assert r.url == "https://acme-controls.example/products/m1"
        assert r.title == "M1 Controller"
        assert r.content == "datasheet text"
        assert r.score == 0.91

    def test_title_url_content_score_parsing(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=search_payload(
                    {
                        "title": "T",
                        "url": "https://acme-controls.example/x",
                        "content": "C",
                        "score": 0.5,
                    }
                ),
            )

        r = mock_client(handler).web_search("q")[0]
        assert r.title == "T"
        assert r.url == "https://acme-controls.example/x"
        assert r.content == "C"
        assert r.score == 0.5

    def test_multiple_results(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=search_payload(
                    {"title": "a", "url": "https://acme-controls.example/a"},
                    {"title": "b", "url": "https://acme-controls.example/b"},
                    {"title": "c", "url": "https://acme-controls.example/c"},
                ),
            )

        results = mock_client(handler).web_search("q")
        assert [r.url for r in results] == [
            "https://acme-controls.example/a",
            "https://acme-controls.example/b",
            "https://acme-controls.example/c",
        ]

    def test_results_limit_enforced(self):
        many = [
            {"title": str(i), "url": f"https://acme-controls.example/{i}"}
            for i in range(15)
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=search_payload(*many))

        results = mock_client(handler).web_search("q")
        assert len(results) == 10

    def test_empty_results(self):
        handler = lambda request: httpx.Response(200, json=search_payload())
        assert mock_client(handler).web_search("q") == []

    def test_malformed_response_raises(self):
        handler = lambda request: httpx.Response(200, content=b"<html>not json")
        with pytest.raises(ProviderInvalidResponseError, match="non-JSON"):
            mock_client(handler).web_search("q")

    def test_missing_executed_tools_returns_empty(self):
        handler = lambda request: httpx.Response(200, json={"message": {"content": "x"}})
        assert mock_client(handler).web_search("q") == []

    def test_missing_search_results_returns_empty(self):
        handler = lambda request: httpx.Response(
            200,
            json={"message": {"executed_tools": [{"type": "web_search"}]}},
        )
        assert mock_client(handler).web_search("q") == []

    def test_non_web_search_tool_type_is_accepted(self):
        """Groq may emit executed_tools with a non-'web_search' type."""
        handler = lambda request: httpx.Response(
            200,
            json={
                "message": {
                    "executed_tools": [
                        {
                            "type": "search",
                            "search_results": [
                                {"title": "T", "url": "https://acme-controls.example/x"}
                            ],
                        }
                    ]
                }
            },
        )
        results = mock_client(handler).web_search("q")
        assert [r.url for r in results] == ["https://acme-controls.example/x"]

    def test_search_results_as_dict_with_results_key(self):
        """Groq can nest results under search_results.results."""
        handler = lambda request: httpx.Response(
            200,
            json={
                "message": {
                    "executed_tools": [
                        {
                            "type": "web_search",
                            "search_results": {
                                "results": [
                                    {
                                        "title": "T",
                                        "url": "https://acme-controls.example/x",
                                    }
                                ]
                            },
                        }
                    ]
                }
            },
        )
        results = mock_client(handler).web_search("q")
        assert [r.url for r in results] == ["https://acme-controls.example/x"]

    def test_http_400_maps_to_invalid_response(self):
        handler = lambda request: httpx.Response(400)
        with pytest.raises(ProviderInvalidResponseError, match="HTTP 400"):
            mock_client(handler).web_search("q")

    def test_http_401_maps_to_configuration_error(self):
        handler = lambda request: httpx.Response(401)
        with pytest.raises(ProviderConfigurationError, match="API key"):
            mock_client(handler).web_search("q")

    def test_http_403_maps_to_configuration_error(self):
        handler = lambda request: httpx.Response(403)
        with pytest.raises(ProviderConfigurationError):
            mock_client(handler).web_search("q")

    def test_http_429_maps_to_unavailable(self):
        handler = lambda request: httpx.Response(429)
        with pytest.raises(ProviderUnavailableError, match="rate limit"):
            mock_client(handler).web_search("q")

    def test_http_500_maps_to_unavailable(self):
        handler = lambda request: httpx.Response(503)
        with pytest.raises(ProviderUnavailableError):
            mock_client(handler).web_search("q")

    def test_timeout_maps_to_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out")

        with pytest.raises(ProviderUnavailableError, match="timed out"):
            mock_client(handler).web_search("q")

    def test_connection_failure_maps_to_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        with pytest.raises(ProviderUnavailableError, match="unreachable"):
            mock_client(handler).web_search("q")

    def test_missing_api_key_at_construction(self):
        with pytest.raises(ProviderConfigurationError, match="GROQ_API_KEY"):
            GroqSearchApiClient(api_key="  ")

    def test_api_key_never_in_repr(self):
        client = GroqSearchApiClient(api_key="sk-secret-12345")
        assert "sk-secret-12345" not in repr(client)

    def test_api_key_never_in_error_text(self):
        handler = lambda request: httpx.Response(401)
        with pytest.raises(ProviderConfigurationError) as exc:
            mock_client(handler, api_key="super-secret-key").web_search("q")
        assert "super-secret-key" not in str(exc.value)


# ------------------------------------------------------------ provider --


class TestGroqSearchProvider:
    def test_from_settings_requires_api_key(self, monkeypatch):
        monkeypatch.setattr(settings, "GROQ_API_KEY", "")
        with pytest.raises(ProviderConfigurationError, match="GROQ_API_KEY"):
            GroqSearchProvider.from_settings(settings)

    def test_from_settings_builds_from_env(self, monkeypatch):
        monkeypatch.setattr(settings, "GROQ_API_KEY", "secret")
        monkeypatch.setattr(settings, "GROQ_BASE_URL", "https://mock.example")
        provider = GroqSearchProvider.from_settings(settings)
        assert provider.name == "groq"
        assert provider.kind == DiscoveryMethod.SEARCH

    def test_discover_builds_candidates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=search_payload(
                    {
                        "title": "M1 Controller",
                        "url": "https://acme-controls.example/products/m1",
                        "content": "x",
                        "score": 0.9,
                    },
                    {
                        "title": "M1 datasheet",
                        "url": "https://acme-controls.example/docs/m1.pdf",
                        "content": "y",
                        "score": 0.8,
                    },
                ),
            )

        candidates = provider_with(handler).discover(
            make_product(), DiscoveryContext(product=make_product())
        )
        assert len(candidates) == 2
        first = candidates[0]
        assert first.id.startswith("groq-")
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
                json=search_payload(
                    {"title": "t", "url": "https://acme-controls.example/m1"}
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
                json=search_payload(
                    {"title": "mail", "url": "mailto:sales@acme.example"},
                    {"title": "file", "url": "file:///C:/m1.pdf"},
                    {"title": "ftp", "url": "ftp://acme.example/m1"},
                    {"title": "good", "url": "https://acme-controls.example/m1"},
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
            return httpx.Response(200, json=search_payload())

        provider = provider_with(handler)
        assert (
            provider.discover(
                ProductIdentity(), DiscoveryContext(product=ProductIdentity())
            )
            == []
        )
        assert called["hit"] is False  # no API call for an empty query

    def test_no_silent_fallback_on_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        provider = provider_with(handler)
        with pytest.raises(ProviderUnavailableError):
            provider.discover(
                make_product(), DiscoveryContext(product=make_product())
            )


# ------------------------------------------------- discovery flow (policy+ranking) --


class TestDiscoveryFlowWithGroqProvider:
    def make_rich_handler(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=search_payload(
                    {
                        "title": "M1 Controller",
                        "url": "https://acme-controls.example/products/m1",
                    },
                    {"title": "M1 on Amazon", "url": "https://www.amazon.com/dp/B0000001"},
                    {
                        "title": "random shop",
                        "url": "https://random-shop.example.com/item/1",
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
            by_domain["random-shop.example.com"].status == CandidateStatus.REJECTED
        )
        assert "unknown external domain" in by_domain[
            "random-shop.example.com"
        ].rejection_reason

    def test_groq_results_are_never_pre_trusted(self):
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
        assert len(result.provider_errors) == 1
        error = result.provider_errors[0]
        assert error.provider_name == "groq"
        assert error.error_kind == "unavailable"
        assert "timed out" in error.message
        assert "test-key" not in error.message

    def test_no_results_no_error(self):
        handler = lambda request: httpx.Response(200, json=search_payload())
        provider = provider_with(handler)
        result = run_discovery(
            make_product(),
            providers=[provider],
            context=DiscoveryContext(product=make_product()),
        )
        assert result.total_discovered == 0
        assert result.provider_errors == []

    def test_manufacturer_domain_sent_as_site_hint_not_tools(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.read().decode())
            return httpx.Response(200, json=search_payload())

        provider = GroqSearchProvider(mock_client(handler))
        provider.discover(
            make_product(),
            DiscoveryContext(
                product=make_product(), manufacturer_domains=ACME_DOMAINS
            ),
        )
        # The trusted manufacturer domain reaches Groq as a site: query
        # bias; SourcePolicy remains the sole accept/reject authority, and
        # the rejected tools primitive is never used.
        content = captured["body"]["messages"][0]["content"]
        assert "site:acme-controls.example" in content
        assert "tools" not in captured["body"]


# ------------------------------------------------------------ domain site hint --


class TestDomainSiteHint:
    def test_build_body_includes_site_hint_when_domains_present(self):
        body = GroqSearchApiClient(api_key="test-key")._build_body(
            '3M "1700-1PK-BB40"', ["3m.com"]
        )
        content = body["messages"][0]["content"]
        assert content == '3M "1700-1PK-BB40" site:3m.com'
        assert "tools" not in body

    def test_build_body_omits_site_hint_when_no_domains(self):
        body = GroqSearchApiClient(api_key="test-key")._build_body("q", None)
        assert body["messages"][0]["content"] == "q"
        assert "site:" not in body["messages"][0]["content"]

    def test_build_body_site_hint_for_multiple_domains(self):
        body = GroqSearchApiClient(api_key="test-key")._build_body(
            "q", ["a.example", "b.example"]
        )
        assert body["messages"][0]["content"] == "q site:a.example site:b.example"

    def test_discover_sends_trusted_domain_as_site_hint(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.read().decode())
            return httpx.Response(200, json=search_payload())

        provider = GroqSearchProvider(mock_client(handler))
        provider.discover(
            make_product(),
            DiscoveryContext(
                product=make_product(), manufacturer_domains=ACME_DOMAINS
            ),
        )
        content = captured["body"]["messages"][0]["content"]
        assert "site:acme-controls.example" in content


class TestDiscoveryRecallBias:
    @pytest.mark.parametrize(
        ("mpn", "manufacturer", "domain"),
        [
            ("XLC10ZW", "Makita Usa Inc", "makitatools.com"),
            ("49-94-0013", "Milwaukee Tool", "milwaukeetool.com"),
            ("1700-1PK-BB40", "3M", "3m.com"),
            ("WDTS7024RZ", "Whirlpool Corporation", "whirlpool.com"),
        ],
    )
    def test_trusted_domain_reaches_provider_query(
        self, mpn: str, manufacturer: str, domain: str
    ):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.read().decode())
            return httpx.Response(200, json=search_payload())

        product = ProductIdentity(manufacturer=manufacturer, mpn=mpn)
        provider = GroqSearchProvider(mock_client(handler))
        provider.discover(
            product,
            DiscoveryContext(product=product, manufacturer_domains=[domain]),
        )
        content = captured["body"]["messages"][0]["content"]
        assert f"site:{domain}" in content
        assert product.mpn in content

    def test_run_discovery_allows_official_domain_result_with_site_hint(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.read().decode())
            return httpx.Response(
                200,
                json=search_payload(
                    {
                        "title": "M1 Controller",
                        "url": "https://acme-controls.example/products/m1",
                        "content": "datasheet text",
                    },
                    {
                        "title": "M1 on Amazon",
                        "url": "https://www.amazon.com/dp/M1",
                        "content": "listing",
                    },
                ),
            )

        provider = GroqSearchProvider(mock_client(handler))
        result = run_discovery(
            make_product(),
            providers=[provider],
            context=DiscoveryContext(
                product=make_product(), manufacturer_domains=ACME_DOMAINS
            ),
        )
        # The site: bias was transmitted AND SourcePolicy still governs:
        # the official domain is ALLOWED, the marketplace stays PROHIBITED.
        content = captured["body"]["messages"][0]["content"]
        assert "site:acme-controls.example" in content
        assert [c.url for c in result.candidates] == [
            "https://acme-controls.example/products/m1"
        ]
        assert result.candidates[0].status == CandidateStatus.ALLOWED
        assert len(result.rejected) == 1
        assert result.rejected[0].status == CandidateStatus.PROHIBITED


# ------------------------------------------------------------ provider selection --


class TestProviderSelection:
    def test_unset_discovery_provider_uses_registry(self):
        assert providers_from_settings() == list(PROVIDERS)

    def test_groq_not_in_default_registry(self):
        names = {p.name for p in PROVIDERS}
        assert "groq" not in names

    def test_groq_selected_with_key(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_provider", "groq")
        monkeypatch.setattr(settings, "GROQ_API_KEY", "secret")
        providers = providers_from_settings()
        assert len(providers) == 1
        assert providers[0].name == "groq"
        assert isinstance(providers[0], GroqSearchProvider)

    def test_groq_selected_without_key_raises_lazily(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_provider", "groq")
        monkeypatch.setattr(settings, "GROQ_API_KEY", "")
        with pytest.raises(ProviderConfigurationError, match="GROQ_API_KEY"):
            providers_from_settings()

    def test_unknown_provider_name_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_provider", "bogus")
        with pytest.raises(ProviderConfigurationError, match="unknown discovery provider"):
            providers_from_settings()

    def test_run_discovery_uses_groq_provider(self, monkeypatch):
        monkeypatch.setattr(settings, "discovery_provider", "groq")
        monkeypatch.setattr(settings, "GROQ_API_KEY", "secret")

        def fake_from_settings(s=None):
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    json=search_payload(
                        {
                            "title": "M1 Controller",
                            "url": "https://acme-controls.example/products/m1",
                        }
                    ),
                )

            return GroqSearchProvider(mock_client(handler))

        monkeypatch.setattr(
            GroqSearchProvider, "from_settings", staticmethod(fake_from_settings)
        )
        product = make_product()
        result = run_discovery(
            product,
            context=DiscoveryContext(
                product=product, manufacturer_domains=ACME_DOMAINS
            ),
        )
        # providers=None -> DISCOVERY_PROVIDER=groq -> the groq provider.
        assert result.total_discovered == 1
        assert result.candidates[0].domain == "acme-controls.example"
        assert result.candidates[0].status == CandidateStatus.ALLOWED


# ------------------------------------------------- LLM_PROVIDER untouched --


class TestLlmProviderUntouched:
    def test_llm_provider_remains_openrouter(self, monkeypatch):
        original = settings.llm_provider
        monkeypatch.setattr(settings, "discovery_provider", "groq")
        monkeypatch.setattr(settings, "GROQ_API_KEY", "secret")
        # Building the groq provider must not mutate LLM settings.
        providers_from_settings()
        assert settings.llm_provider == original
        assert settings.llm_provider == "openrouter"

    def test_groq_provider_module_ignores_llm_settings(self):
        # The groq module imports from search/errors/ranking, never from llm/.
        import app.sources.providers.groq_search as groq_mod

        assert "openrouter" not in json.dumps(
            {k: repr(v) for k, v in vars(groq_mod).items()}, default=str
        )
