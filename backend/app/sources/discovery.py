"""Source discovery orchestration.

Flow: ProductIdentity -> discovery providers -> policy filter -> ranking.

Source discovery is kept strictly separate from evidence retrieval
(app.sources.retrieval). Providers registered here produce candidate sources
only - they never fetch or extract content, and this foundation never makes
network calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.core.domain import ProductIdentity
from app.config import settings
from app.sources.candidates import DiscoveryMethod, SourceCandidate
from app.sources.errors import ProviderError, ProviderUnavailableError
from app.sources.policy import SourcePolicy, SourcePolicyConfig, policy_from_settings
from app.sources.ranking import rank_candidates
from app.utils.retry import retry_call


@runtime_checkable
class SourceProvider(Protocol):
    """Interface for future discovery providers.

    Three kinds are planned and map onto DiscoveryMethod:
      - search provider   (DiscoveryMethod.SEARCH)
      - direct URL provider (DiscoveryMethod.DIRECT_URL)
      - document provider (DiscoveryMethod.DOCUMENT)

    Implementations discover candidate sources for a product WITHOUT fetching
    content, then register themselves via register_provider().
    """

    name: str
    kind: DiscoveryMethod

    def discover(
        self, product: ProductIdentity, context: "DiscoveryContext"
    ) -> list[SourceCandidate]:
        """Return candidate sources for the product."""
        ...


PROVIDERS: list[SourceProvider] = []


def register_provider(provider: SourceProvider) -> None:
    """Register a discovery provider (used by run_discovery by default)."""
    PROVIDERS.append(provider)


@dataclass
class DiscoveryContext:
    """Per-discovery inputs.

    manufacturer_domains stays empty until the official UniHack manufacturer
    registry is available; it is intentionally not fabricated. policy_config
    overrides the environment-derived default policy.

    query_biased decouples QUERY BIAS from TRUST AUTHORITY: when True,
    providers may steer search queries toward the trusted domains (site:
    hints); when False they must not. It never affects SourcePolicy trust -
    manufacturer_domains are always available to the policy regardless of
    query_biased.
    """

    product: ProductIdentity
    manufacturer_domains: list[str] = field(default_factory=list)
    policy_config: SourcePolicyConfig | None = None
    query_biased: bool = True


class ProviderErrorInfo(BaseModel):
    """One typed discovery-provider failure, recorded on the result.

    Providers never fabricate results on failure: configuration problems,
    timeouts, outages and malformed responses surface here instead, and
    discovery continues with the remaining providers.
    """

    provider_name: str
    # Mirrors ProviderError.kind: configuration | unavailable | invalid_response.
    error_kind: str
    message: str


class DiscoveryResult(BaseModel):
    """Outcome of a discovery run.

    `candidates` contains only ALLOWED sources, ranked best-first;
    `rejected` keeps every rejected/prohibited candidate (with its
    rejection_reason) for review; `provider_errors` records typed provider
    failures so discovery never silently fabricates or aborts.
    """

    product: ProductIdentity
    candidates: list[SourceCandidate] = Field(default_factory=list)
    rejected: list[SourceCandidate] = Field(default_factory=list)
    total_discovered: int = 0
    provider_errors: list[ProviderErrorInfo] = Field(default_factory=list)


def run_discovery(
    product: ProductIdentity,
    providers: list[SourceProvider] | None = None,
    context: DiscoveryContext | None = None,
) -> DiscoveryResult:
    """Discover -> policy-filter -> rank, with a recall pass (Discovery-2).

    Uses the providers selected by DISCOVERY_PROVIDER (or the registered
    registry when unset) unless `providers` is given explicitly. Every
    candidate a provider emits passes through the SourcePolicy - providers
    never decide acceptability. Typed ProviderError failures are recorded on
    the result and discovery continues. No network calls are made by this
    function itself.

    Pass 1 runs the providers normally (query_biased=True, so trusted-domain
    site: hints may steer the search). When Pass 1 yields zero ALLOWED
    candidates, Pass 2 re-runs the SAME providers with query_biased=False
    (no site: hints) to widen recall; both candidate sets are merged,
    deduplicated by URL, and filtered ONCE by the same SourcePolicy.
    query_biased only changes search steering - the policy still trusts
    exactly manufacturer_domains. A Pass 2 failure never erases Pass 1
    results, and a candidate is never fabricated.
    """
    ctx = context or DiscoveryContext(product=product)
    providers = providers if providers is not None else _default_providers()

    discovered, provider_errors = _run_providers(providers, product, ctx)
    policy = _build_policy(ctx)
    allowed, _ = policy.filter(discovered)
    if not allowed:
        fallback_ctx = replace(ctx, query_biased=False)
        more, more_errors = _run_providers(providers, product, fallback_ctx)
        discovered = _dedupe_candidates([*discovered, *more])
        provider_errors = [*provider_errors, *more_errors]

    allowed, rejected = policy.filter(discovered)
    ranked = rank_candidates(allowed, product)

    return DiscoveryResult(
        product=product,
        candidates=ranked,
        rejected=rejected,
        total_discovered=len(discovered),
        provider_errors=provider_errors,
    )


def _run_providers(
    providers: list[SourceProvider],
    product: ProductIdentity,
    context: DiscoveryContext,
) -> tuple[list[SourceCandidate], list[ProviderErrorInfo]]:
    """Run every provider once; typed failures become ProviderErrorInfo.

    A retryable provider failure (rate limit / transient unavailability) is
    retried on the same provider with exponential backoff before being
    recorded as a ProviderErrorInfo (see DISCOVERY_RETRY_ATTEMPTS).
    """
    discovered: list[SourceCandidate] = []
    provider_errors: list[ProviderErrorInfo] = []
    for provider in providers:
        try:
            result = retry_call(
                lambda: provider.discover(product, context),
                attempts=settings.discovery_retry_attempts,
                base_delay=settings.retry_base_delay_seconds,
                should_retry=lambda exc: isinstance(
                    exc, ProviderUnavailableError
                ),
            )
            discovered.extend(result)
        except ProviderError as exc:
            provider_errors.append(
                ProviderErrorInfo(
                    provider_name=exc.provider_name,
                    error_kind=exc.kind,
                    message=exc.message,
                )
            )
    return discovered, provider_errors


def _dedupe_candidates(
    candidates: list[SourceCandidate],
) -> list[SourceCandidate]:
    """Keep the first candidate per URL; order is preserved."""
    seen: set[str] = set()
    out: list[SourceCandidate] = []
    for candidate in candidates:
        url = (candidate.url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(candidate)
    return out


def _default_providers() -> list[SourceProvider]:
    """Providers for a run with `providers=None` (lazy import: avoids a
    module-level cycle between the discovery orchestration and the provider
    package, which builds providers from settings)."""
    from app.sources.providers import providers_from_settings

    return providers_from_settings()


def _build_policy(context: DiscoveryContext) -> SourcePolicy:
    if context.policy_config is not None:
        config = context.policy_config
    else:
        config = policy_from_settings()
    if context.manufacturer_domains:
        config = config.model_copy(
            update={"manufacturer_domains": context.manufacturer_domains}
        )
    return SourcePolicy(config)
