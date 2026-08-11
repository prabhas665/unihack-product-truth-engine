"""Parser for the official UniHack input dataset (Step 6A).

Reads the real input CSV (``Unihack_ Sample Dataset - Input.csv``): exactly 6
columns, UTF-8 with BOM, comma-delimited. Raw cell values are preserved
byte-for-byte; the four official placeholder tokens are detected and exposed
semantically as missing values. Rows are never dropped: malformed or
incomplete rows stay in the result and are reported via ``row_errors``.
Fatal problems (wrong encoding, wrong header, missing file) raise
``UniHackInputError``.
"""

from pathlib import Path

from app.unihack.models import (
    UniHackInputResult,
    UniHackInputRow,
    UniHackRowError,
)

INPUT_COLUMNS: list[str] = [
    "Mfg_Part_Num",
    "Part_Desc",
    "E1_Brand",
    "Unilog_Brand",
    "DIB_Brand",
    "Part_Manuf",
]
EXPECTED_INPUT_COLUMN_COUNT: int = len(INPUT_COLUMNS)

# Official placeholder tokens used in the dataset. Each field has exactly one
# token; a blank cell is also treated as missing.
PLACEHOLDER_E1_BRAND = "-- Unbranded --"
PLACEHOLDER_UNILOG_BRAND = "-- No Unilog Brand --"
PLACEHOLDER_DIB_BRAND = "-- No DIB Brand --"
PLACEHOLDER_PART_MANUF = "-"

# Official placeholder tokens used in the dataset; blank cells also count as
# missing. Fields without a token (MPN, description) use "".
_FIELD_PLACEHOLDERS: dict[str, str] = {
    "mfg_part_num": "",
    "part_desc": "",
    "e1_brand": PLACEHOLDER_E1_BRAND,
    "unilog_brand": PLACEHOLDER_UNILOG_BRAND,
    "dib_brand": PLACEHOLDER_DIB_BRAND,
    "part_manuf": PLACEHOLDER_PART_MANUF,
}

REQUIRED_FIELDS: list[str] = ["Mfg_Part_Num", "Part_Desc"]
_FIELD_DISPLAY_NAMES: dict[str, str] = {
    "mfg_part_num": "Mfg_Part_Num",
    "part_desc": "Part_Desc",
}

_FIELD_TO_RAW_ATTR = {
    "mfg_part_num": "mfg_part_num",
    "part_desc": "part_desc",
    "e1_brand": "e1_brand",
    "unilog_brand": "unilog_brand",
    "dib_brand": "dib_brand",
    "part_manuf": "part_manuf",
}
_FIELD_TO_VALUE_ATTR = {
    "mfg_part_num": "mfg_part_num_value",
    "part_desc": "part_desc_value",
    "e1_brand": "e1_brand_value",
    "unilog_brand": "unilog_brand_value",
    "dib_brand": "dib_brand_value",
    "part_manuf": "part_manuf_value",
}


class UniHackInputError(ValueError):
    """Fatal input error: unreadable file, wrong encoding, wrong header."""


class UniHackInputParser:
    """Parses the official UniHack input CSV into UniHackInputResult."""

    def parse_path(self, path: str | Path) -> UniHackInputResult:
        """Parse the CSV file at ``path`` (UTF-8, optional BOM)."""
        file_path = Path(path)
        if not file_path.is_file():
            raise UniHackInputError(f"input file not found: {file_path}")
        try:
            text = file_path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise UniHackInputError(
                f"input file is not valid UTF-8: {file_path} ({exc})"
            ) from exc
        return self.parse_text(text)

    def parse_text(self, text: str) -> UniHackInputResult:
        """Parse already-decoded CSV content.

        The text must be UTF-8 (BOM allowed, stripped here); the caller is
        responsible for decoding with the right encoding.
        """
        import csv
        import io

        if text.startswith("\ufeff"):
            text = text[1:]
        rows = list(csv.reader(io.StringIO(text)))

        if not rows:
            raise UniHackInputError("input CSV is empty (no header row)")
        header = rows[0]
        if header != INPUT_COLUMNS:
            raise UniHackInputError(
                f"input header mismatch: expected {INPUT_COLUMNS}, got {header}"
            )

        result = UniHackInputResult()
        placeholder_counts = dict.fromkeys(_FIELD_PLACEHOLDERS.values(), 0)
        if "" in placeholder_counts:
            placeholder_counts.pop("")

        for line_index, cells in enumerate(rows[1:], start=2):
            if all(cell.strip() == "" for cell in cells):
                continue  # skip blank lines
            if len(cells) != EXPECTED_INPUT_COLUMN_COUNT:
                result.row_errors.append(
                    UniHackRowError(
                        row_number=line_index,
                        message=(
                            f"expected {EXPECTED_INPUT_COLUMN_COUNT} columns, "
                            f"got {len(cells)}"
                        ),
                    )
                )
                continue

            row = UniHackInputRow(
                row_id=len(result.rows) + 1,
                row_number=line_index,
                mfg_part_num=cells[0],
                part_desc=cells[1],
                e1_brand=cells[2],
                unilog_brand=cells[3],
                dib_brand=cells[4],
                part_manuf=cells[5],
            )
            for field in _FIELD_PLACEHOLDERS:
                raw = getattr(row, _FIELD_TO_RAW_ATTR[field])
                placeholder = _FIELD_PLACEHOLDERS[field]
                if placeholder != "" and placeholder in (raw, raw.strip()):
                    placeholder_counts[placeholder] += 1
                semantic = self._semantic_value(raw, placeholder)
                setattr(row, _FIELD_TO_VALUE_ATTR[field], semantic)
                if semantic is None:
                    row.missing_fields.append(field)

            for required in _FIELD_DISPLAY_NAMES:
                if getattr(row, f"{required}_value") is None:
                    result.row_errors.append(
                        UniHackRowError(
                            row_number=line_index,
                            message=(
                                "missing required field: "
                                f"{_FIELD_DISPLAY_NAMES[required]}"
                            ),
                        )
                    )
            result.rows.append(row)

        result.placeholder_counts = placeholder_counts
        result.duplicate_groups = self._detect_duplicates(result.rows)
        return result

    @staticmethod
    def _semantic_value(raw: str, placeholder: str) -> str | None:
        """Strip; placeholders and blanks count as missing (None)."""
        cleaned = raw.strip()
        if cleaned == "" or cleaned == placeholder:
            return None
        return cleaned

    @staticmethod
    def _detect_duplicates(rows: list[UniHackInputRow]) -> dict[str, list[int]]:
        """Group data row ids by exact stripped MPN (rows never removed)."""
        groups: dict[str, list[int]] = {}
        for row in rows:
            mpn = row.mfg_part_num.strip()
            if mpn == "":
                continue
            groups.setdefault(mpn, []).append(row.row_id)
        duplicates: dict[str, list[int]] = {}
        for mpn, row_ids in groups.items():
            if len(row_ids) > 1:
                duplicates[mpn] = row_ids
                for row in rows:
                    if row.mfg_part_num.strip() == mpn:
                        row.mfg_part_num_duplicate = True
                        row.duplicate_group_id = mpn
        return duplicates
