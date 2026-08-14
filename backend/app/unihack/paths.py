"""Repo-root path helpers for the UniHack integration layer.

Production routing no longer depends on the official UniHack CSV files (Step
6A). The 252-column delivery header is frozen into a committed artifact
(``app/unihack/delivery_headers.py``) and loaded via ``DeliverySchema.frozen``;
the batch/dashboard/lookup routes no longer read any CSV at runtime.

The two official files remain available only as DEVELOPMENT/EVALUATION
fixtures (``backend/tests/fixtures/``) so the evaluation harness and byte-exact
regression tests can still run offline. Runtime production code must never
import or read these fixtures.
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def repo_root() -> Path:
    """Absolute path of the repository root (contains ``backend/``)."""
    return _REPO_ROOT


def input_fixture_path() -> Path:
    """Dev/evaluation-only copy of the official input CSV (NOT production)."""
    return (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "fixtures"
        / "Unihack_ Sample Dataset - Input.csv"
    )


def delivery_fixture_path() -> Path:
    """Dev/evaluation-only copy of the official 252-column reference CSV."""
    return (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "fixtures"
        / "Unihack_ Expected Output - Delivery Format.csv"
    )
