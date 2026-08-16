"""Tests for the 252-column delivery format layer (Step 6A).

The schema is built from the FROZEN artifact (``DeliverySchema.frozen``), which
is exactly the official 252-column header. A dev/evaluation fixture copy of the
official reference file is used only where a test explicitly compares against
the source bytes (byte-exact header contract). Production code never reads
either official file.
"""

import csv

import pytest

from app.core.domain import (
    AttributeValue,
    Classification,
    Descriptions,
    Evidence,
    ProductIdentity,
    ProductIntelligence,
)
from app.core.domain.enums import SourceType
from app.unihack import (
    DELIVERY_HEADERS,
    DeliveryCsvWriter,
    DeliveryRow,
    DeliverySchema,
    DeliveryWriteError,
    SchemaError,
    UniHackDeliveryMapper,
    UniHackInputParser,
)
from app.unihack.paths import delivery_fixture_path, input_fixture_path

schema = DeliverySchema.frozen()
mapper = UniHackDeliveryMapper(schema)


# ---------------------------------------------------------------- schema --

def test_schema_has_exactly_252_columns():
    assert schema.count == 252


def test_schema_headers_are_unique_and_non_blank():
    assert len(set(schema.headers)) == 252
    assert all(name.strip() != "" for name in schema.headers)


def test_schema_header_order_matches_reference_file():
    with delivery_fixture_path().open(
        "r", encoding="utf-8-sig", newline=""
    ) as fh:
        reference_header = next(csv.reader(fh))
    assert schema.headers == reference_header


def test_schema_index_of():
    assert schema.index_of("PART_NUMBER") == 6
    assert schema.index_of("Mfg_Part_Num") == 11
    assert schema.index_of("ATTRIBUTE_LABEL 1") == 55
    assert schema.index_of("ATTRIBUTE_UOM 50") == 204
    assert schema.index_of("Actual Image (Yes/No)") == 251


def test_schema_duplicate_header_rejected():
    with pytest.raises(SchemaError, match="duplicate"):
        DeliverySchema(["A"] * 252)


def test_schema_blank_header_rejected():
    headers = [f"H{i}" for i in range(252)]
    headers[10] = "   "
    with pytest.raises(SchemaError, match="blank"):
        DeliverySchema(headers)


def test_schema_entirely_empty_rejected():
    with pytest.raises(SchemaError, match="empty"):
        DeliverySchema([])


@pytest.mark.parametrize("count", [251, 253])
def test_schema_wrong_column_count_rejected(count):
    headers = [f"H{i}" for i in range(count)]
    with pytest.raises(SchemaError, match="expected exactly 252"):
        DeliverySchema(headers)


def test_schema_missing_reference_file(tmp_path):
    with pytest.raises(SchemaError, match="not found"):
        DeliverySchema.from_reference_csv(tmp_path / "missing.csv")


# ---------------------------------------------------------------- mapper --

def test_minimal_product_maps_to_252_blank_cells():
    row = mapper.map(ProductIntelligence())
    assert isinstance(row, DeliveryRow)
    assert row.column_count == 252
    assert all(value == "" for value in row.values)
    assert any("requires enrichment" in note for note in row.notes)


def test_input_passthrough_echoes_raw_values():
    result = UniHackInputParser().parse_path(input_fixture_path())
    input_row = result.rows[0]
    product = ProductIntelligence(identity=input_row.to_identity())
    row = mapper.map(product, input_row=input_row)
    assert row.values[schema.index_of("Mfg_Part_Num")] == input_row.mfg_part_num
    assert row.values[schema.index_of("Part_Desc")] == input_row.part_desc
    assert row.values[schema.index_of("E1_Brand")] == input_row.e1_brand
    assert row.values[schema.index_of("Unilog_Brand")] == input_row.unilog_brand
    assert row.values[schema.index_of("DIB_Brand")] == input_row.dib_brand
    assert row.values[schema.index_of("Part_Manuf")] == input_row.part_manuf


def test_unavailable_identity_columns_blank_with_notes():
    row = mapper.map(ProductIntelligence())
    for name in ("PART_NUMBER", "MANUFACTURER_NAME", "BRAND_NAME"):
        assert row.values[schema.index_of(name)] == ""
        assert any(
            name in note and "requires enrichment" in note for note in row.notes
        )


