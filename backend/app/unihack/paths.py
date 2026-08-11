"""Repo-root path helpers for the official UniHack CSV files (Step 6A).

The two official files live at the repository root and must not be moved:
path resolution walks up from this module to the repo root so the code keeps
working regardless of where the repository is checked out.
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def repo_root() -> Path:
    """Absolute path of the repository root (contains the UniHack CSVs)."""
    return _REPO_ROOT


def unihack_input_path() -> Path:
    """Path of the official input dataset (1000 rows x 6 columns)."""
    return repo_root() / "Unihack_ Sample Dataset - Input.csv"


def delivery_reference_path() -> Path:
    """Path of the official 252-column delivery-format reference file."""
    return repo_root() / "Unihack_ Expected Output - Delivery Format.csv"
