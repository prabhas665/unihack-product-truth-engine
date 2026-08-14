"""Freeze the official 252-column delivery schema into a versioned artifact.

This is a ONE-TIME / re-run-at-need developer tool. It reads the official
``Unihack_ Expected Output - Delivery Format.csv`` header row, validates it,
records a SHA-256 of the source file, and writes a committed, immutable
artifact (``app/unihack/delivery_headers.py``) that the PRODUCTION application
loads instead of the official file at runtime.

Running this requires the official reference file to exist (pass --source).
After the artifact is generated and committed, the official CSV may be removed
from the repository root without affecting production.

Usage:
    python -m app.scripts.freeze_delivery_schema [--source PATH] [--out PATH]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path

ARTIFACT_VERSION = "1"
COLUMN_COUNT = 252

HEADER_GUARD = """Frozen, versioned 252-column delivery header artifact (auto-generated).

DO NOT EDIT BY HAND. Regenerate with:

    python -m app.scripts.freeze_delivery_schema --source "<official CSV>"

The production schema (DeliverySchema.frozen) loads DELIVERY_HEADERS
directly; no file I/O, no dependency on the official reference CSV at runtime.
"""

OUT_DEFAULT = Path(__file__).resolve().parent.parent / "unihack" / "delivery_headers.py"


def read_header(path: Path) -> list[str]:
    if not path.is_file():
        raise SystemExit(f"source file not found: {path}")
    text = path.read_text(encoding="utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise SystemExit(f"source file is empty: {path}")
    return rows[0]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=str(Path(__file__).resolve().parents[3] / "Unihack_ Expected Output - Delivery Format.csv"),
        help="official delivery-format reference CSV",
    )
    parser.add_argument(
        "--out",
        default=str(OUT_DEFAULT),
        help="artifact output path",
    )
    args = parser.parse_args(argv)

    source = Path(args.source)
    header = read_header(source)
    if len(header) != COLUMN_COUNT:
        raise SystemExit(
            f"expected exactly {COLUMN_COUNT} columns, got {len(header)}"
        )
    if any(name.strip() == "" for name in header):
        raise SystemExit("blank column header detected")
    dupes = {name for name in header if header.count(name) > 1}
    if dupes:
        raise SystemExit(f"duplicate headers: {sorted(dupes)}")

    digest = sha256_of(source)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = ['"""']
    lines.extend(HEADER_GUARD.strip().splitlines())
    lines.append('"""')
    lines.append("")
    lines.append("ARTIFACT_VERSION = " + repr(ARTIFACT_VERSION))
    lines.append("SOURCE_FILENAME = " + repr(source.name))
    lines.append("SOURCE_SHA256 = " + repr(digest))
    lines.append("COLUMN_COUNT = " + repr(COLUMN_COUNT))
    lines.append("GENERATED_AT = " + repr(generated))
    lines.append("")
    lines.append("DELIVERY_HEADERS: tuple[str, ...] = (")
    for name in header:
        lines.append("    " + repr(name) + ",")
    lines.append(")")
    lines.append("")

    out = Path(args.out)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}")
    print(f"  columns: {len(header)}")
    print(f"  source SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
