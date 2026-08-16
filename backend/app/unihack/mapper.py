"""Maps the internal ProductIntelligence model to the 252-column delivery
format (Step 6A).

Rules:
- The six original input columns are passed through verbatim (raw values,
  placeholders included) when the originating UniHackInputRow is supplied;
  otherwise they fall back to the internal identity.
- Ambiguous columns (PART_NUMBER, SKU, MANUFACTURER_NAME, BRAND_NAME,
  Dept/Class/Fine/Classpath, ...) are filled ONLY from verified internal
  model values; otherwise they stay blank and a note records why.
- Attribute triples are serialized in insertion order, first 50 slots;
  ATTRIBUTE_VALUE = normalized value if present else raw; ATTRIBUTE_UOM only
  when a unit is known.
- URLs/assets come exclusively from recorded evidence. Nothing is invented.
"""

from __future__ import annotations

import re

from app.core.domain.enums import SourceType
from app.core.domain.evidence import Evidence
from app.core.domain.product import ProductIntelligence
from app.unihack.models import DeliveryRow, UniHackInputRow
from app.unihack.schema import (
    ATTRIBUTE_SLOT_COUNT,
    DeliverySchema,
)

NOTE_ENRICHMENT = "requires enrichment/verification"
NOTE_TAXONOMY = "requires classification from the official UniHack taxonomy"

# A product-like token: one or more A-Z0-9 groups joined by hyphens (so
# hyphenated part numbers like XLC10ZW-2 or 49-94-0013 stay one token),
# at least 4 characters, containing at least one digit (this keeps short
# spec values like "18V" from looking like product identities).
_MPN_TOKEN_RE = re.compile(r"[A-Z0-9]+(?:-[A-Z0-9]+)*")
_DIGIT_RE = re.compile(r"\d")


def _mpn_tokens(value: str) -> set[str]:
    """Uppercased product-like tokens (>=4 chars, contain a digit)."""
    return {
        token
        for token in _MPN_TOKEN_RE.findall((value or "").upper())
        if len(token) >= 4 and _DIGIT_RE.search(token)
    }


def _canonical_mpn(mpn: str | None) -> str:
    """One canonical MPN representation for delivery relevance checks."""
    return (mpn or "").strip().upper()


def _match_kind(
    evidence: "Evidence", requested: str, *, url_only: bool = False
) -> str:
    """How an evidence record's url (and optionally title) relate to the MPN.

    exact   : the full MPN appears as a token (e.g. ``49-94-0013``).
    soft    : a token is a strict PREFIX of the MPN; manufacturers truncate
              MPNs in search slugs (e.g. ``WDTS7024R`` for ``WDTS7024RZ``).
    sibling : a token is a strict EXTENSION of the MPN (``XLC10ZW-2`` for
              ``XLC10ZW``) or a different product token (``XLC10R1W``).
    none    : no product-like token in the inspected fields at all.

    ``url_only`` restricts the check to the URL slug, which is the
    authoritative statement of which product a page describes: a sibling
    page (URL slug ``XLC10R1W``) whose TITLE merely mentions the requested
    MPN must never be selected as the requested product's manufacturer page.
    """
    requested = _canonical_mpn(requested)
    source = (
        evidence.source_url
        if url_only
        else f"{evidence.source_url} {evidence.source_title}"
    )
    tokens = _mpn_tokens(source)
    if not requested or not tokens:
        return "none"
    if requested in tokens:
        return "exact"
    if any(
        requested.startswith(token) and token != requested
        for token in tokens
    ):
        return "soft"
    return "sibling"


def _references_mpn(evidence: "Evidence", mpn: str) -> bool:
    """True when the evidence's URL or title identifies the requested MPN.

    Delivery citations must point at evidence for the REQUESTED product, not
    at sibling manufacturer pages that merely share the manufacturer domain.
    Relevance is token-aware at exact/soft level: ``XLC10ZW-2`` and
    ``XLC10R1W`` never satisfy a request for ``XLC10ZW``, while a page whose
    slug is a strict prefix of the requested MPN (``WDTS7024R`` for
    ``WDTS7024RZ``) is accepted.
    """
    if not mpn:
        return True
    return _match_kind(evidence, mpn) in ("exact", "soft")


