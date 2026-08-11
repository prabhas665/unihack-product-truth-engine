"""POST /api/enrich: single-product enrichment (Step 6D).

Accepts the six official UniHack input fields as JSON and returns the full,
reviewable EnrichmentResult. The service is built per request from settings
(real discovery providers, real retrieval, real LLM provider when
configured); tests override the service via dependency_overrides to stay
fully offline.
"""

from fastapi import APIRouter, Depends

from app.pipeline.enrichment import (
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentService,
)

router = APIRouter(prefix="/api", tags=["enrich"])


def get_enrichment_service() -> EnrichmentService:
    """Build the pipeline service from current settings."""
    return EnrichmentService()


@router.post("/enrich", response_model=EnrichmentResult)
def enrich(
    request: EnrichmentRequest,
    service: EnrichmentService = Depends(get_enrichment_service),
) -> EnrichmentResult:
    return service.run(request)