def test_fillable_identity_columns_when_verified():
    product = ProductIntelligence(
        identity=ProductIdentity(
            mpn="DCB518ASTS06G",
            manufacturer="Freud Inc (2435)",
            brand="Diablo",
            sku="SKU-9",
            verified_manufacturer="Freud",
            verified_brand="Diablo",
            verified_trade_name="",
        )
    )
    row = mapper.map(product)
    assert row.values[schema.index_of("PART_NUMBER")] == "DCB518ASTS06G"
    assert row.values[schema.index_of("SKU - MY_PART_NUMBER")] == "SKU-9"
    # MANUFACTURER_NAME/BRAND_NAME come from the verified identity, not the
    # raw input tokens.
    assert row.values[schema.index_of("MANUFACTURER_NAME")] == "Freud"
    assert row.values[schema.index_of("BRAND_NAME")] == "Diablo"
    assert row.values[schema.index_of("TRADE_NAME")] == ""
    assert not any("PART_NUMBER" in note for note in row.notes)


def test_verified_identity_blank_when_unverified():
    product = ProductIntelligence(
        identity=ProductIdentity(
            mpn="UNKNOWN-MPN",
            manufacturer="-- Unbranded --",
            brand="-- Unbranded --",
        )
    )
    row = mapper.map(product)
    assert row.values[schema.index_of("MANUFACTURER_NAME")] == ""
    assert row.values[schema.index_of("BRAND_NAME")] == ""
    assert any(
        "MANUFACTURER_NAME" in note and "enrichment" in note
        for note in row.notes
    )


def test_attributes_serialize_in_insertion_order_with_units():
    product = ProductIntelligence(
        attributes={
            "Width": AttributeValue(
                name="Width", raw_value="0.5 in", value="0.5", unit="in"
            ),
            "Grit": AttributeValue(
                name="Grit", raw_value="80", value="80", unit=""
            ),
        }
    )
    row = mapper.map(product)
    assert row.values[schema.index_of("ATTRIBUTE_LABEL 1")] == "Width"
    assert row.values[schema.index_of("ATTRIBUTE_VALUE 1")] == "0.5"
    assert row.values[schema.index_of("ATTRIBUTE_UOM 1")] == "in"
    assert row.values[schema.index_of("ATTRIBUTE_LABEL 2")] == "Grit"
    assert row.values[schema.index_of("ATTRIBUTE_VALUE 2")] == "80"
    assert row.values[schema.index_of("ATTRIBUTE_UOM 2")] == ""  # unit only if known


def test_attribute_value_falls_back_to_raw_when_un_normalized():
    product = ProductIntelligence(
        attributes={
            "Color": AttributeValue(name="Color", raw_value="Black", value="")
        }
    )
    row = mapper.map(product)
    assert row.values[schema.index_of("ATTRIBUTE_VALUE 1")] == "Black"


def test_more_than_50_attributes_truncated_with_note():
    product = ProductIntelligence(
        attributes={
            f"Attr {i}": AttributeValue(name=f"Attr {i}", value=f"v{i}")
            for i in range(55)
        }
    )
    row = mapper.map(product)
    assert row.values[schema.index_of("ATTRIBUTE_LABEL 50")] == "Attr 49"
    assert row.values[schema.index_of("ATTRIBUTE_VALUE 50")] == "v49"
    assert any("55 attributes" in note and "truncated" in note for note in row.notes)


def test_descriptions_map_to_delivery_columns():
    product = ProductIntelligence(
        descriptions=Descriptions(
            product_title="Diablo Belt",
            short_description="short",
            mobile_description="mobile",
            invoice_description="invoice",
            long_description="long",
            retail_description="retail",
            marketing_description="marketing",
            with_="bag",
            application="sanding",
            includes="6 belts",
            product_name="Sanding Belt",
            item_features=["f1", "f2"],
        )
    )
    row = mapper.map(product)
    for name, value in {
        "MOBILE_DESC": "mobile",
        "INVOICE_DESC": "invoice",
        "SHORT_DESC": "short",
        "LONG_DESC1": "long",
        "RETAIL_DESC": "retail",
        "MARKETING_DESCRIPTION": "marketing",
        "With": "bag",
        "Application": "sanding",
        "Includes": "6 belts",
        "Product Name": "Sanding Belt",
        "ITEM_FEATURES_1": "f1",
        "ITEM_FEATURES_2": "f2",
        "ITEM_FEATURES_3": "",
    }.items():
        assert row.values[schema.index_of(name)] == value, name