class UniHackDeliveryMapper:
    """One-way mapper: ProductIntelligence -> DeliveryRow (252 cells)."""

    def __init__(self, schema: DeliverySchema):
        self.schema = schema

    # -- public API --------------------------------------------------------

    def map(
        self,
        product: ProductIntelligence,
        input_row: UniHackInputRow | None = None,
    ) -> DeliveryRow:
        row = DeliveryRow(values=[""] * self.schema.count)
        self._map_identity(row, product, input_row)
        self._map_evidence(row, product)
        self._map_descriptions(row, product)
        self._map_classification(row, product)
        self._map_attributes(row, product)
        self._map_assets(row, product)
        self._map_ambiguous_blanks(row, product)
        return row

    # -- identity / passthrough --------------------------------------------

    def _map_identity(
        self,
        row: DeliveryRow,
        product: ProductIntelligence,
        input_row: UniHackInputRow | None,
    ) -> None:
        if input_row is not None:
            passthrough = {
                "Mfg_Part_Num": input_row.mfg_part_num,
                "Part_Desc": input_row.part_desc,
                "E1_Brand": input_row.e1_brand,
                "Unilog_Brand": input_row.unilog_brand,
                "DIB_Brand": input_row.dib_brand,
                "Part_Manuf": input_row.part_manuf,
            }
        else:
            identity = product.identity
            passthrough = {
                "Mfg_Part_Num": identity.mpn,
                "Part_Desc": identity.raw_description,
                "E1_Brand": identity.brand,
                "Unilog_Brand": "",
                "DIB_Brand": "",
                "Part_Manuf": identity.manufacturer,
            }
        for name, value in passthrough.items():
            self._set(row, name, value)

    # -- evidence URLs -----------------------------------------------------

    def _map_evidence(
        self, row: DeliveryRow, product: ProductIntelligence
    ) -> None:
        evidence = sorted(product.evidence.values(), key=lambda e: e.id)
        if not evidence:
            return
        requested = _canonical_mpn(product.identity.mpn)
        if not requested:
            # Legacy behavior when no MPN identity is available: keep the
            # historical selection (first product page, else first evidence).
            product_page = next(
                (
                    e
                    for e in evidence
                    if e.source_type == SourceType.MANUFACTURER_PRODUCT_PAGE
                ),
                None,
            )
            if product_page is not None:
                self._set(row, "MFR URL", product_page.source_url)
                rest = [e for e in evidence if e.id != product_page.id]
            else:
                self._set(row, "MFR URL", evidence[0].source_url)
                rest = evidence[1:]
            for offset, evidence_item in enumerate(rest[:5]):
                self._set(
                    row, f"Ref URL {offset + 1}", evidence_item.source_url
                )
            return

        # MPN-aware delivery traceability: only evidence that references the
        # requested MPN may become a citation. MFR URL must be a
        # manufacturer product page for the requested product: an exact MPN
        # match, else a page whose slug is a strict prefix of the MPN (e.g.
        # WDTS7024R for WDTS7024RZ). A page that names a DIFFERENT MPN
        # (sibling/extension such as XLC10ZW-2) is never cited as the
        # manufacturer URL, and no URL is ever invented. Ref URLs contain
        # only relevant (exact/soft) evidence, with unused cells left blank.
        product_pages = [
            e
            for e in evidence
            if e.source_type == SourceType.MANUFACTURER_PRODUCT_PAGE
        ]
        mfr_page: Evidence | None = None
        for kind in ("exact", "soft"):
            if mfr_page is not None:
                break
            mfr_page = next(
                (
                    e
                    for e in product_pages
                    if _match_kind(e, requested, url_only=True) == kind
                ),
                None,
            )
        if mfr_page is not None:
            self._set(row, "MFR URL", mfr_page.source_url)
        relevant = [
            e
            for e in evidence
            if e.id != (mfr_page.id if mfr_page is not None else None)
            and _references_mpn(e, requested)
        ]
        for offset, evidence_item in enumerate(
            sorted(relevant, key=lambda e: e.id)[:5]
        ):
            self._set(row, f"Ref URL {offset + 1}", evidence_item.source_url)

    # -- descriptions ------------------------------------------------------

    def _map_descriptions(
        self, row: DeliveryRow, product: ProductIntelligence
    ) -> None:
        descriptions = product.descriptions
        mapping = {
            "MOBILE_DESC": descriptions.mobile_description,
            "INVOICE_DESC": descriptions.invoice_description,
            "SHORT_DESC": descriptions.short_description,
            "LONG_DESC1": descriptions.long_description,
            "RETAIL_DESC": descriptions.retail_description,
            "MARKETING_DESCRIPTION": descriptions.marketing_description,
            "With": descriptions.with_,
            "Application": descriptions.application,
            "Includes": descriptions.includes,
            "Product Name": descriptions.product_name,
        }
        for name, value in mapping.items():
            if value:
                self._set(row, name, value)
        for slot, feature in enumerate(descriptions.item_features[:20]):
            if feature:
                self._set(row, f"ITEM_FEATURES_{slot + 1}", feature)
        if len(descriptions.item_features) > 20:
            row.note(
                "ITEM_FEATURES: only the first 20 of "
                f"{len(descriptions.item_features)} features written; "
                f"{len(descriptions.item_features) - 20} truncated"
            )

    # -- classification ----------------------------------------------------

    def _map_classification(
        self, row: DeliveryRow, product: ProductIntelligence
    ) -> None:
        classification = product.classification
        mapping = {
            "Dept": classification.department,
            "Class": classification.class_,
            "Fine": classification.fine,
            "Classpath": classification.classpath,
        }
        for name, value in mapping.items():
            if value:
                self._set(row, name, value)

    # -- attributes --------------------------------------------------------

    def _map_attributes(
        self, row: DeliveryRow, product: ProductIntelligence
    ) -> None:
        slots = self.schema.attribute_slots()
        attributes = list(product.attributes.values())
        for slot, attribute in enumerate(attributes[:ATTRIBUTE_SLOT_COUNT]):
            label_name, value_name, uom_name = slots[slot]
            self._set(row, label_name, attribute.name)
            serialized_value = attribute.value or attribute.raw_value
            if serialized_value:
                self._set(row, value_name, serialized_value)
            if attribute.unit:
                self._set(row, uom_name, attribute.unit)
        if len(attributes) > ATTRIBUTE_SLOT_COUNT:
            row.note(
                "ATTRIBUTE slots: only the first "
                f"{ATTRIBUTE_SLOT_COUNT} of {len(attributes)} attributes "
                "written; "
                f"{len(attributes) - ATTRIBUTE_SLOT_COUNT} truncated"
            )

    # -- digital assets ----------------------------------------------------

    def _map_assets(
        self, row: DeliveryRow, product: ProductIntelligence
    ) -> None:
        for evidence_item in product.evidence.values():
            for column_name, url in evidence_item.assets.items():
                if not self.schema.column_exists(column_name):
                    row.note(
                        f"asset column {column_name!r} not present in the "
                        "delivery schema; asset skipped"
                    )
                    continue
                if self._set(row, column_name, url):
                    row.note(
                        f"duplicate asset for {column_name!r}: kept first, "
                        f"skipped {url!r}"
                    )

    # -- ambiguous-but-fillable-when-verified fields -----------------------

    def _map_ambiguous_blanks(
        self, row: DeliveryRow, product: ProductIntelligence
    ) -> None:
        fillable = {
            "PART_NUMBER": product.identity.mpn,
            "SKU - MY_PART_NUMBER": product.identity.sku or "",
            "MANUFACTURER_NAME": product.identity.verified_manufacturer,
            "BRAND_NAME": product.identity.verified_brand,
            "TRADE_NAME": product.identity.verified_trade_name,
        }
        for name, value in fillable.items():
            if value:
                self._set(row, name, value)
            else:
                row.note(f"{name}: {NOTE_ENRICHMENT}")
        for name in ("Dept", "Class", "Fine", "Classpath"):
            if not self._get(row, name):
                row.note(f"{name}: {NOTE_TAXONOMY}")

    # -- helpers -----------------------------------------------------------

    def _set(self, row: DeliveryRow, name: str, value: str) -> bool:
        """Set a column by official name; True when it was already filled."""
        if not self.schema.column_exists(name):
            raise KeyError(f"unknown delivery column: {name}")
        index = self.schema.index_of(name)
        already = row.values[index] != ""
        if not already:
            row.values[index] = value
        return already

    def _get(self, row: DeliveryRow, name: str) -> str:
        return row.values[self.schema.index_of(name)]
