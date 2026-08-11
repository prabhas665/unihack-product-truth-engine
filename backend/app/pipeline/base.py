"""Pipeline orchestration interface.

The enrichment pipeline is a sequence of stages. Each stage takes the current
record and returns a (possibly) enriched record. Stages are registered here as
they are implemented; the registry is intentionally empty until then.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.domain import ProductIntelligence


@runtime_checkable
class Stage(Protocol):
    name: str

    def run(self, record: ProductIntelligence) -> ProductIntelligence:
        """Apply this stage's transformation to the record."""
        ...


STAGES: list[Stage] = []


def register_stage(stage: Stage) -> None:
    STAGES.append(stage)