def test_item_features_beyond_20_truncated_with_note():
    product = ProductIntelligence(
        descriptions=Descriptions(item_features=[f"f{i}" for i in range(22)])
    )
    row = mapper.map(product)
    assert row.values[schema.index_of("ITEM_FEATURES_20")] == "f19"
    assert any("22" in note and "ITEM_FEATURES" in note for note in row.notes)


def test_classification_maps_to_taxonomy_columns():
    product = ProductIntelligence(
        classification=Classification(
            department="D1", class_="C1", fine="F1", classpath="D1>C1>F1"
        )
    )
    row = mapper.map(product)
    assert row.values[schema.index_of("Dept")] == "D1"
    assert row.values[schema.index_of("Class")] == "C1"
    assert row.values[schema.index_of("Fine")] == "F1"
    assert row.values[schema.index_of("Classpath")] == "D1>C1>F1"


def test_mfr_url_prefers_product_page_then_manual_fills_ref_urls():
    product = ProductIntelligence(
        evidence={
            "e1": Evidence(
                id="e1",
                source_url="https://frigidaire.com/product.pdf",
                source_type=SourceType.MANUFACTURER_MANUAL,
            ),
            "e2": Evidence(
                id="e2",
                source_url="https://frigidaire.com/PD/4816.html",
                source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
            ),
        }
    )
    row = mapper.map(product)
    assert row.values[schema.index_of("MFR URL")] == (
        "https://frigidaire.com/PD/4816.html"
    )
    assert row.values[schema.index_of("Ref URL 1")] == (
        "https://frigidaire.com/product.pdf"
    )
    assert row.values[schema.index_of("Ref URL 2")] == ""


def test_without_product_page_first_evidence_becomes_mfr_url():
    product = ProductIntelligence(
        evidence={
            "a": Evidence(
                id="a",
                source_url="https://mfr.com/manual.pdf",
                source_type=SourceType.MANUFACTURER_MANUAL,
            ),
            "b": Evidence(
                id="b",
                source_url="https://mfr.com/cat.pdf",
                source_type=SourceType.MANUFACTURER_CATALOGUE,
            ),
        }
    )
    row = mapper.map(product)
    assert row.values[schema.index_of("MFR URL")] == "https://mfr.com/manual.pdf"
    assert row.values[schema.index_of("Ref URL 1")] == "https://mfr.com/cat.pdf"


def test_assets_map_into_asset_columns():
    product = ProductIntelligence(
        evidence={
            "e1": Evidence(
                id="e1",
                source_url="https://mfr.com/p",
                source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
                assets={
                    "Product Image": "https://mfr.com/p.jpg",
                    "Specification Sheet": "https://mfr.com/spec.pdf",
                    "Video Link": "https://mfr.com/v.mp4",
                },
            )
        }
    )
    row = mapper.map(product)
    assert row.values[schema.index_of("Product Image")] == "https://mfr.com/p.jpg"
    assert row.values[schema.index_of("Specification Sheet")] == (
        "https://mfr.com/spec.pdf"
    )
    assert row.values[schema.index_of("Video Link")] == "https://mfr.com/v.mp4"


def test_unknown_asset_column_noted_and_skipped():
    product = ProductIntelligence(
        evidence={
            "e1": Evidence(
                id="e1",
                source_url="https://mfr.com/p",
                source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
                assets={"Not A Real Column": "https://mfr.com/x.png"},
            )
        }
    )
    row = mapper.map(product)
    assert any("Not A Real Column" in note for note in row.notes)


def test_real_input_rows_map_to_full_width_delivery_rows():
    result = UniHackInputParser().parse_path(input_fixture_path())
    for input_row in result.rows[:3]:
        product = ProductIntelligence(identity=input_row.to_identity())
        row = mapper.map(product, input_row=input_row)
        assert row.column_count == 252
        assert row.values[schema.index_of("Mfg_Part_Num")] == (
            input_row.mfg_part_num
        )


# ---------------------------------------------------------------- writer --

def test_writer_round_trip_preserves_values(tmp_path):
    row = mapper.map(
        ProductIntelligence(
            identity=ProductIdentity(mpn="ABC", brand="Diablo"),
            attributes={
                "Grit": AttributeValue(name="Grit", value="80", unit="grit")
            },
        )
    )
    out = tmp_path / "delivery.csv"
    count = DeliveryCsvWriter(schema).write_path(out, [row])
    assert count == 1
    with out.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        data = next(reader)
    assert header == schema.headers
    assert data == row.values
    assert len(data) == 252


