"""Gemini Search Discovery provider (Step 11).

Implements search discovery using Google Gemini's grounding feature,
which returns web search results grounded in verified sources.

Rules:
- Never fabricate URLs
- Only parse actual groundingMetadata.groundingChunks[*].web.{uri,title}
- SourcePolicy remains the sole authority for ALLOWED/REJECTED
- Every discovered candidate must initially be: PENDING / UNVERIFIED
"""

from __future__ import annotations

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

DEFAULT_MODEL = "gemini-flash-latest"
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
DEFAULT_TIMEOUT_SECONDS = 20.0


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
        from app.sources.providers.search import _description_tokens

        parts.extend(_description_tokens(product, max_tokens=4))
    if brand and manufacturer and brand.lower() != manufacturer.lower():
        parts.append(brand)

    query = " ".join(parts).strip()
    if len(query) > 200:
        query = query[:199].rstrip() + "…"
    return query


def _description_tokens(product: ProductIdentity, max_tokens: int = 4) -> list[str]:
    """Extract up to max_tokens keywords from product description, skipping
    identity words and stopwords, for use in search queries when no MPN exists."""
    import re

    skip: set[str] = set()
    for field in (product.manufacturer, product.brand, product.mpn):
        for token in re.findall(r"[a-zA-Z0-9]+", (field or "").lower()):
            if token:
                skip.add(token.lower())
    tokens: list[str] = []
    for word in re.findall(
        r"[a-zA-Z0-9][a-zA-Z0-9'-]*", (product.raw_description or "")
    ):
        lowered = word.lower()
        if lowered in skip or len(word) < 3:
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
        from urllib.parse import urlsplit
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
    digest = sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"gemini-{digest}"


def _is_retrievable_url(url: str) -> bool:
    """True only for http/https URLs; anything else can never be fetched.

    This is a transport-safety filter, not an acceptability decision: every
    candidate this provider emits still goes through SourcePolicy untouched.
    """
    try:
        from urllib.parse import urlsplit
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return False
    return scheme in ("http", "https")


