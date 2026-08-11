"""Human review state for attributes that need attention.

Triggered by uncertain/conflicting/validation-failed values. Reviewer decisions
are stored so a future human-review workflow (frontend page) can act on them.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.core.domain.enums import ReviewDecision


class ReviewState(BaseModel):
    needs_review: bool = False
    reason: str = ""
    decision: Optional[ReviewDecision] = None
    reviewer_notes: str = ""
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
