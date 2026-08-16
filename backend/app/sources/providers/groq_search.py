"""Groq Web Search discovery provider (Step 12B).

Implements search discovery using Groq's web_search tool on the
OpenAI-compatible chat/completions endpoint. Groq runs an agentic search
(the model decides the queries) and returns the actual retrieved sources
inside ``message.executed_tools[*].search_results[*]``.

Rules:
- Never fabricate URLs.
- Only parse ``executed_tools[*].search_results[*]`` ({title, url, content,
  score}). The synthesized ``message.content`` is NOT evidence and must never
  become a SourceCandidate.
- SourcePolicy remains the sole authority for ALLOWED/REJECTED.
- Every discovered candidate is initially: PENDING / UNVERIFIED.

This provider is discovery ONLY. It never fetches or extracts content
(retrieval and extraction stay separate) and never touches the LLM layer
(OpenRouter is untouched).
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Optional

import httpx

from app.core.domain import ProductIdentity, SourceTrustLevel, SourceType
from app.sources.candidates import (
    CandidateStatus,
    DiscoveryMethod,
    SourceCandidate,
    normalize_domain,
)
from app.sources.errors import (
    ProviderConfigurationError,
    ProviderInvalidResponseError,
    ProviderUnavailableError,
)
from app.sources.policy import SourcePolicy
from app.sources.ranking import rank_candidates
from app.sources.providers.search import build_search_query, guess_source_type

DEFAULT_MODEL = "groq/compound-mini"
DEFAULT_BASE_URL = "https://api.groq.com"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_RESULTS_LIMIT = 10

# Selecting this model-version pins Groq's basic-search pricing mode
# ($5 / 1K requests) rather than the later unified pricing.
GROQ_MODEL_VERSION = "2025-07-23"

CHAT_COMPLETIONS_PATH = "/openai/v1/chat/completions"


def _candidate_id(url: str) -> str:
    """Stable candidate id derived from the real URL (never fabricated)."""
    digest = sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"groq-{digest}"


def _is_retrievable_url(url: str) -> bool:
    """True only for http/https URLs; anything else can never be fetched.

    Transport-safety filter, not an acceptability decision: every candidate
    this provider emits still goes through SourcePolicy untouched.
    """
    try:
        from urllib.parse import urlsplit

        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return False
    return scheme in ("http", "https")


@dataclass(frozen=True)
class GroqSearchResult:
    """One raw Groq web_search result, normalized away from the API shape."""

    url: str
    title: str = ""
    content: str = ""
    score: Optional[float] = None


def _parse_search_results(response: dict) -> list[dict]:
    """Extract web_search results from a Groq chat/completions response.

    Reads ``message.executed_tools`` (top-level) with a defensive fallback to
    ``choices[0].message.executed_tools``. Returns dicts with url/title/content
    /score, or an empty list when none are found. Never reads ``content``.
    """
    if not isinstance(response, dict):
        return []

    executed: object = None
    message = response.get("message")
    if isinstance(message, dict):
        executed = message.get("executed_tools")
    if not isinstance(executed, list) and "choices" in response:
        try:
            executed = response["choices"][0].get("message", {}).get("executed_tools")
        except (KeyError, IndexError, TypeError):
            executed = None
    if not isinstance(executed, list):
        return []

    out: list[dict] = []
    for tool in executed:
        if not isinstance(tool, dict):
            continue
        # Accept any executed tool that carries web-search results, regardless
        # of the exact `type` string Groq uses (observed: "search").
        search_results = tool.get("search_results")
        if isinstance(search_results, dict):
            search_results = search_results.get("results")
        if not isinstance(search_results, list):
            continue
        for sr in search_results:
            if not isinstance(sr, dict):
                continue
            url = sr.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            out.append(
                {
                    "url": url.strip(),
                    "title": str(sr.get("title") or "").strip(),
                    "content": str(sr.get("content") or ""),
                    "score": sr.get("score"),
                }
            )
    return out


class GroqSearchApiClient:
    """Minimal Groq web_search client (OpenAI-compatible endpoint).

    ``http_client`` is injectable (httpx.Client with a MockTransport) so all
    tests stay fully offline. The API key lives only on the backend; it is
    never in reprs, exception text, logs, or serialized results.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        results_limit: int = DEFAULT_RESULTS_LIMIT,
        http_client: httpx.Client | None = None,
    ) -> None:
        key = (api_key or "").strip()
        if not key:
            raise ProviderConfigurationError(
                "groq", "GROQ_API_KEY is not set"
            )
        self._api_key = key
        self._model = (model or "").strip() or DEFAULT_MODEL
        self._base_url = (base_url or "").rstrip("/") or DEFAULT_BASE_URL
        self._timeout_seconds = timeout_seconds
        self._results_limit = results_limit
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = http_client is None

    def _build_body(self, query: str, include_domains: list[str] | None) -> dict:
        # Groq compound models (e.g. groq/compound-mini) perform web search
        # natively (agentically) on the OpenAI-compatible chat/completions path;
        # the web_search tool primitive is NOT accepted here (the API rejects
        # tools[].type values other than "function"/"mcp", and rejects any
        # tool_choice object). We send a plain chat request and parse the model's
        # executed_tools search_results. The trusted manufacturer domains ARE
        # passed through as a site: query bias so the search is steered toward
        # the official pages; SourcePolicy remains the sole authority for
        # ALLOWED/REJECTED (DiscoveryContext.manufacturer_domains) - the bias
        # is never a trust decision.
        content = query
        if include_domains:
            sites = " ".join(f"site:{domain}" for domain in include_domains)
            content = f"{query} {sites}".strip()
        return {
            "model": self._model,
            "messages": [{"role": "user", "content": content}],
            "n": 1,
            "temperature": 0,
        }

    def web_search(
        self, query: str, include_domains: list[str] | None = None
    ) -> list[GroqSearchResult]:
        """Run a Groq web_search; returns parsed results or raises ProviderError."""
        body = self._build_body(query, include_domains)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Groq-Model-Version": GROQ_MODEL_VERSION,
        }
        try:
            response = self._client.post(
                f"{self._base_url}{CHAT_COMPLETIONS_PATH}",
                json=body,
                headers=headers,
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError(
                "groq", f"Groq provider timed out after {self._timeout_seconds}s"
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(
                "groq", f"Groq provider unreachable: {exc}"
            ) from exc

        code = response.status_code
        if code in (401, 403):
            raise ProviderConfigurationError(
                "groq", "Groq API key invalid, expired, or lacks web search access"
            )
        if code == 429:
            raise ProviderUnavailableError(
                "groq", "Groq provider rate limit hit; retry later"
            )
        if code >= 500:
            raise ProviderUnavailableError(
                "groq", f"Groq provider returned HTTP {code}"
            )
        if code != 200:
            raise ProviderInvalidResponseError(
                "groq", f"Groq provider returned HTTP {code}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderInvalidResponseError(
                "groq", "Groq provider returned a non-JSON response"
            ) from exc

        raw = _parse_search_results(payload)
        results: list[GroqSearchResult] = []
        for item in raw[: self._results_limit]:
            results.append(
                GroqSearchResult(
                    url=item["url"],
                    title=item.get("title", ""),
                    content=item.get("content", ""),
                    score=item.get("score"),
                )
            )
        return results

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _build_candidates(results: list[GroqSearchResult]) -> list[SourceCandidate]:
    """Build SourceCandidate list from Groq web_search results."""
    candidates: list[SourceCandidate] = []
    for result in results:
        url = result.url
        if not _is_retrievable_url(url):
            continue  # skip non-http(s) URLs
        title = result.title
        candidates.append(
            SourceCandidate(
                id=_candidate_id(url),
                url=url,
                title=title,
                source_type=guess_source_type(url, title),
                domain=normalize_domain(url),
                discovery_method=DiscoveryMethod.SEARCH,
                status=CandidateStatus.PENDING,
                trust_level=SourceTrustLevel.UNVERIFIED,
            )
        )
    return candidates


class GroqSearchProvider:
    """SourceProvider implementation backed by Groq web search.

    Discovers candidate sources only - it never fetches or extracts content
    (retrieval and extraction stay separate). Safe on every failure mode:
    missing config, no results, timeout, malformed responses and outages all
    surface as typed ProviderError subclasses or empty results, never as
    fabricated candidates. It does NOT silently fall back to another provider.
    """

    name = "groq"
    kind = DiscoveryMethod.SEARCH

    def __init__(self, api_client: GroqSearchApiClient):
        self._api_client = api_client

    @classmethod
    def from_settings(cls, settings: object | None = None) -> "GroqSearchProvider":
        """Build the provider from backend environment settings.

        Raises ProviderConfigurationError when GROQ_API_KEY is missing;
        the application still starts because providers are only built when
        discovery actually runs.
        """
        if settings is None:
            from app.config import settings as app_settings

            settings = app_settings

        api_key = getattr(settings, "GROQ_API_KEY", "")
        model = getattr(settings, "GROQ_MODEL", DEFAULT_MODEL)
        base_url = getattr(settings, "GROQ_BASE_URL", DEFAULT_BASE_URL)
        timeout = getattr(settings, "GROQ_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
        results_limit = getattr(settings, "GROQ_RESULTS_LIMIT", DEFAULT_RESULTS_LIMIT)

        if not api_key:
            raise ProviderConfigurationError(
                "groq", "GROQ_API_KEY is not set in backend environment"
            )

        api_client = GroqSearchApiClient(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout,
            results_limit=results_limit,
        )
        return cls(api_client)

    def discover(
        self, product: ProductIdentity, context: object | None = None
    ) -> list[SourceCandidate]:
        """Return candidate sources for the product via Groq web search.

        1. Build search query from product identity (reused from search.py).
        2. Call Groq web_search with manufacturer domains (if known).
        3. Parse executed_tools search_results into SourceCandidates.
        4. Rank candidates via existing rank_candidates().
        5. Return PENDING / UNVERIFIED candidates (SourcePolicy decides ALLOWED).
        """
        query = build_search_query(product)
        if not query:
            return []

        include_domains: list[str] = []
        if context is not None:
            include_domains = list(
                getattr(context, "manufacturer_domains", []) or []
            )

        results = self._api_client.web_search(query, include_domains=include_domains)
        candidates = _build_candidates(results)
        return rank_candidates(candidates, product)

    def close(self) -> None:
        self._api_client.close()
