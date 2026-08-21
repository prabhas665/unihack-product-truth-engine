from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.core.schemas import HealthResponse, LLMHealthResponse
from app.db.database import get_session
from app.db.models import ProductRecordModel
from app.llm import (
    CompletionRequest,
    LLMConfigurationError,
    LLMError,
    get_client,
)
from app.llm.errors import LLMProviderUnavailableError, LLMTimeoutError

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(session: Session = Depends(get_session)) -> HealthResponse:
    try:
        total = session.query(func.count()).select_from(
            ProductRecordModel
        ).scalar() or 0
        return HealthResponse(
            status="ok", app=settings.app_name, version=settings.version,
            database_records=total,
        )
    except Exception:
        return HealthResponse(
            status="degraded", app=settings.app_name, version=settings.version,
        )


@router.get("/health/llm", response_model=LLMHealthResponse)
def llm_health() -> LLMHealthResponse:
    """Read-only LLM connectivity check (no key material ever returned).

    Makes ONE tiny chat/completions call with the configured provider and
    reports the outcome. Useful for ops: if this returns 200, the deployed
    API key is valid and extraction will authenticate.
    """
    provider = settings.llm_provider or "unset"
    key_configured = bool((settings.llm_api_key or "").strip())
    model = settings.llm_model or "(provider default)"
    if provider == "gemini":
        key_configured = bool((settings.GEMINI_API_KEY or "").strip())
        model = settings.GEMINI_MODEL or "(provider default)"
    if provider == "nvidia":
        key_configured = bool((settings.NVIDIA_NIM_API_KEY or "").strip())
        model = settings.NVIDIA_MODEL or "(provider default)"
    result = LLMHealthResponse(
        provider=provider,
        model=model,
        fallback_models=[
            model
            for model in (
                settings.llm_fallback_model,
                settings.llm_fallback_model_2,
            )
            if model
        ],
        key_configured=key_configured,
    )
    if not result.key_configured:
        result.error = (
            "GEMINI_API_KEY is not set"
            if provider == "gemini"
            else "NVIDIA_NIM_API_KEY is not set"
            if provider == "nvidia"
            else "LLM_API_KEY is not set"
        )
        return result
    try:
        client = get_client()
    except LLMConfigurationError as exc:
        result.error = str(exc)
        return result

    from time import perf_counter

    started = perf_counter()
    try:
        client.complete(
            CompletionRequest(
                system_prompt="You are a connectivity check.",
                user_prompt="Reply with the single word ok.",
                timeout_seconds=30,
            )
        )
        result.chat_completions_status = 200
    except LLMTimeoutError:
        result.chat_completions_status = None
        result.error = "provider call timed out"
    except LLMProviderUnavailableError as exc:
        message = str(exc)
        result.error = message
        match = message.split("HTTP ")  # e.g. "... (HTTP 401). ..."
        if len(match) == 2 and match[1][:3].isdigit():
            result.chat_completions_status = int(match[1][:3])
    except LLMError as exc:
        result.error = str(exc)
    finally:
        result.elapsed_ms = round((perf_counter() - started) * 1000)
    return result