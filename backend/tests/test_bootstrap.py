"""Unit tests for the identity bootstrap module (app.sources.bootstrap).

All tests are offline: fake providers and retrievers return canned data.
No network calls or real API credentials are used.
"""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest

from app.core.domain import ProductIdentity, SourceType
from app.sources.bootstrap import (
    BootstrapResult,
    _check_sibling_contamination,
    _extract_manufacturer_from_text,
    _filter_candidates,
    _is_marketplace,
    _mpn_present_in_text,
    _select_domain,
    _verify_strong_evidence,
    bootstrap_identity,
)
from app.sources.candidates import CandidateStatus, DiscoveryMethod, SourceCandidate
from app.sources.retrieval.models import EvidenceRecord, RetrievalStatus


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _candidate(
    url: str,
    title: str = "",
) -> SourceCandidate:
    return SourceCandidate(
        id=f"cand-{hashlib.sha256(url.encode()).hexdigest()[:12]}",
        url=url,
        title=title or url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
    )


def _success_record(
    url: str,
    *,
    evidence_id: str = "",
    text: str = "",
    title: str = "",
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id or f"ev-{hashlib.sha256(url.encode()).hexdigest()[:12]}",
        url=url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        title=title or url,
        text=text,
        content_type="text/html",
        retrieval_status=RetrievalStatus.SUCCESS,
    )


def _failed_record(url: str) -> EvidenceRecord:
    return EvidenceRecord(
        url=url,
        retrieval_status=RetrievalStatus.FAILED,
        error_message="connection refused",
    )


class FakeProvider:
    name = "fake-search"
    kind = DiscoveryMethod.SEARCH

    def __init__(self, candidates: list[SourceCandidate] | None = None):
        self._candidates = candidates or []

    def discover(self, product, context):
        return list(self._candidates)


class FakeRetriever:
    def __init__(self, records: list[EvidenceRecord]) -> None:
        self.by_url = {r.url: r for r in records}

    def __call__(self, candidate: SourceCandidate) -> EvidenceRecord:
        return self.by_url.get(
            candidate.url,
            EvidenceRecord(
                url=candidate.url,
                retrieval_status=RetrievalStatus.FAILED,
                error_message="not found in fake retriever",
            ),
        )


def _mirka_product() -> ProductIdentity:
    return ProductIdentity(
        manufacturer="",
        brand="",
        mpn="5B-332-080",
        raw_description="Mirka abrasive disc 5 inch",
    )


def _diablo_product() -> ProductIdentity:
    return ProductIdentity(
        manufacturer="Freud",
        brand="Diablo",
        mpn="DCB518ASTS06G",
        raw_description='Diablo 1/2"x18" sanding belt 6pc',
    )


def _no_mfr_product() -> ProductIdentity:
    return ProductIdentity(
        manufacturer="",
        brand="",
        mpn="TEST-1234-ABCD",
        raw_description="some product",
    )


# --------------------------------------------------------------------------
# filtering tests
# --------------------------------------------------------------------------


class TestFiltering:
    def test_is_marketplace_amazon(self):
        assert _is_marketplace("www.amazon.com") is True
        assert _is_marketplace("amazon.co.uk") is True

    def test_is_marketplace_ebay(self):
        assert _is_marketplace("www.ebay.com") is True

    def test_is_not_marketplace(self):
        assert _is_marketplace("www.mirka.com") is False
        assert _is_marketplace("diablotools.com") is False

    def test_filter_removes_marketplace(self):
        candidates = [
            _candidate("https://www.amazon.com/product/123"),
            _candidate("https://www.mirka.com/product/123"),
        ]
        filtered = _filter_candidates(candidates)
        assert len(filtered) == 1
        assert "mirka" in filtered[0].url

    def test_filter_removes_non_http(self):
        candidates = [
            _candidate("ftp://example.com/file"),
            _candidate("https://www.example.com/page"),
        ]
        filtered = _filter_candidates(candidates)
        assert len(filtered) == 1

    def test_filter_deduplicates_urls(self):
        candidates = [
            _candidate("https://www.example.com/page"),
            _candidate("https://www.example.com/page"),
        ]
        filtered = _filter_candidates(candidates)
        assert len(filtered) == 1


