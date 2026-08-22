"""DuckDuckGo search discovery provider.

Free web search via the duckduckgo-search library. No API key required.
Used as a fallback when primary providers (Groq, Gemini, Serper) are
unavailable or rate-limited.

SourcePolicy remains the sole authority for ALLOWED/REJECTED.
"""

from __future__ import annotations

import logging
from hashlib import sha256

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

from app.core.domain import ProductIdentity, SourceTrustLevel, SourceType
from app.sources.candidates import (
    CandidateStatus,
    DiscoveryMethod,
    SourceCandidate,
    normalize_domain,
)
from app.config import settings
from app.sources.discovery import SourceProvider
from app.sources.errors import ProviderConfigurationError, ProviderUnavailableError
from app.sources.providers.search import build_search_query, guess_source_type
from app.sources.ranking import rank_candidates
from app.utils.retry import retry_call

logger = logging.getLogger(__name__)

_DEFAULT_RESULTS_LIMIT = 10
_DEFAULT_TIMEOUT = 15.0


def _candidate_id(url: str) -> str:
    digest = sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"ddg-{digest}"


def _is_retrievable_url(url: str) -> bool:
    try:
        from urllib.parse import urlsplit
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return False
    return scheme in ("http", "https")


def _build_candidates(raw_results: list[dict]) -> list[SourceCandidate]:
    candidates: list[SourceCandidate] = []
    for item in raw_results:
        url = (item.get("href") or "").strip()
        title = (item.get("title") or "").strip()
        if not url or not _is_retrievable_url(url):
            continue
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


class DuckDuckGoSearchProvider:
    """SourceProvider backed by DuckDuckGo web search.

    Free, no API key required. Best-effort: failures surface as empty
    results or ProviderUnavailableError, never fabricated candidates.
    """

    name = "duckduckgo"
    kind = DiscoveryMethod.SEARCH

    def __init__(
        self,
        *,
        results_limit: int = _DEFAULT_RESULTS_LIMIT,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._results_limit = results_limit
        self._timeout = timeout

    @classmethod
    def from_settings(cls, settings: object | None = None) -> "DuckDuckGoSearchProvider":
        return cls()

    def discover(
        self, product: ProductIdentity, context: object | None = None
    ) -> list[SourceCandidate]:
        query = build_search_query(product)
        if not query:
            return []

        # DuckDuckGo doesn't handle exact-match quotes well; strip them
        # for better recall on industrial part numbers.
        ddg_query = query.replace('"', "")

        def _search():
            with DDGS(timeout=self._timeout) as ddg:
                return ddg.text(ddg_query, max_results=self._results_limit)

        try:
            raw = retry_call(
                _search,
                attempts=settings.discovery_retry_attempts,
                base_delay=settings.retry_base_delay_seconds,
                should_retry=lambda exc: True,  # retry on any transient error
            )
        except Exception as exc:
            msg = str(exc).lower()
            if "no results" in msg or "DDGSException" in type(exc).__name__:
                return []
            logger.warning("DuckDuckGo search failed after retries: %s", exc)
            raise ProviderUnavailableError(
                "duckduckgo", f"DuckDuckGo search failed: {exc}"
            ) from exc

        candidates = _build_candidates(raw)
        return rank_candidates(candidates, product)

    def close(self) -> None:
        pass
