"""P0 claim-support gate tests (evidence-grounded acceptance).

An extracted attribute is accepted ONLY when its claimed value occurs
deterministically (verbatim, whitespace-tolerant) in at least one cited
evidence record, attributed to the requested product's own passage (within
CLAIM_MPN_WINDOW_CHARS of the requested MPN) or to family copy not
attributable to any other product. Claims whose value only appears near
OTHER products' codes - or not at all - are rejected with
"claim not found in cited evidence" and never carry a quote.

Every path is covered: JSON output, LLM-5 salvage, bullet-list fallback,
and delivery. One regression test replays the REAL saved XLC10ZW probe
attributes against the real Makita category-page text
(fixtures/xlc10zw_category_page.json): the claims that were previously
contaminated by sibling passages (Speed Settings from GLC04Z, Dust Bag
Included with an empty quote) must now be rejected or re-anchored.

All tests are offline: FakeLLMClient with canned responses and the saved
fixture - no network calls, no real providers.

TEST FIXTURES: made-up evidence text used only to exercise extraction
logic, plus the saved real category-page fixture for the regression.
These are NOT fabrications of UniHack data.
"""

import json

import pytest

from app.core.domain import ProductIdentity, SourceType
from app.extraction import (
    ExtractionRequest,
    ExtractionResponse,
    ExtractionService,
)
from app.llm import FakeLLMClient
from app.sources.retrieval import EvidenceRecord, RetrievalStatus
from tests.test_extraction_failover import (  # noqa: E402,F401 - shared offline fakes
    FakeProvider,
    FakeRetriever,
    PipelineLLM,
    default_request,
    make_service as failover_service,
)

REJECT_REASON = "claim not found in cited evidence"

FIXTURE_DIR = "tests/fixtures"


def load_fixture(name: str) -> dict:
    with open(f"{FIXTURE_DIR}/{name}", encoding="utf-8") as fh:
        return json.load(fh)


def make_evidence(
    evidence_id: str, text: str, title: str = "", url: str = ""
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        source_candidate_id="cand-" + evidence_id,
        url=url or f"https://acme-controls.example/{evidence_id}",
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        title=title or "Fixture page",
        text=text,
        retrieval_status=RetrievalStatus.SUCCESS,
    )


def make_request(mpn: str, *records: EvidenceRecord) -> ExtractionRequest:
    return ExtractionRequest(
        identity=ProductIdentity(manufacturer="Acme Controls", mpn=mpn),
        evidence_records=list(records),
    )


def service_with(response_json: str) -> ExtractionService:
    return ExtractionService(FakeLLMClient(responses=[response_json]))


def items_json(*items: str) -> str:
    return '{"items": [' + ",".join(items) + "]}"


@pytest.fixture(scope="module")
def page() -> EvidenceRecord:
    fixture = load_fixture("xlc10zw_category_page.json")
    return make_evidence(
        fixture["evidence_id"],
        fixture["text"],
        title=fixture["title"],
        url=fixture["url"],
    )


@pytest.fixture(scope="module")
def probe_items() -> dict:
    probe = load_fixture("xlc10zw_probe_attributes.json")
    return {att["name"]: att for att in probe["attributes"]}


def item(
    name: str,
    raw_value: str,
    evidence_ids: str,
    normalized_value: str = "",
    confidence: str = "0.9",
) -> str:
    return (
        '{"name": "' + name + '", "raw_value": "' + raw_value + '", '
        '"normalized_value": "' + normalized_value + '", '
        '"confidence": ' + confidence + ', '
        '"evidence_ids": ' + evidence_ids + "}"
    )


# A: the value does not occur anywhere in the cited evidence -> rejected.
class TestAValueAbsent:
    def test_value_absent_anywhere_is_rejected(self):
        record = make_evidence("ev-1", "The M1 has IP65 protection.")
        response = service_with(
            items_json(item("voltage", "24 V DC", '["ev-1"]'))
        ).extract(make_request("M1", record))
        assert response.attributes == []
        assert len(response.rejected) == 1
        assert response.rejected[0].name == "voltage"
        assert REJECT_REASON in response.rejected[0].reason

    def test_value_absent_in_every_cited_record_is_rejected(self):
        records = [
            make_evidence("ev-1", "The M1 is blue."),
            make_evidence("ev-2", "The M1 is compact."),
        ]
        response = service_with(
            items_json(item("voltage", "24 V DC", '["ev-1", "ev-2"]'))
        ).extract(make_request("M1", *records))
        assert response.attributes == []
        assert REJECT_REASON in response.rejected[0].reason


