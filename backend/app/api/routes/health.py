from fastapi import APIRouter

from app.config import settings
from app.core.schemas import HealthResponse

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok", app=settings.app_name, version=settings.version
    )