class GeminiSearchApiClient:
    """Minimal Gemini generateContent client with Google Search grounding.

    Uses the Google AI Studio / Vertex AI generateContent endpoint with the
    google_search_retrieval tool to return web results grounded in sources.
    The API key lives only on the backend; never in client reprs or logs.
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        api_keys: list[str] | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        keys = [k.strip() for k in (api_keys or []) if k and k.strip()]
        if api_key and not keys:
            keys = [api_key.strip()]
        keys = [k for k in keys if k]
        if not keys:
            raise ProviderConfigurationError(
                "gemini", "GEMINI_API_KEY is not set"
            )
        self._api_keys = keys
        self._model = (model or "").strip() or DEFAULT_MODEL
        self._base_url = (base_url or "").rstrip("/") or DEFAULT_BASE_URL
        self._timeout_seconds = timeout_seconds
        self._client = httpx.Client(timeout=timeout_seconds)
        self._owns_client = True

    def grounding_request(self, query: str) -> dict:
        """Send generateContent with google_search_retrieval tool.

        Returns the parsed JSON response dict.
        """
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": query}],
                }
            ],
            "tools": [
                {"google_search_retrieval": {}}
            ],
        }

        url = f"{self._base_url}/v1beta/models/{self._model}:generateContent?key={self._api_keys[0]}"

        def _call(key: str) -> httpx.Response:
            return self._client.post(
                url.replace(self._api_keys[0], key, 1),
                json=payload,
                timeout=self._timeout_seconds,
            )

        try:
            response = _call(self._api_keys[0])
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError(
                "gemini", f"Gemini provider timed out after {self._timeout_seconds}s"
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(
                "gemini", f"Gemini provider unreachable: {exc}"
            ) from exc

        # Rotate around the configured API keys on rate limits (each key has
        # its own free-tier quota).
        if response.status_code == 429 and len(self._api_keys) > 1:
            for alt_key in self._api_keys[1:]:
                try:
                    alt = _call(alt_key)
                except httpx.TimeoutException as exc:
                    raise ProviderUnavailableError(
                        "gemini", f"Gemini provider timed out after {self._timeout_seconds}s"
                    ) from exc
                except httpx.TransportError as exc:
                    raise ProviderUnavailableError(
                        "gemini", f"Gemini provider unreachable: {exc}"
                    ) from exc
                if alt.status_code == 429:
                    continue
                if alt.status_code == 401:
                    continue
                if alt.status_code >= 500:
                    continue
                if alt.status_code != 200:
                    continue
                response = alt
                break
            else:
                raise ProviderUnavailableError(
                    "gemini", "Gemini provider rate limit hit on all configured "
                    "API keys; retry later"
                )

        if response.status_code == 401:
            raise ProviderConfigurationError(
                "gemini", "Gemini API key invalid or expired"
            )
        if response.status_code == 429:
            raise ProviderUnavailableError(
                "gemini", "Gemini provider rate limit hit; retry later"
            )
        if response.status_code >= 500:
            raise ProviderUnavailableError(
                "gemini", f"Gemini provider returned HTTP {response.status_code}"
            )

        if response.status_code != 200:
            raise ProviderInvalidResponseError(
                "gemini", f"Gemini provider returned HTTP {response.status_code}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise ProviderInvalidResponseError(
                "gemini", "Gemini provider returned a non-JSON response"
            ) from exc


def _parse_grounding_chunks(response: dict) -> list[dict]:
    """Extract web result chunks from Gemini groundingMetadata.

    Returns list of dicts with uri and title, or empty list if none found.
    """
    chunks: list[dict] = []
    try:
        gc = response.get("candidates", [{}])[0].get("groundingMetadata", {})
        for chunk in gc.get("groundingChunks", []):
            web = chunk.get("web", {})
            uri = web.get("uri")
            title = web.get("title")
            if uri and title:
                chunks.append({"uri": uri, "title": title})
    except (KeyError, IndexError, TypeError, AttributeError):
        pass
    return chunks


def _build_candidates(chunks: list[dict], product: ProductIdentity) -> list[SourceCandidate]:
    """Build SourceCandidate list from Gemini grounding chunks."""
    candidates: list[SourceCandidate] = []
    for chunk in chunks:
        uri = chunk["uri"]
        title = chunk["title"]
        if not _is_retrievable_url(uri):
            continue  # skip non-http(s) URLs
        candidates.append(
            SourceCandidate(
                id=_candidate_id(uri),
                url=uri,
                title=title,
                source_type=guess_source_type(uri, title),
                domain=normalize_domain(uri),
                discovery_method=DiscoveryMethod.SEARCH,
                status=CandidateStatus.PENDING,
                trust_level=SourceTrustLevel.UNVERIFIED,
            )
        )
    return candidates


class GeminiSearchProvider:
    """SourceProvider implementation backed by Google Gemini grounding.

    Discovers candidate sources only - it never fetches or extracts content
    (retrieval and extraction stay separate). Safe on every failure mode:
    missing config, no results, timeout, malformed responses and outages all
    surface as typed ProviderError subclasses or empty results, never as
    fabricated candidates.
    """

    name = "gemini"
    kind = DiscoveryMethod.SEARCH

    def __init__(self, api_client: GeminiSearchApiClient):
        self._api_client = api_client

    @classmethod
    def from_settings(cls, settings: object | None = None) -> "GeminiSearchProvider":
        """Build the provider from backend environment settings.

        Raises ProviderConfigurationError when GEMINI_API_KEY is missing;
        the application still starts because providers are only built when
        discovery actually runs.
        """
        if settings is None:
            from app.config import settings as app_settings

            settings = app_settings

        api_keys = getattr(settings, "gemini_api_keys", None)
        api_key = getattr(settings, "GEMINI_API_KEY", "")
        model = getattr(settings, "GEMINI_MODEL", DEFAULT_MODEL)
        base_url = getattr(settings, "GEMINI_BASE_URL", DEFAULT_BASE_URL)
        timeout = getattr(settings, "GEMINI_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)

        if not api_key and not api_keys:
            raise ProviderConfigurationError(
                "gemini", "GEMINI_API_KEY is not set in backend environment"
            )

        api_client = GeminiSearchApiClient(
            api_keys=api_keys,
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout,
        )
        return cls(api_client)

    def discover(
        self, product: ProductIdentity, context: object | None = None
    ) -> list[SourceCandidate]:
        """Return candidate sources for the product via Gemini grounding.

        1. Build search query from product identity (Pass 1 exact; discovery
           Pass 2 uses the wider recall variant)
        2. Call Gemini generateContent with google_search_retrieval
        3. Parse groundingChunks into SourceCandidate s
        4. Rank candidates via existing rank_candidates()
        5. Return PENDING / UNVERIFIED candidates (SourcePolicy decides ALLOWED)
        """
        from app.sources.providers.search import build_recall_query

        recall = not bool(getattr(context, "query_biased", True))
        query = build_recall_query(product) if recall else build_search_query(product)
        if not query:
            return []

        response = self._api_client.grounding_request(query)
        chunks = _parse_grounding_chunks(response)
        candidates = _build_candidates(chunks, product)
        return rank_candidates(candidates, product)

    def close(self) -> None:
        if self._api_client._owns_client:
            self._api_client._client.close()
