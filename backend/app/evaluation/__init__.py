"""Evaluation package (Step 14B, D3)."""

from app.evaluation.benchmark import (
    clean_dirty_brand,
    is_leak,
    load_expected,
    load_input_rows,
    normalize_expected_cell,
)
from app.evaluation.runner import (
    CellComparison,
    EvaluationReport,
    RowResult,
    run_evaluation,
)

__all__ = [
    "EvaluationReport",
    "RowResult",
    "CellComparison",
    "run_evaluation",
    "load_input_rows",
    "load_expected",
    "normalize_expected_cell",
    "clean_dirty_brand",
    "is_leak",
]
