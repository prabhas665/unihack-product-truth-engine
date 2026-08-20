"""Real search discovery provider (Step 6B).

Implements the existing SourceProvider protocol with one provider-specific
implementation kept isolated here: a Serper-style JSON search API client.

Rules this provider obeys:
- It only produces SourceCandidate objects. It never decides whether a source
  is acceptable: every candidate is emitted with status PENDING and the
  existing SourcePolicy decides (marketplaces -> PROHIBITED, unknown external
  domains -> REJECTED, manufacturer-owned/allowlisted + permitted type ->
  ALLOWED). It never pre-filters results for acceptability - only URLs that
  can never be retrieved (non-http/https schemes) are skipped.
- No UniHack-specific manufacturer domains are hard-coded.
- Search queries prefer the exact manufacturer part number (quoted).
- Failures are typed (ProviderError subclasses) so run_discovery() can record
  them on the DiscoveryResult instead of fabricating results or aborting.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

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

if TYPE_CHECKING:
    from app.sources.discovery import DiscoveryContext

DEFAULT_BASE_URL = "https://google.serper.dev"

# Words that carry no search value when pulled from the raw description.
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "from", "this", "that", "your", "our",
        "are", "was", "were", "has", "have", "had", "not", "but", "all",
        "any", "can", "its", "their", "also", "will", "into", "over",
    }
)

_QUERY_MAX_LENGTH = 200


@dataclass(frozen=True)
class SearchResult:
    """One raw search result, normalized away from the API shape."""

    url: str
    title: str = ""
    snippet: str = ""


class SearchApiClient:
    """Minimal Serper-style JSON search API client (https://serper.dev).

    ``http_client`` is injectable (httpx.Client with a MockTransport) so all
    tests stay fully offline. The API key lives only on the backend.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 15.0,
        results_limit: int = 10,
        http_client: httpx.Client | None = None,
    ) -> None:
        key = (api_key or "").strip()
        if not key:
            raise ProviderConfigurationError(
                "search", "SEARCH_PROVIDER_API_KEY is not set"
            )
        self._api_key = key
        self._base_url = (base_url or "").rstrip("/") or DEFAULT_BASE_URL
        self._timeout_seconds = timeout_seconds
        self._results_limit = results_limit
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = http_client is None

    def search(self, query: str) -> list[SearchResult]:
        """Run one query; returns normalized results or raises ProviderError."""
        try:
            response = self._client.post(
                f"{self._base_url}/search",
                json={"q": query, "num": self._results_limit},
                headers={
                    "X-API-KEY": self._api_key,
                    "Content-Type": "application/json",
                },
            )
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError(
                "search",
                f"search provider timed out after {self._timeout_seconds}s",
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(
                "search", f"search provider unreachable: {exc}"
            ) from exc

        if response.status_code != 200:
            raise ProviderUnavailableError(
                "search",
                f"search provider returned HTTP {response.status_code}",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderInvalidResponseError(
                "search", "search provider returned a non-JSON response"
            ) from exc

        if not isinstance(payload, dict) or "organic" not in payload:
            raise ProviderInvalidResponseError(
                "search",
                "search provider response is missing the 'organic' results list",
            )
        organic = payload["organic"]
        if not isinstance(organic, list):
            raise ProviderInvalidResponseError(
                "search", "search provider 'organic' field is not a list"
            )

        results: list[SearchResult] = []
        for item in organic:
            if not isinstance(item, dict):
                continue
            link = item.get("link")
            if not isinstance(link, str) or not link.strip():
                continue  # item-level junk is skipped, the batch is not lost
            results.append(
                SearchResult(
                    url=link.strip(),
                    title=str(item.get("title") or "").strip(),
                    snippet=str(item.get("snippet") or "").strip(),
                )
            )
        return results

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def build_search_query(product: ProductIdentity) -> str:
    """Deterministic search query preferring the exact manufacturer part number.

    Manufacturer (or brand as fallback) comes first, then the MPN quoted for
    exact matching; only when no MPN exists are useful raw-description tokens
    appended (they are never trusted - the policy gates every result anyway).
    """
    manufacturer = (product.manufacturer or "").strip()
    brand = (product.brand or "").strip()
    mpn = (product.mpn or "").strip()

    parts: list[str] = []
    if manufacturer:
        parts.append(manufacturer)
    elif brand:
        parts.append(brand)
    if mpn:
        parts.append(f'"{mpn}"')
    else:
        parts.extend(_description_tokens(product))
    if brand and manufacturer and brand.lower() != manufacturer.lower():
        parts.append(brand)

    query = " ".join(parts).strip()
    if len(query) > _QUERY_MAX_LENGTH:
        query = query[: _QUERY_MAX_LENGTH - 1].rstrip() + "…"
    return query


def build_recall_query(product: ProductIdentity) -> str:
    """Wider recall query for discovery Pass 2 (zero ALLOWED in Pass 1).

    A genuinely different query from the Pass-1 form: unquoted MPN first,
    then "specifications" and the manufacturer. Never trusts more than the
    identity - the policy still gates every result.
    """
    mpn = (product.mpn or "").strip()
    manufacturer = (product.manufacturer or "").strip()

    parts: list[str] = []
    if mpn:
        parts.append(mpn)
    parts.append("specifications")
    if manufacturer:
        parts.append(manufacturer)

    query = " ".join(parts).strip()
    if len(query) > _QUERY_MAX_LENGTH:
        query = query[: _QUERY_MAX_LENGTH - 1].rstrip() + "…"
    return query


def _description_tokens(product: ProductIdentity, max_tokens: int = 4) -> list[str]:
    skip: set[str] = set()
    for field in (product.manufacturer, product.brand, product.mpn):
        for token in re.findall(r"[a-zA-Z0-9]+", (field or "").lower()):
            if token:
                skip.add(token)
    tokens: list[str] = []
    for word in re.findall(
        r"[a-zA-Z0-9][a-zA-Z0-9'\-]*", (product.raw_description or "")
    ):
        lowered = word.lower()
        if lowered in skip or lowered in _STOPWORDS or len(word) < 3:
            continue
        tokens.append(word)
        if len(tokens) >= max_tokens:
            break
    return tokens


def guess_source_type(url: str, title: str) -> SourceType:
    """Conservative, deterministic source-type guess from the result itself.

    Only the clearest signals are used (PDF extension -> technical PDF/manual;
    everything else -> product page). The policy still verifies the type is in
    its permitted set, and retrieval re-checks the URL scheme later.
    """
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        path = ""
    if path.endswith(".pdf"):
        if "manual" in path or "manual" in (title or "").lower():
            return SourceType.MANUFACTURER_MANUAL
        return SourceType.MANUFACTURER_TECHNICAL_PDF
    return SourceType.MANUFACTURER_PRODUCT_PAGE


def _candidate_id(url: str) -> str:
    """Stable candidate id derived from the real URL (never fabricated)."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"search-{digest}"


def _is_retrievable_url(url: str) -> bool:
    """True only for http/https URLs; anything else can never be fetched.

    This is a transport-safety filter, not an acceptability decision: every
    candidate this provider emits still goes through SourcePolicy untouched.
    """
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return False
    return scheme in ("http", "https")


class SearchProvider:
    """SourceProvider implementation backed by a real search API.

    Discovers candidate sources only - it never fetches or extracts content
    (discovery and retrieval stay separate). Safe on every failure mode:
    missing config, no results, timeout, malformed responses and outages all
    surface as typed ProviderError subclasses or empty results, never as
    fabricated candidates.
    """

    name = "search"
    kind = DiscoveryMethod.SEARCH

    def __init__(self, api_client: SearchApiClient):
        self._api_client = api_client

    @classmethod
    def from_settings(cls, settings: object | None = None) -> "SearchProvider":
        """Build the provider from backend environment settings.

        Raises ProviderConfigurationError when SEARCH_PROVIDER_API_KEY is
        missing; the application still starts because providers are only
        built when discovery actually runs.
        """
        if settings is None:
            from app.config import settings as app_settings

            settings = app_settings
        api_client = SearchApiClient(
            api_key=getattr(settings, "search_provider_api_key", ""),
            base_url=getattr(
                settings, "search_provider_base_url", DEFAULT_BASE_URL
            ),
            timeout_seconds=getattr(
                settings, "search_provider_timeout_seconds", 15.0
            ),
            results_limit=getattr(settings, "search_provider_results_limit", 10),
        )
        return cls(api_client)

    def discover(
        self, product: ProductIdentity, context: "DiscoveryContext"
    ) -> list[SourceCandidate]:
        # Pass 1 (query_biased=True) uses the exact MPN query; discovery
        # Pass 2 (query_biased=False) uses the wider recall variant instead
        # of re-sending an identical query.
        recall = not bool(getattr(context, "query_biased", True))
        query = build_recall_query(product) if recall else build_search_query(product)
        if not query:
            return []  # nothing to search for; not an error
        results = self._api_client.search(query)

        candidates: list[SourceCandidate] = []
        for result in results:
            url = result.url
            if not _is_retrievable_url(url):
                continue
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

    def close(self) -> None:
        self._api_client.close()