# --------------------------------------------------------------------------
# MPN verification tests
# --------------------------------------------------------------------------


class TestMPNVerification:
    def test_mpn_present_in_text(self):
        assert _mpn_present_in_text("The 5B-332-080 is a disc", "5B-332-080") is True

    def test_mpn_case_insensitive(self):
        assert _mpn_present_in_text("the 5b-332-080 model", "5B-332-080") is True

    def test_mpn_not_in_text(self):
        assert _mpn_present_in_text("Some other product", "5B-332-080") is False

    def test_mpn_empty(self):
        assert _mpn_present_in_text("Some text", "") is False


# --------------------------------------------------------------------------
# sibling contamination tests
# --------------------------------------------------------------------------


class TestSiblingContamination:
    def test_no_contamination_when_mpn_present(self):
        clean, _ = _check_sibling_contamination(
            "The XLC10ZW disc", "https://example.com/xlc10zw", "XLC10ZW", "XLC10ZW"
        )
        assert clean is True

    def test_contamination_when_different_mpn(self):
        clean, reason = _check_sibling_contamination(
            "The ABCD-1234 disc",
            "https://example.com/abcd-1234",
            "ABCD-1234",
            "XLC10ZW",
        )
        assert clean is False
        assert "ABCD-1234" in reason

    def test_no_contamination_when_no_product_tokens(self):
        clean, _ = _check_sibling_contamination(
            "Generic product page with no part numbers",
            "https://example.com/products",
            "Product Page",
            "XLC10ZW",
        )
        assert clean is True


# --------------------------------------------------------------------------
# manufacturer extraction tests
# --------------------------------------------------------------------------


class TestManufacturerExtraction:
    def test_extract_manufacturer_with_suffix(self):
        mfr = _extract_manufacturer_from_text(
            "The product is made by Freud Inc. Quality tools since 1960.",
            "https://example.com/product",
            "Freud Product",
        )
        assert "Freud" in mfr
        assert "Inc" in mfr

    def test_extract_manufacturer_gmbh(self):
        mfr = _extract_manufacturer_from_text(
            "Manufactured by Mirka GmbH in Finland.",
            "https://example.com/product",
            "Mirka Product",
        )
        assert "Mirka" in mfr
        assert "GmbH" in mfr

    def test_extract_manufacturer_none_found(self):
        mfr = _extract_manufacturer_from_text(
            "A generic product with no company name.",
            "https://example.com/product",
            "Generic Product",
        )
        assert mfr == ""


# --------------------------------------------------------------------------
# strong evidence verification tests
# --------------------------------------------------------------------------