def test_unicode_survives_round_trip(tmp_path):
    product = ProductIntelligence(
        descriptions=Descriptions(
            short_description="FRIGIDAIRE® Gallery™ 24\" Dishwasher"
        )
    )
    row = mapper.map(product)
    out = tmp_path / "unicode.csv"
    DeliveryCsvWriter(schema).write_path(out, [row])
    with out.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        next(reader)  # header
        data = next(reader)
    assert data[schema.index_of("SHORT_DESC")] == row.values[
        schema.index_of("SHORT_DESC")
    ]
    assert "®" in data[schema.index_of("SHORT_DESC")]


def test_written_header_identical_to_reference_header(tmp_path):
    out = tmp_path / "header_only.csv"
    DeliveryCsvWriter(schema).write_path(out, [])
    reference_line = delivery_fixture_path().read_text(
        encoding="utf-8-sig"
    ).splitlines()[0]
    written_line = out.read_text(encoding="utf-8-sig").splitlines()[0]
    assert written_line == reference_line


def test_written_file_has_utf8_bom(tmp_path):
    out = tmp_path / "bom.csv"
    DeliveryCsvWriter(schema).write_path(out, [])
    first_bytes = out.read_bytes()[:3]
    assert first_bytes == b"\xef\xbb\xbf"


def test_writer_rejects_wrong_width_row(tmp_path):
    bad_row = DeliveryRow(values=["x"] * 251)
    with pytest.raises(DeliveryWriteError, match="expected 252"):
        DeliveryCsvWriter(schema).write_path(tmp_path / "bad.csv", [bad_row])


def test_writer_writes_many_rows(tmp_path):
    rows = [
        mapper.map(ProductIntelligence(identity=ProductIdentity(mpn=f"M{i}")))
        for i in range(5)
    ]
    out = tmp_path / "many.csv"
    assert DeliveryCsvWriter(schema).write_path(out, rows) == 5
    with out.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = list(csv.reader(fh))
    assert len(reader) == 6  # header + 5 rows


# ------------------------------------------------------- formula injection --


def test_escape_formula_policy():
    from app.unihack.writer import escape_formula

    # Always escaped: nothing legitimate starts with these.
    assert escape_formula("") == ""
    assert escape_formula("=1+1") == "'=1+1"
    assert escape_formula("+44 1234") == "'+44 1234"
    assert escape_formula("@cmd") == "'@cmd"
    # "-" is escaped only when it starts something that could parse as an
    # expression...
    assert escape_formula("-SUM(A1)") == "'-SUM(A1)"
    # ...never for negative numbers, the official "-" placeholder, the
    # official "-- ... --" placeholder tokens, or hyphenated part numbers.
    assert escape_formula("-") == "-"
    assert escape_formula("-5") == "-5"
    assert escape_formula("-5.5 kg") == "-5.5 kg"
    assert escape_formula("-- Unbranded --") == "-- Unbranded --"
    assert escape_formula("-- No Unilog Brand --") == "-- No Unilog Brand --"
    assert escape_formula("XLC10ZW-2") == "XLC10ZW-2"
    assert escape_formula("18 in") == "18 in"
    # A leading space already forces Excel to treat the cell as text.
    assert escape_formula("  =1+1") == "  =1+1"


def test_writer_escapes_formula_values(tmp_path):
    from app.unihack.writer import escape_formula

    values = [""] * schema.count
    values[0] = "=cmd"
    values[1] = "-SUM(A1)"
    values[2] = "-5.5"
    values[3] = "-"
    row = DeliveryRow(values=values)
    out = tmp_path / "escaped.csv"
    DeliveryCsvWriter(schema).write_path(out, [row])
    with out.open("r", encoding="utf-8-sig", newline="") as fh:
        data = list(csv.reader(fh))[1]
    assert data[0] == "'=cmd"
    assert data[1] == "'-SUM(A1)"
    assert data[2] == "-5.5"
    assert data[3] == "-"
    assert all(escape_formula(v) == data[i] for i, v in enumerate(values))


# ------------------------------------------------------- MPN-relevant URLs --


def _evidence(ev_id: str, url: str, source_type: SourceType, title: str = "") -> Evidence:
    return Evidence(
        id=ev_id,
        source_url=url,
        source_type=source_type,
        source_title=title or url,
    )


def _product(mpn: str, evidence: list[Evidence]) -> ProductIntelligence:
    return ProductIntelligence(
        identity=ProductIdentity(mpn=mpn),
        evidence={e.id: e for e in evidence},
    )


