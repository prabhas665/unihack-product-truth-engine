"""Regression tests for the extraction evidence-selection policy (Step 20).

All providers are fakes; no network and no real API calls. These prove:

1. A sibling manufacturer page (different MPN, no mention of the requested
   MPN) cannot be used as extraction evidence for the requested product, and
   an attribute that tries to cite the sibling's evidence id is rejected as
   dangling.
2. The total extraction context respects the configured character budget.
3. PRIMARY (url/title match) outranks a text-only SECONDARY mention.
"""

from __future__ import annotations

from app.core.domain import ProductIdentity, SourceType
from app.extraction.selection import select_extraction_evidence
from app.extraction.types import ExtractionRequest
from app.extraction.service import ExtractionService
from app.llm import LLMClient
from app.sources.retrieval import EvidenceRecord, ExtractionStatus, RetrievalStatus


def _record(ev_id: str, url: str, title: str, text: str) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=ev_id,
        source_candidate_id=f"cand-{ev_id}",
        url=url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        title=title,
        text=text,
        content_type="text/html",
        retrieval_status=RetrievalStatus.SUCCESS,
        extraction_status=ExtractionStatus.EXTRACTED,
    )


def _xlc10zw_request() -> ProductIdentity:
    return ProductIdentity(mpn="XLC10ZW", manufacturer="Makela", brand="Makita")


class _StubLLM(LLMClient):
    """Echoes a single attribute citing exactly the requested evidence id."""

    provider = "stub"

    def __init__(self, evidence_id: str) -> None:
        self._evidence_id = evidence_id

    def _complete(self, prompt, **kwargs) -> str:
        return (
            '{"items":[{"name":"voltage","raw_value":"18V","normalized_value":"18",'
            f'"unit":"V","confidence":0.9,"evidence_ids":["{self._evidence_id}"],'
            '"notes":"from page"}]}'
        )


def test_sibling_page_excluded_from_selection():
    primary = _record(
        "ev-xlc10zw",
        "https://makitatools.com/products/details/XLC10ZW",
        "XLC10ZW",
        "XLC10ZW Makita 18V cordless tool.",
    )
    sibling = _record(
        "ev-xlc10r1w",
        "https://makitatools.com/products/details/XLC10R1W",
        "XLC10R1W",
        "XLC10R1W Makita 18V cordless tool, a different product.",
    )
    result = select_extraction_evidence(
        _xlc10zw_request(), [primary, sibling], budget_chars=12_000
    )
    selected_ids = [r.evidence_id for r in result.selected]
    assert selected_ids == ["ev-xlc10zw"]
    assert any("ev-xlc10r1w" in reason for reason in result.dropped)


def test_sibling_evidence_id_cannot_be_cited():
    """Even if the model cites a sibling's evidence id, it is rejected as
    dangling because that id is never supplied to extraction."""
    primary = _record(
        "ev-xlc10zw",
        "https://makitatools.com/products/details/XLC10ZW",
        "XLC10ZW",
        "XLC10ZW Makita 18V cordless tool.",
    )
    sibling = _record(
        "ev-xlc10r1w",
        "https://makitatools.com/products/details/XLC10R1W",
        "XLC10R1W",
        "XLC10R1W is a different Makita tool.",
    )
    selection = select_extraction_evidence(
        _xlc10zw_request(), [primary, sibling], budget_chars=12_000
    )
    # The model tries to cite the sibling's evidence id.
    service = ExtractionService(_StubLLM("ev-xlc10r1w"))
    response = service.extract(
        ExtractionRequest(
            identity=_xlc10zw_request(),
            evidence_records=selection.selected,
        )
    )
    assert response.attributes == []  # dangling citation refused
    assert any("dangling" in r.reason.lower() for r in response.rejected)


def test_sibling_mentioning_requested_mpn_is_kept():
    """A page whose text explicitly applies to the requested MPN is kept."""
    primary = _record(
        "ev-xlc10zw",
        "https://makitatools.com/products/details/XLC10ZW",
        "XLC10ZW",
        "XLC10ZW Makita 18V cordless tool.",
    )
    compatible = _record(
        "ev-xlc10r1w",
        "https://makitatools.com/products/details/XLC10R1W",
        "XLC10R1W",
        "XLC10R1W is compatible with the XLC10ZW battery pack.",
    )
    result = select_extraction_evidence(
        _xlc10zw_request(), [primary, compatible], budget_chars=12_000
    )
    assert {r.evidence_id for r in result.selected} == {
        "ev-xlc10zw",
        "ev-xlc10r1w",
    }


def test_context_budget_enforced():
    records = [
        _record(
            f"ev-p{i}",
            f"https://makita.example/{mpn}",
            mpn,
            f"{mpn} Makita 18V tool. " + "spec " * 1500,
        )
        for i, mpn in enumerate(
            ["XLC10ZW", "XLC10R1W", "XLC03R1WX4", "XLC08R1B"]
        )
    ]
    # Only XLC10ZW is the requested MPN; the rest are siblings -> excluded.
    result = select_extraction_evidence(
        _xlc10zw_request(), records, budget_chars=12_000
    )
    selected = result.selected
    assert [r.evidence_id for r in selected] == ["ev-p0"]
    # Verify the delivered context stays within budget.
    used = sum(
        min(len(r.text), 6000) + len(f"[{r.evidence_id}] {r.title} | {r.url}")
        for r in selected
    )
    assert used <= 12_000