# B: an empty quote is never accepted - it is a rejection, not a pass.
class TestBEmptyQuoteNeverAccepted:
    def test_empty_evidence_text_rejects_the_claim(self):
        record = make_evidence("ev-1", "")
        response = service_with(
            items_json(item("voltage", "24 V", '["ev-1"]'))
        ).extract(make_request("M1", record))
        assert response.attributes == []
        assert len(response.rejected) == 1
        assert REJECT_REASON in response.rejected[0].reason

    def test_rejection_carries_no_quote(self):
        record = make_evidence("ev-1", "The M1 has IP65 protection.")
        response = service_with(
            items_json(item("voltage", "24 V DC", '["ev-1"]'))
        ).extract(make_request("M1", record))
        assert response.rejected[0].raw_value == "24 V DC"


# C: the value occurs in the requested product's own passage -> accepted
# with a quote anchored to that passage.
class TestCDirectSupport:
    def test_value_near_requested_mpn_accepted(self):
        record = make_evidence("ev-1", "The M1 operates at 24 V DC.")
        attribute = service_with(
            items_json(item("voltage", "24 V DC", '["ev-1"]'))
        ).extract(make_request("M1", record)).attributes[0]
        assert attribute.name == "voltage"
        assert attribute.quote != ""
        assert "24 V DC" in attribute.quote
        assert attribute.quote.startswith("The M1 operates at")

    def test_late_occurrence_in_own_passage_still_supports(self):
        record = make_evidence(
            "ev-1",
            "Intro copy with no values. The M1 product listing: operates "
            "at 24 V DC and is fully sealed.",
        )
        attribute = service_with(
            items_json(item("voltage", "24 V DC", '["ev-1"]'))
        ).extract(make_request("M1", record)).attributes[0]
        assert "24 V DC" in attribute.quote
        assert "M1" in attribute.quote


# D: the value occurs ONLY near other products' codes -> rejected, and the
# page itself is not rejected as a whole (claim-level, not page-level).
class TestDSiblingContamination:
    SIBLING_PAGE = (
        "GLC04Z\n\n40V max XGT Brushless Cordless Cyclonic Power Brush "
        "Head 4-Speed Compact Stick Vacuum, Tool Only\n\n"
        "XLC11ZW\n\n18V LXT Lithium-ion Compact Brushless Cordless "
        "Cyclonic 4-Speed Stick Vacuum (Tool Only)\n\n"
        "XLC10ZW\n\n18V LXT Lithium-ion Compact Brushless Cordless "
        "4 -Speed Vacuum, w/ Push Button and Dust Bag (Tool Only)"
    )

    def test_value_only_near_sibling_mpn_rejected(self):
        record = make_evidence("ev-1", self.SIBLING_PAGE)
        response = service_with(
            items_json(item("speed_settings", "4-Speed", '["ev-1"]'))
        ).extract(make_request("XLC10ZW", record))
        assert response.attributes == []
        assert len(response.rejected) == 1
        assert response.rejected[0].name == "speed_settings"
        assert REJECT_REASON in response.rejected[0].reason

    def test_own_passage_claim_on_same_page_accepted(self):
        record = make_evidence("ev-1", self.SIBLING_PAGE)
        response = service_with(
            items_json(
                item("voltage", "18V", '["ev-1"]'),
                item("speed_settings", "4-Speed", '["ev-1"]'),
            )
        ).extract(make_request("XLC10ZW", record))
        assert [a.name for a in response.attributes] == ["voltage"]
        assert response.attributes[0].quote.count("XLC10ZW") >= 1
        assert response.rejected[0].name == "speed_settings"


# E: multiple cited records - support is searched across ALL of them and
# the quote comes from the record that actually supports the claim.
class TestEMultipleCitedRecords:
    def test_support_found_in_second_cited_record(self):
        records = [
            make_evidence("ev-1", "The M1 has a blue housing."),
            make_evidence("ev-2", "The M1 operates at 24 V DC."),
        ]
        attribute = service_with(
            items_json(item("voltage", "24 V DC", '["ev-1", "ev-2"]'))
        ).extract(make_request("M1", *records)).attributes[0]
        assert attribute.evidence_ids == ["ev-1", "ev-2"]
        assert "24 V DC" in attribute.quote
        assert "blue housing" not in attribute.quote

    def test_none_of_the_cited_records_support_rejects(self):
        records = [
            make_evidence("ev-1", "The M1 has a blue housing."),
            make_evidence("ev-2", "The M1 is compact."),
        ]
        response = service_with(
            items_json(item("voltage", "24 V DC", '["ev-1", "ev-2"]'))
        ).extract(make_request("M1", *records))
        assert response.attributes == []
        assert REJECT_REASON in response.rejected[0].reason