def _ref_urls(row: DeliveryRow, schema: DeliverySchema) -> list[str]:
    return [
        row.values[schema.index_of(f"Ref URL {i}")]
        for i in range(1, 6)
    ]


class TestMfrUrlMpnRelevance:
    """Delivery URL traceability: MFR URL and Ref URLs must identify the
    requested MPN, never sibling manufacturer pages."""

    XLC10ZW = "https://makitatools.com/products/details/XLC10ZW"
    SIBLINGS = [
        "https://makitatools.com/products/details/XLC08ZB",
        "https://makitatools.com/products/details/XLC09ZB",
        "https://makitatools.com/products/details/XLC10R1W",
    ]

    def test_exact_page_present_and_siblings_excluded(self):
        exact = _evidence("e-exact", self.XLC10ZW, SourceType.MANUFACTURER_PRODUCT_PAGE)
        siblings = [
            _evidence(f"e-sib-{i}", url, SourceType.MANUFACTURER_PRODUCT_PAGE)
            for i, url in enumerate(self.SIBLINGS)
        ]
        row = mapper.map(_product("XLC10ZW", [exact] + siblings))

        assert row.values[schema.index_of("MFR URL")] == self.XLC10ZW
        for url in _ref_urls(row, schema):
            assert url == ""

    def test_exact_page_absent_leaves_mfr_blank_and_siblings_out(self):
        siblings = [
            _evidence(f"e-sib-{i}", url, SourceType.MANUFACTURER_PRODUCT_PAGE)
            for i, url in enumerate(self.SIBLINGS)
        ]
        row = mapper.map(_product("XLC10ZW", siblings))

        assert row.values[schema.index_of("MFR URL")] == ""
        for url in _ref_urls(row, schema):
            assert url == ""

    def test_sibling_that_mentions_requested_mpn_is_a_ref_url(self):
        exact = _evidence("e-exact", self.XLC10ZW, SourceType.MANUFACTURER_PRODUCT_PAGE)
        compatible = _evidence(
            "e-compat",
            "https://makitatools.com/products/details/XLC10R1W",
            SourceType.MANUFACTURER_PRODUCT_PAGE,
            title="XLC10R1W - compatible with XLC10ZW",
        )
        row = mapper.map(_product("XLC10ZW", [exact, compatible]))

        assert row.values[schema.index_of("MFR URL")] == self.XLC10ZW
        refs = _ref_urls(row, schema)
        assert refs[0] == compatible.source_url
        assert refs[1:] == [""] * 4

    def test_generic_manufacturer_page_is_not_cited(self):
        generic = _evidence(
            "e-generic",
            "https://makitatools.com/products",
            SourceType.MANUFACTURER_PRODUCT_PAGE,
            title="Makita Tools",
        )
        row = mapper.map(_product("XLC10ZW", [generic]))

        assert row.values[schema.index_of("MFR URL")] == ""
        for url in _ref_urls(row, schema):
            assert url == ""

    def test_prohibited_or_rejected_candidates_never_cited(self):
        # Rejected/prohibited candidates are filtered by SourcePolicy before
        # ProductIntelligence is built, so they can never reach the mapper.
        # Assert a known-rejected URL appears nowhere in the delivery row.
        rejected = "https://marketplace.example/xlc10zw-listing"
        exact = _evidence("e-exact", self.XLC10ZW, SourceType.MANUFACTURER_PRODUCT_PAGE)
        row = mapper.map(_product("XLC10ZW", [exact]))

        assert row.values[schema.index_of("MFR URL")] == self.XLC10ZW
        assert rejected not in _ref_urls(row, schema)

    def test_no_relevant_references_leaves_all_urls_blank(self):
        unrelated = [
            _evidence(f"e-{i}", url, SourceType.MANUFACTURER_PRODUCT_PAGE)
            for i, url in enumerate(self.SIBLINGS)
        ]
        row = mapper.map(_product("XLC99ZZ", unrelated))

        assert row.values[schema.index_of("MFR URL")] == ""
        for url in _ref_urls(row, schema):
            assert url == ""

    def test_fewer_than_five_relevant_leaves_rest_blank(self):
        exact = _evidence("e-exact", self.XLC10ZW, SourceType.MANUFACTURER_PRODUCT_PAGE)
        compatible = _evidence(
            "e-compat",
            "https://makitatools.com/products/details/XLC10R1W",
            SourceType.MANUFACTURER_PRODUCT_PAGE,
            title="XLC10R1W compatible with XLC10ZW",
        )
        row = mapper.map(_product("XLC10ZW", [exact, compatible]))

        refs = _ref_urls(row, schema)
        assert refs[0] == compatible.source_url
        assert refs[1:] == [""] * 4


