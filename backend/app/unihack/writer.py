"""CSV writer for the 252-column delivery format (Step 6A).

Uses only the standard library: UTF-8 with BOM (matching the official file),
CRLF line endings, and the stdlib ``csv`` module for correct quoting of
commas, quotes and Unicode. The header is written from the DeliverySchema so
it is always in exact official order; every data row must be exactly as wide
as the schema.
"""

from pathlib import Path

from app.unihack.models import DeliveryRow
from app.unihack.schema import DeliverySchema


class DeliveryWriteError(ValueError):
    """A delivery row could not be written (wrong width, bad value)."""


class DeliveryCsvWriter:
    """Streaming writer for delivery rows; returns the number of rows."""

    def __init__(self, schema: DeliverySchema):
        self.schema = schema

    def write_path(self, path: str | Path, rows: list[DeliveryRow]) -> int:
        """Write header + rows to ``path`` (UTF-8 with BOM, CRLF)."""
        import csv

        file_path = Path(path)
        with file_path.open(
            "w", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(self.schema.headers)
            for row in rows:
                if len(row.values) != self.schema.count:
                    raise DeliveryWriteError(
                        f"row has {len(row.values)} columns; expected "
                        f"{self.schema.count}"
                    )
                writer.writerow(row.values)
        return len(rows)

    def write_text(self, rows: list[DeliveryRow]) -> str:
        """Same as write_path but returns the CSV text (utf-8-sig encoded
        bytes are decoded back for convenience; BOM survives)."""
        import csv
        import io

        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(self.schema.headers)
        for row in rows:
            if len(row.values) != self.schema.count:
                raise DeliveryWriteError(
                    f"row has {len(row.values)} columns; expected "
                    f"{self.schema.count}"
                )
            writer.writerow(row.values)
        return buffer.getvalue()
