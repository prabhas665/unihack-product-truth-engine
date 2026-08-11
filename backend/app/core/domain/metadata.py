"""Processing metadata: how far the pipeline got and what went wrong."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.domain.common import utcnow
from app.core.domain.enums import ProcessingStatus


class ProcessingError(BaseModel):
    stage: str
    message: str
    occurred_at: datetime = Field(default_factory=utcnow)
    retryable: bool = False


class ProcessingMetadata(BaseModel):
    status: ProcessingStatus = ProcessingStatus.PENDING
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    errors: list[ProcessingError] = Field(default_factory=list)
