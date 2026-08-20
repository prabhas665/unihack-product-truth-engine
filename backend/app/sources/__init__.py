"""Source discovery package.

Public surface: SourceCandidate and its enums, the SourcePolicy, the
deterministic ranking, and the discovery orchestration (providers,
DiscoveryContext, run_discovery). Evidence retrieval is intentionally NOT
part of this package - see app/sources/retrieval/.
"""

from app.sources.candidates import (
    CandidateStatus,
    DiscoveryMethod,
    ManufacturerRelationship,
    SourceCandidate,
    normalize_domain,
)
from app.sources.discovery import (
    PROVIDERS,
    DiscoveryContext,
    DiscoveryResult,
    ProviderErrorInfo,
    SourceProvider,
    register_provider,
    run_discovery,
)
from app.sources.errors import (
    ProviderConfigurationError,
    ProviderError,
    ProviderInvalidResponseError,
    ProviderUnavailableError,
)
from app.sources.policy import (
    PERMITTED_SOURCE_TYPES,
    SourcePolicy,
    SourcePolicyConfig,
    policy_from_settings,
)
from app.sources.providers import (
    SearchApiClient,
    SearchProvider,
    SearchResult,
    build_recall_query,
    build_search_query,
    providers_from_settings,
)
from app.sources.ranking import RANKING_WEIGHTS, rank_candidates, score_candidate

__all__ = [
    "CandidateStatus",
    "DiscoveryContext",
    "DiscoveryMethod",
    "DiscoveryResult",
    "ManufacturerRelationship",
    "PERMITTED_SOURCE_TYPES",
    "PROVIDERS",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderErrorInfo",
    "ProviderInvalidResponseError",
    "ProviderUnavailableError",
    "RANKING_WEIGHTS",
    "SearchApiClient",
    "SearchProvider",
    "SearchResult",
    "SourceCandidate",
    "SourcePolicy",
    "SourcePolicyConfig",
    "SourceProvider",
    "build_recall_query",
    "build_search_query",
    "normalize_domain",
    "policy_from_settings",
    "providers_from_settings",
    "rank_candidates",
    "register_provider",
    "run_discovery",
    "score_candidate",
]