# F: normalization - the existing quote resolver's tolerance applies, but
# values the resolver cannot match stay rejected (no fuzzy matching).
class TestFNormalization:
    def test_whitespace_variants_accepted(self):
        record = make_evidence("ev-1", "Voltage rating:\n  24 V\n  DC input.")
        attribute = service_with(
            items_json(item("voltage", "24 V DC", '["ev-1"]'))
        ).extract(make_request("M1", record)).attributes[0]
        assert "24 V DC" in attribute.quote

    def test_case_insensitive_match_accepted(self):
        record = make_evidence("ev-1", "The M1 runs at 18v.")
        attribute = service_with(
            items_json(item("voltage", "18V", '["ev-1"]'))
        ).extract(make_request("M1", record)).attributes[0]
        assert "18v" in attribute.quote

    def test_unsupported_spacing_variant_rejected(self):
        record = make_evidence("ev-1", "The M1 runs at 18V.")
        response = service_with(
            items_json(item("voltage", "18 V", '["ev-1"]'))
        ).extract(make_request("M1", record))
        assert response.attributes == []
        assert REJECT_REASON in response.rejected[0].reason

    def test_normalized_value_preferred_over_raw(self):
        record = make_evidence(
            "ev-1", "Length is 100 mm (approximately ten centimeters)."
        )
        attribute = service_with(
            items_json(
                item(
                    "length",
                    "10 cm",
                    '["ev-1"]',
                    normalized_value="100 mm",
                )
            )
        ).extract(make_request("M1", record)).attributes[0]
        assert "100 mm" in attribute.quote
        assert "10 cm" not in attribute.quote


# G: the LLM-5 salvage path applies the same gate.
class TestGSalvagePath:
    def test_salvaged_supported_item_accepted(self):
        record = make_evidence("ev-1", "The M1 operates at 24 V DC.")
        raw = items_json(
            item("voltage", "24 V DC", '["ev-1"]', confidence='"high"'),
            item("bogus", "x", '["ev-1"]', confidence='"not-a-confidence"'),
        )
        response = service_with(raw).extract(make_request("M1", record))
        assert [a.name for a in response.attributes] == ["voltage"]
        assert len(response.rejected) == 1
        assert "not-a-confidence" in response.rejected[0].reason

    def test_salvaged_unsupported_item_rejected_by_gate(self):
        record = make_evidence("ev-1", "The M1 operates at 24 V DC.")
        raw = items_json(
            item("speed_settings", "4-Speed", '["ev-1"]', confidence='"high"')
        )
        response = service_with(raw).extract(make_request("M1", record))
        assert response.attributes == []
        assert len(response.rejected) == 1
        assert response.rejected[0].name == "speed_settings"
        assert REJECT_REASON in response.rejected[0].reason


# H: the bullet-list fallback applies the same gate.
class TestHBulletFallback:
    def test_bullet_with_unsupported_value_rejected(self):
        record = make_evidence("ev-1", "The M1 operates at 24 V DC.")
        client = FakeLLMClient(
            responses=[
                "this is definitely not json",
                "- Voltage: 24 V DC [ev-1]\n- Speed: 4-Speed [ev-1]",
            ]
        )
        response = ExtractionService(client).extract(make_request("M1", record))
        assert [a.name for a in response.attributes] == ["Voltage"]
        assert response.attributes[0].quote != ""
        assert len(response.rejected) == 1
        assert response.rejected[0].name == "Speed"
        assert REJECT_REASON in response.rejected[0].reason


# I: delivery - rejected claims never reach the 252-column output.
class TestIDelivery:
    def test_rejected_claim_absent_from_delivery(self):
        result = failover_service(
            PipelineLLM(
                extraction=json.dumps(
                    {
                        "items": [
                            {
                                "name": "belt_width",
                                "raw_value": "0.5 inch",
                                "normalized_value": "0.5 in",
                                "unit": "in",
                                "confidence": 0.9,
                                "evidence_ids": ["ev-acme-page-0001"],
                            },
                            {
                                "name": "speed_settings",
                                "raw_value": "4-Speed",
                                "confidence": 0.9,
                                "evidence_ids": ["ev-acme-page-0001"],
                            },
                        ]
                    }
                )
            )
        ).run(default_request())
        assert result.extraction is not None
        assert [a.name for a in result.extraction.attributes] == ["belt_width"]
        assert {r.name for r in result.extraction.rejected} == {"speed_settings"}
        assert any(
            "claim not found in cited evidence" in r
            for r in result.review_reasons
        )
        delivery_text = "|".join(result.delivery.values)
        assert "4-Speed" not in delivery_text
        assert "speed_settings" not in delivery_text
        assert "0.5 in" in delivery_text
        assert "belt_width" in delivery_text