class TestStrongEvidenceVerification:
    def test_passes_with_all_signals(self):
        passed, reason = _verify_strong_evidence(
            text="The Freud Inc 5B-332-080 is an abrasive disc.",
            url="https://www.mirka.com/products/5b-332-080",
            title="Mirka 5B-332-080 Abrasive Disc",
            mpn="5B-332-080",
            part_manuf_input="Mirka Abrasives Inc",
        )
        assert passed is True
        assert "verified" in reason.lower()

    def test_fails_no_mpn_in_text(self):
        passed, reason = _verify_strong_evidence(
            text="Some product without the MPN mentioned.",
            url="https://www.example.com/product",
            title="Product Page",
            mpn="5B-332-080",
            part_manuf_input="Mirka Abrasives Inc",
        )
        assert passed is False
        assert "not found" in reason.lower()

    def test_fails_marketplace(self):
        passed, reason = _verify_strong_evidence(
            text="The 5B-332-080 product page on Amazon.",
            url="https://www.amazon.com/product/5b-332-080",
            title="5B-332-080 on Amazon",
            mpn="5B-332-080",
            part_manuf_input="Mirka Abrasives Inc",
        )
        assert passed is False
        assert "marketplace" in reason.lower()

    def test_fails_sibling_contamination(self):
        passed, reason = _verify_strong_evidence(
            text="The ABCD-1234 product. Also mentions 5B-332-080 briefly. "
                 "Mirka Abrasives Inc makes both.",
            url="https://www.example.com/abcd-1234",
            title="ABCD-1234 Product",
            mpn="5B-332-080",
            part_manuf_input="Mirka Abrasives Inc",
        )
        assert passed is True
        assert "verified" in reason.lower()

    def test_fails_manufacturer_mismatch(self):
        passed, reason = _verify_strong_evidence(
            text="The Freud Inc 5B-332-080 abrasive disc.",
            url="https://www.mirka.com/catalog/abrasives",
            title="Mirka 5B-332-080",
            mpn="5B-332-080",
            part_manuf_input="Bosch Power Tools Inc",
        )
        assert passed is False
        assert "mismatch" in reason.lower()

    def test_passes_with_url_path_mpn_override(self):
        """MPN in URL path overrides manufacturer mismatch (brand site)."""
        passed, reason = _verify_strong_evidence(
            text="Diablo makes the DCB518ASTS06G sanding belt.",
            url="https://diablotools.com/products/DCB518ASTS06G",
            title="DCB518ASTS06G Sanding Belt",
            mpn="DCB518ASTS06G",
            part_manuf_input="Freud Inc (2435)",
        )
        assert passed is True

    def test_passes_without_part_manuf(self):
        passed, reason = _verify_strong_evidence(
            text="The Freud Inc 5B-332-080 abrasive disc.",
            url="https://www.mirka.com/products/5b-332-080",
            title="Mirka 5B-332-080",
            mpn="5B-332-080",
            part_manuf_input="",
        )
        assert passed is True

    def test_passes_with_placeholder_part_manuf(self):
        passed, reason = _verify_strong_evidence(
            text="The Freud Inc 5B-332-080 abrasive disc.",
            url="https://www.mirka.com/products/5b-332-080",
            title="Mirka 5B-332-080",
            mpn="5B-332-080",
            part_manuf_input="-- No Part Manuf --",
        )
        assert passed is True

    def test_fails_no_manufacturer_and_no_input(self):
        passed, reason = _verify_strong_evidence(
            text="A product called 5B-332-080 with no company info.",
            url="https://www.example.com/5b-332-080",
            title="5B-332-080 Product",
            mpn="5B-332-080",
            part_manuf_input="",
        )
        assert passed is False
        assert "no manufacturer" in reason.lower()


# --------------------------------------------------------------------------
# domain selection tests
# --------------------------------------------------------------------------


class TestDomainSelection:
    def test_selects_most_common_domain(self):
        records = [
            _success_record("https://www.mirka.com/p1", text="mirka page 1"),
            _success_record("https://www.mirka.com/p2", text="mirka page 2"),
            _success_record("https://www.other.com/p1", text="other page"),
        ]
        domain = _select_domain(records)
        assert domain == "mirka.com"

    def test_selects_single_domain(self):
        records = [
            _success_record("https://diablotools.com/p1", text="diablo page"),
        ]
        domain = _select_domain(records)
        assert domain == "diablotools.com"


# --------------------------------------------------------------------------
# full bootstrap tests
# --------------------------------------------------------------------------


