"""Data models for the UniHack dataset integration layer (Step 6A).

These are lightweight dataclasses on purpose: the layer moves raw bytes and
CSV cells in and out of the internal domain model; it is not itself a domain
boundary.
"""

from dataclasses import dataclass, field


@dataclass
class UniHackRowError:
    """A row-level problem that did not prevent parsing the file."""

    row_number: int
    message: str


@dataclass
class UniHackInputRow:
    """One parsed row of the official UniHack input CSV.

    ``mfg_part_num`` / ``part_desc`` / ... keep the raw cell bytes exactly as
    they appear in the file (quotes, whitespace, placeholders included); the
    ``*_value`` fields carry the semantic value with placeholders and blank
    cells treated as missing (``None``).
    """

    row_id: int = 0  # 1-based data row number (file line minus the header).
    row_number: int = 0  # 1-based physical line in the file (header = 1).
    mfg_part_num: str = ""
    part_desc: str = ""
    e1_brand: str = ""
    unilog_brand: str = ""
    dib_brand: str = ""
    part_manuf: str = ""

    mfg_part_num_value: str | None = None
    part_desc_value: str | None = None
    e1_brand_value: str | None = None
    unilog_brand_value: str | None = None
    dib_brand_value: str | None = None
    part_manuf_value: str | None = None

    # True when the exact stripped MPN also appears on another row.
    mfg_part_num_duplicate: bool = False
    # The shared MPN value; None for unique MPNs.
    duplicate_group_id: str | None = None
    # Names of fields missing a semantic value (placeholders/blanks).
    missing_fields: list[str] = field(default_factory=list)

    def to_identity(self) -> "ProductIdentity":
        """Adapt this row into the internal ProductIdentity model."""
        from app.core.domain.identity import ProductIdentity

        return ProductIdentity(
            manufacturer=self.part_manuf_value or "",
            brand=self.e1_brand_value or "",
            mpn=self.mfg_part_num_value or "",
            raw_description=self.part_desc_value or "",
        )

    def to_product_intelligence(self) -> "ProductIntelligence":
        """Adapt this row into the internal ProductIntelligence aggregate."""
        from app.core.domain.product import ProductIntelligence

        return ProductIntelligence(identity=self.to_identity())


@dataclass
class UniHackInputResult:
    """Full parse result: rows plus everything the file told us about them."""

    rows: list[UniHackInputRow] = field(default_factory=list)
    row_errors: list[UniHackRowError] = field(default_factory=list)
    # MPN value -> data row ids sharing that exact stripped MPN (>1 entry).
    duplicate_groups: dict[str, list[int]] = field(default_factory=dict)
    # Placeholder token -> number of rows where it stood in for a value.
    placeholder_counts: dict[str, int] = field(default_factory=dict)

    @property
    def total_rows(self) -> int:
        return len(self.rows)


@dataclass
class DeliveryRow:
    """One row of the official 252-column delivery format.

    ``values`` always holds exactly as many cells as the DeliverySchema it was
    produced for (252). ``notes`` records every spot where the mapper had to
    leave a cell blank instead of inventing a value, plus any truncations.
    """

    values: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def column_count(self) -> int:
        return len(self.values)

    def set_value(self, index: int, value: str) -> None:
        self.values[index] = value

    def note(self, message: str) -> None:
        self.notes.append(message)