class TestMfrUrlTokenBoundaries:
    """Delivery URL relevance is decided at MPN token boundaries (P0).

    The audit found the substring relevance check too strict (it would blank
    the WDTS7024RZ manufacturer URL, whose slug truncates the MPN) and too
    loose at the same time (XLC10ZW-2 or XLC10ZWX could match XLC10ZW).
    Relevance now operates on hyphen-aware product tokens:
    - exact token (49-94-0013) is the strongest match;
    - a page token that is a strict PREFIX of the MPN (WDTS7024R for
      WDTS7024RZ) is accepted for the manufacturer URL;
    - a strict EXTENSION (XLC10ZW-2) or a different MPN (XLC10R1W) is a
      sibling and is never cited.
    """

    WHIRLPOOL_PREFIX = (
        "https://learnwhirlpool.com/smartsearchresults?searchtext=WDTS7024R"
    )
    MILWAUKEE_4940013 = "https://www.milwaukeetool.com/products/49-94-0013"

    def test_kit_extension_variant_never_cited_for_base_mpn(self):
        exact = _evidence(
            "e-exact",
            "https://makitatools.com/products/details/XLC10ZW",
            SourceType.MANUFACTURER_PRODUCT_PAGE,
        )
        kit = _evidence(
            "e-kit",
            "https://makitatools.com/products/details/XLC10ZW-2",
            SourceType.MANUFACTURER_PRODUCT_PAGE,
            title="XLC10ZW-2 kit with charger",
        )
        row = mapper.map(_product("XLC10ZW", [exact, kit]))

        assert (
            row.values[schema.index_of("MFR URL")]
            == "https://makitatools.com/products/details/XLC10ZW"
        )
        refs = _ref_urls(row, schema)
        assert kit.source_url not in refs
        assert refs == [""] * 5

    def test_prefix_slug_without_full_mpn_is_mfr_url(self):
        """WDTS7024RZ's official manufacturer URL truncates the MPN in the
        search slug (searchtext=WDTS7024R): it must still be cited."""
        page = _evidence(
            "e-whirlpool",
            self.WHIRLPOOL_PREFIX,
            SourceType.MANUFACTURER_PRODUCT_PAGE,
            title="Whirlpool Smart Search Results",
        )
        row = mapper.map(_product("WDTS7024RZ", [page]))

        assert row.values[schema.index_of("MFR URL")] == self.WHIRLPOOL_PREFIX

    def test_hyphenated_mpn_exact_url_is_mfr_url(self):
        page = _evidence(
            "e-milwaukee",
            self.MILWAUKEE_4940013,
            SourceType.MANUFACTURER_PRODUCT_PAGE,
        )
        row = mapper.map(_product("49-94-0013", [page]))

        assert row.values[schema.index_of("MFR URL")] == self.MILWAUKEE_4940013

    def test_soft_slug_is_mfr_url_and_extended_variant_still_excluded(self):
        exact = _evidence(
            "e-exact",
            self.WHIRLPOOL_PREFIX,
            SourceType.MANUFACTURER_PRODUCT_PAGE,
            title="WDTS7024RZ results",
        )
        extended = _evidence(
            "e-ext",
            "https://learnwhirlpool.com/smartsearchresults?searchtext=WDTS7024RZ1",
            SourceType.MANUFACTURER_PRODUCT_PAGE,
            title="WDTS7024RZ1 deluxe model",
        )
        row = mapper.map(_product("WDTS7024RZ", [exact, extended]))

        assert row.values[schema.index_of("MFR URL")] == self.WHIRLPOOL_PREFIX
        refs = _ref_urls(row, schema)
        assert extended.source_url not in refs
        assert refs == [""] * 5

    def test_lookalike_extension_never_satisfies_request(self):
        """XLC10ZWX is a different token; only the exact page may be cited."""
        lookalike = _evidence(
            "e-lookalike",
            "https://makitatools.com/products/details/XLC10ZWX",
            SourceType.MANUFACTURER_PRODUCT_PAGE,
            title="XLC10ZWX",
        )
        row = mapper.map(_product("XLC10ZW", [lookalike]))

        assert row.values[schema.index_of("MFR URL")] == ""
        assert _ref_urls(row, schema) == [""] * 5
