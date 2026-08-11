"""Fetcher abstraction for evidence retrieval.

A fetcher retrieves an ALREADY-APPROVED candidate (one that passed
SourcePolicy), extracts readable text, and returns a structured
EvidenceRecord. Fetchers must never discover new sources and must never
fetch rejected candidates - the orchestrator enforces both.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.domain import SourceType
from app.sources.candidates import SourceCandidate
from app.sources.retrieval.limits import RetrievalLimits
from app.sources.retrieval.models import EvidenceRecord


@runtime_checkable
class Fetcher(Protocol):
    """One retrieval capability (HTML, PDF, ...)."""

    name: str
    supported_types: frozenset[SourceType]

    def supports(self, candidate: SourceCandidate) -> bool:
        """True when this fetcher can retrieve the candidate's source type."""
        ...

    def fetch(
        self, candidate: SourceCandidate, limits: RetrievalLimits
    ) -> EvidenceRecord:
        """Retrieve and extract; raises RetrievalError on failure.

        Failures must be raised as typed RetrievalError (never swallowed).
        """
        ...