class TestBootstrapIdentity:
    def test_resolves_unknown_manufacturer(self):
        product = _mirka_product()
        candidates = [_candidate("https://www.mirka.com/products/5b-332-080")]
        record = _success_record(
            "https://www.mirka.com/products/5b-332-080",
            text="The Mirka 5B-332-080 is a 5-inch abrasive disc. "
                 "Mirka Abrasives Inc manufactures quality abrasives.",
            title="Mirka 5B-332-080 Abrasive Disc",
        )
        provider = FakeProvider(candidates)
        retriever = FakeRetriever([record])

        result = bootstrap_identity(
            product,
            [provider],
            retriever,
            part_manuf_input="Mirka Abrasives Inc",
        )

        assert result.success is True
        assert "Mirka" in result.manufacturer
        assert result.domain == "mirka.com"
        assert result.provenance is not None
        assert result.provenance.trust_status == "run_verified"
        assert result.provenance.source_url == "https://www.mirka.com/products/5b-332-080"
        assert len(result.bootstrap_evidence) == 1

    def test_resolves_wrong_domain_mode_b(self):
        product = _diablo_product()
        candidates = [_candidate("https://diablotools.com/products/dcb518asts06g")]
        record = _success_record(
            "https://diablotools.com/products/dcb518asts06g",
            text="The Diablo DCB518ASTS06G sanding belt. Freud Inc makes Diablo tools.",
            title="Diablo DCB518ASTS06G Sanding Belt",
        )
        provider = FakeProvider(candidates)
        retriever = FakeRetriever([record])

        result = bootstrap_identity(
            product,
            [provider],
            retriever,
            part_manuf_input="Freud Inc (2435)",
        )

        assert result.success is True
        assert result.domain == "diablotools.com"
        assert result.provenance is not None
        assert result.provenance.trust_status == "run_verified"

    def test_rejects_marketplace(self):
        product = _mirka_product()
        candidates = [
            _candidate("https://www.amazon.com/mirka-5b-332-080", title="5B-332-080 on Amazon"),
        ]
        provider = FakeProvider(candidates)
        retriever = FakeRetriever([])

        result = bootstrap_identity(
            product,
            [provider],
            retriever,
            part_manuf_input="Mirka Abrasives Inc",
        )

        assert result.success is False
        assert "filtered" in result.failure_reason.lower() or "marketplace" in result.failure_reason.lower()

    def test_rejects_sibling_product(self):
        product = _mirka_product()
        candidates = [_candidate("https://www.mirka.com/products/other-product")]
        record = _success_record(
            "https://www.mirka.com/products/other-product",
            text="The ABCD-1234 is a different abrasive product. "
                 "Mirka Abrasives Inc makes this product line.",
            title="Mirka ABCD-1234 Different Product",
        )
        provider = FakeProvider(candidates)
        retriever = FakeRetriever([record])

        result = bootstrap_identity(
            product,
            [provider],
            retriever,
            part_manuf_input="Mirka Abrasives Inc",
        )

        assert result.success is False
        assert "mpn" in result.failure_reason.lower() or "verification" in result.failure_reason.lower()

    def test_no_results_returns_failure(self):
        product = _mirka_product()
        provider = FakeProvider([])
        retriever = FakeRetriever([])

        with patch("app.sources.bootstrap._gemini_fallback_search", return_value=[]), \
             patch("app.sources.bootstrap._duckduckgo_fallback_search", return_value=[]):
            result = bootstrap_identity(
                product,
                [provider],
                retriever,
                part_manuf_input="Mirka Abrasives Inc",
            )

        assert result.success is False
        assert "no search results" in result.failure_reason.lower()

    def test_mpn_not_in_page_text(self):
        product = _mirka_product()
        candidates = [_candidate("https://www.mirka.com/products/some-page")]
        record = _success_record(
            "https://www.mirka.com/products/some-page",
            text="Mirka makes great abrasive products. No specific MPN mentioned here.",
            title="Mirka Abrasive Products",
        )
        provider = FakeProvider(candidates)
        retriever = FakeRetriever([record])

        result = bootstrap_identity(
            product,
            [provider],
            retriever,
            part_manuf_input="Mirka Abrasives Inc",
        )

        assert result.success is False
        assert "mpn" in result.failure_reason.lower() or "verification" in result.failure_reason.lower()

    def test_strong_evidence_required(self):
        product = _mirka_product()
        candidates = [_candidate("https://www.mirka.com/products/5b-332-080")]
        record = _success_record(
            "https://www.mirka.com/products/5b-332-080",
            text="A product called 5B-332-080 with no company info whatsoever.",
            title="5B-332-080",
        )
        provider = FakeProvider(candidates)
        retriever = FakeRetriever([record])

        result = bootstrap_identity(
            product,
            [provider],
            retriever,
            part_manuf_input="Mirka Abrasives Inc",
        )

        # Domain-based fallback extracts "Mirka" from mirka.com, matching input
        assert result.success is True

    def test_bootstrap_without_part_manuf(self):
        product = _no_mfr_product()
        candidates = [_candidate("https://www.example.com/products/test-1234-abcd")]
        record = _success_record(
            "https://www.example.com/products/test-1234-abcd",
            text="Acme Corp manufactures the TEST-1234-ABCD widget. "
                 "Acme Corp has been in business since 1950.",
            title="Acme Corp TEST-1234-ABCD Widget",
        )
        provider = FakeProvider(candidates)
        retriever = FakeRetriever([record])

        result = bootstrap_identity(
            product,
            [provider],
            retriever,
            part_manuf_input="",
        )

        assert result.success is True
        assert "Acme" in result.manufacturer
        assert result.domain == "example.com"

    def test_foreign_locale_manufacturer_domain(self):
        product = _mirka_product()
        candidates = [
            _candidate(
                "https://www.mirka.de/products/5b-332-080",
                title="Mirka 5B-332-080 Schleifscheibe",
            ),
        ]
        record = _success_record(
            "https://www.mirka.de/products/5b-332-080",
            text="Mirka GmbH stellt die 5B-332-080 Schleifscheibe her. "
                 "Mirka GmbH ist ein finnisches Unternehmen.",
            title="Mirka 5B-332-080 Schleifscheibe",
        )
        provider = FakeProvider(candidates)
        retriever = FakeRetriever([record])

        result = bootstrap_identity(
            product,
            [provider],
            retriever,
            part_manuf_input="Mirka Abrasives Inc",
        )

        assert result.success is True
        assert result.domain == "mirka.de"
        assert result.provenance is not None

    def test_ambiguous_manufacturer_conflict(self):
        product = _mirka_product()
        candidates = [_candidate("https://www.example.com/products/5b-332-080")]
        record = _success_record(
            "https://www.example.com/products/5b-332-080",
            text="The Bosch Power Tools Inc 5B-332-080 is an abrasive disc. "
                 "Bosch Power Tools Inc makes this product.",
            title="Bosch 5B-332-080 Abrasive Disc",
        )
        provider = FakeProvider(candidates)
        retriever = FakeRetriever([record])

        result = bootstrap_identity(
            product,
            [provider],
            retriever,
            part_manuf_input="Mirka Abrasives Inc",
        )

        assert result.success is False
        assert "mismatch" in result.failure_reason.lower()

    def test_provenance_recorded(self):
        product = _mirka_product()
        candidates = [_candidate("https://www.mirka.com/products/5b-332-080")]
        record = _success_record(
            "https://www.mirka.com/products/5b-332-080",
            text="Mirka Inc makes the 5B-332-080 abrasive disc.",
            title="Mirka 5B-332-080",
        )
        provider = FakeProvider(candidates)
        retriever = FakeRetriever([record])

        result = bootstrap_identity(
            product,
            [provider],
            retriever,
            part_manuf_input="Mirka Abrasives Inc",
        )

        assert result.success is True
        assert result.provenance is not None
        assert result.provenance.source_url == "https://www.mirka.com/products/5b-332-080"
        assert result.provenance.evidence_id != ""
        assert result.provenance.trust_status == "run_verified"
        assert "verified" in result.provenance.verification_reason.lower()

    def test_empty_mpn_returns_failure(self):
        product = ProductIdentity(mpn="", manufacturer="Test")
        result = bootstrap_identity(product, [], lambda c: _failed_record(c.url))
        assert result.success is False
        assert "no MPN" in result.failure_reason

    def test_provider_exception_handled(self):
        product = _mirka_product()

        class FailingProvider:
            name = "failing"
            kind = DiscoveryMethod.SEARCH
            def discover(self, product, context):
                raise RuntimeError("provider crashed")

        with patch("app.sources.bootstrap._gemini_fallback_search", return_value=[]), \
             patch("app.sources.bootstrap._duckduckgo_fallback_search", return_value=[]):
            result = bootstrap_identity(
                product,
                [FailingProvider()],
                lambda c: _failed_record(c.url),
                part_manuf_input="Mirka",
            )
        assert result.success is False
        assert "no search results" in result.failure_reason