def test_primary_outranks_text_only_secondary():
    url_primary = _record(
        "ev-primary",
        "https://makita.example/XLC10ZW",
        "XLC10ZW",
        "XLC10ZW Makita 18V tool.",
    )
    text_secondary = _record(
        "ev-secondary",
        "https://makita.example/accessories",
        "Accessory kit",
        "Works with XLC10ZW. Generic Makita accessory.",
    )
    result = select_extraction_evidence(
        _xlc10zw_request(),
        [text_secondary, url_primary],
        budget_chars=12_000,
    )
    assert [r.evidence_id for r in result.selected] == [
        "ev-primary",
        "ev-secondary",
    ]


def test_empty_mpn_keeps_every_record():
    """No usable MPN identity means nothing can be judged a sibling.

    Regression: an empty MPN previously inverted the sibling logic and
    dropped every record that carried any product token.
    """
    records = [
        _record(
            "ev-a",
            "https://makita.example/XLC10ZW",
            "XLC10ZW",
            "XLC10ZW 18V tool.",
        ),
        _record(
            "ev-b",
            "https://makita.example/XLC10R1W",
            "XLC10R1W",
            "XLC10R1W 18V tool.",
        ),
    ]
    result = select_extraction_evidence(
        ProductIdentity(mpn="", manufacturer="Makela", brand="Makita"),
        records,
        budget_chars=12_000,
    )
    assert {r.evidence_id for r in result.selected} == {"ev-a", "ev-b"}
    assert result.dropped == []


def test_hyphenated_kit_variant_is_a_sibling_not_a_match():
    """XLC10ZW-2 is a DIFFERENT product token, never a match for XLC10ZW."""
    exact = _record(
        "ev-xlc10zw",
        "https://makitatools.com/products/details/XLC10ZW",
        "XLC10ZW",
        "XLC10ZW Makita 18V tool.",
    )
    kit = _record(
        "ev-kit",
        "https://makitatools.com/products/details/XLC10ZW-2",
        "XLC10ZW-2",
        "XLC10ZW-2 kit with charger.",
    )
    result = select_extraction_evidence(
        _xlc10zw_request(), [exact, kit], budget_chars=12_000
    )
    assert [r.evidence_id for r in result.selected] == ["ev-xlc10zw"]
    assert any("ev-kit" in reason for reason in result.dropped)


def test_substring_lookalike_is_not_a_match():
    """A longer token (XLC10ZWX) never satisfies a request for XLC10ZW.

    Regression: the old substring check treated XLC10ZWX as a mention of
    XLC10ZW and promoted the wrong page.
    """
    exact = _record(
        "ev-xlc10zw",
        "https://makitatools.com/products/details/XLC10ZW",
        "XLC10ZW",
        "XLC10ZW Makita 18V tool.",
    )
    lookalike = _record(
        "ev-zwx",
        "https://makitatools.com/products/details/XLC10ZWX",
        "XLC10ZWX",
        "XLC10ZWX is a different Makita model.",
    )
    result = select_extraction_evidence(
        _xlc10zw_request(), [exact, lookalike], budget_chars=12_000
    )
    assert [r.evidence_id for r in result.selected] == ["ev-xlc10zw"]
    assert any("ev-zwx" in reason for reason in result.dropped)


def test_sibling_mpn_only_in_url_slug_is_excluded():
    """The URL slug is scanned: a sibling named only in the slug is dropped.

    Regression: siblinghood was decided on title/text alone, so a page whose
    slug names a foreign MPN but whose title/body carry no token leaked in.
    """
    exact = _record(
        "ev-xlc10zw",
        "https://makitatools.com/products/details/XLC10ZW",
        "XLC10ZW",
        "XLC10ZW Makita 18V tool.",
    )
    slug_sibling = _record(
        "ev-slug",
        "https://makitatools.com/products/details/XLC08ZB",
        "Cordless vacuum",
        "Cordless stick vacuum for home use.",
    )
    result = select_extraction_evidence(
        _xlc10zw_request(), [exact, slug_sibling], budget_chars=12_000
    )
    assert [r.evidence_id for r in result.selected] == ["ev-xlc10zw"]
    assert any("ev-slug" in reason for reason in result.dropped)


def test_hyphenated_mpn_kept_as_one_token():
    """49-94-0013 is one token and matches its exact product page."""
    identity = ProductIdentity(
        mpn="49-94-0013", manufacturer="Milwaukee", brand="Milwaukee"
    )
    exact = _record(
        "ev-m18",
        "https://www.milwaukeetool.com/products/49-94-0013",
        "49-94-0013",
        "49-94-0013 Milwaukee tool.",
    )
    result = select_extraction_evidence(identity, [exact], budget_chars=12_000)
    assert [r.evidence_id for r in result.selected] == ["ev-m18"]
    assert result.dropped == []
