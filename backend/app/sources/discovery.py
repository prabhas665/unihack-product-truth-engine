"""Source discovery orchestration.

Flow: ProductIdentity -> discovery providers -> policy filter -> ranking.

Source discovery is kept strictly separate from evidence retrieval
(app.sources.retrieval). Providers registered here produce candidate sources
only - they never fetch or extract content, and this foundation never makes
network calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.core.domain import ProductIdentity
from app.sources.candidates import DiscoveryMethod, SourceCandidate
from app.sources.errors import ProviderError
from app.sources.policy import SourcePolicy, SourcePolicyConfig, policy_from_settings
from app.sources.ranking import rank_candidates


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
    """

    product: ProductIdentity
    manufacturer_domains: list[str] = field(default_factory=list)
    policy_config: SourcePolicyConfig | None = None


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
    """Discover -> policy-filter -> rank.

    Uses the providers selected by DISCOVERY_PROVIDER (or the registered
    registry when unset) unless `providers` is given explicitly. Every
    candidate a provider emits passes through the SourcePolicy - providers
    never decide acceptability. Typed ProviderError failures are recorded on
    the result and discovery continues. No network calls are made by this
    function itself.
    """
    ctx = context or DiscoveryContext(product=product)
    providers = providers if providers is not None else _default_providers()

    discovered: list[SourceCandidate] = []
    provider_errors: list[ProviderErrorInfo] = []
    for provider in providers:
        try:
            discovered.extend(provider.discover(product, ctx))
        except ProviderError as exc:
            provider_errors.append(
                ProviderErrorInfo(
                    provider_name=exc.provider_name,
                    error_kind=exc.kind,
                    message=exc.message,
                )
            )

    policy = _build_policy(ctx)
    allowed, rejected = policy.filter(discovered)
    ranked = rank_candidates(allowed, product)

    return DiscoveryResult(
        product=product,
        candidates=ranked,
        rejected=rejected,
        total_discovered=len(discovered),
        provider_errors=provider_errors,
    )


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