# J: no fabrication - quotes are verbatim slices of the cited evidence text.
class TestJNoFabrication:
    def test_accepted_quote_is_verbatim_evidence_slice(self):
        record = make_evidence("ev-1", "The M1 operates at 24 V DC.")
        attribute = service_with(
            items_json(item("voltage", "24 V DC", '["ev-1"]'))
        ).extract(make_request("M1", record)).attributes[0]
        body = attribute.quote.strip("…").strip()
        assert body in record.text

    def test_multi_record_quote_comes_verbatim_from_supporting_record(self):
        records = [
            make_evidence("ev-1", "The M1 has a blue housing."),
            make_evidence("ev-2", "The M1 operates at 24 V DC."),
        ]
        attribute = service_with(
            items_json(item("voltage", "24 V DC", '["ev-1", "ev-2"]'))
        ).extract(make_request("M1", *records)).attributes[0]
        body = attribute.quote.strip("…").strip()
        assert body in records[1].text


# ---------------------------------------------------------------------------
# Regression: the REAL saved XLC10ZW probe against the REAL saved Makita
# category page (record fb15f75b69ed, makitatools.com compact vacuums).
# Before the gate: Speed Settings carried a GLC04Z sibling quote, Dust Bag
# Included was accepted with an empty quote, and Battery Chemistry carried
# an XLC08ZB sibling quote.
# ---------------------------------------------------------------------------
class TestXlc10zwRegression:
    def _extract(self, page: EvidenceRecord, *items: str) -> ExtractionResponse:
        return service_with(items_json(*items)).extract(
            make_request("XLC10ZW", page)
        )

    def test_previously_contaminated_claims_are_rejected(
        self, page, probe_items
    ):
        response = self._extract(
            page,
            item("Speed Settings", "4-Speed", '["fb15f75b69ed"]'),
            item("Dust Bag Included", "Dust Bag Included", '["fb15f75b69ed"]'),
        )
        assert response.attributes == []
        assert len(response.rejected) == 2
        assert all(
            REJECT_REASON in rejected.reason for rejected in response.rejected
        )

    def test_sibling_quote_contamination_is_reanchored(
        self, page: EvidenceRecord
    ):
        attribute = self._extract(
            page,
            item("Battery Chemistry", "Lithium-ion", '["fb15f75b69ed"]'),
        ).attributes[0]
        assert attribute.quote != ""
        assert "XLC10ZW" in attribute.quote
        assert "XLC08ZB" not in attribute.quote

    def test_own_passage_claims_stay_accepted(self, page: EvidenceRecord):
        response = self._extract(
            page,
            item("Voltage", "18V", '["fb15f75b69ed"]'),
            item("Motor Type", "Brushless", '["fb15f75b69ed"]'),
            item("Power Source", "Cordless", '["fb15f75b69ed"]'),
        )
        assert len(response.attributes) == 3
        for attribute in response.attributes:
            assert "XLC10ZW" in attribute.quote
            assert attribute.quote != ""

    def test_generic_family_copy_stays_accepted(self, page: EvidenceRecord):
        attribute = self._extract(
            page,
            item("Product Type", "Compact Vacuum", '["fb15f75b69ed"]'),
        ).attributes[0]
        assert "Compact Vacuum" in attribute.quote

    def test_attributes_with_unsupported_values_rejected(
        self, page: EvidenceRecord
    ):
        response = self._extract(
            page,
            item("Speed Settings", "4-Speed", '["fb15f75b69ed"]'),
            item("Dust Bag Included", "Dust Bag Included", '["fb15f75b69ed"]'),
            item("Voltage", "18V", '["fb15f75b69ed"]'),
            item("Motor Type", "Brushless", '["fb15f75b69ed"]'),
            item("Power Source", "Cordless", '["fb15f75b69ed"]'),
            item("Battery Chemistry", "Lithium-ion", '["fb15f75b69ed"]'),
            item("Product Type", "Compact Vacuum", '["fb15f75b69ed"]'),
        )
        accepted = {a.name for a in response.attributes}
        rejected = {r.name for r in response.rejected}
        assert accepted == {
            "Voltage",
            "Motor Type",
            "Power Source",
            "Battery Chemistry",
            "Product Type",
        }
        assert rejected == {"Speed Settings", "Dust Bag Included"}
        assert all(
            REJECT_REASON in r.reason for r in response.rejected
        )
        assert all(a.quote != "" for a in response.attributes)