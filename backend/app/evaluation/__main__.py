"""CLI entry point for the evaluation harness (Step 14B, D3).

Usage:
    python -m app.evaluation [--input PATH] [--expected PATH] [--live]
                             [--limit N] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import sys

from app.evaluation.runner import (
    DEFAULT_EXPECTED,
    DEFAULT_INPUT,
    DEFAULT_REPORT_DIR,
    run_evaluation,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="UniHack evaluation harness")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="input CSV")
    parser.add_argument(
        "--expected",
        default=str(DEFAULT_EXPECTED),
        help="expected-output CSV (benchmark; omit to skip scoring)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="use settings providers + real LLM (default: offline)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="evaluate only the first N rows"
    )
    parser.add_argument("--out", default=str(DEFAULT_REPORT_DIR), help="report dir")
    args = parser.parse_args(argv)

    report = run_evaluation(
        args.input,
        args.expected,
        live=args.live,
        limit=args.limit,
        report_dir=args.out,
    )
    print(json.dumps(report.model_dump(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
