"""Offline tests for the extra REST endpoints (Step 9): product lookup,
dataset dashboard, batch enrichment, and delivery downloads.

The batch tests run the real pipeline shape (EnrichmentService) but fully
faked providers/retrieval/LLM via dependency overrides, write into a
tmp_path-managed batch directory, and persist to an in-memory SQLite
database. The real input dataset and the official delivery reference file
are read (they are part of the repo), nothing else touches the network.
"""

from __future__ import annotations

import csv
import json

import pytest
from fastapi.testclient import TestClient

from app.api.routes import batch as batch_module
from app.api.routes.batch import get_batch_factory
from app.core.domain import ProcessingStatus, SourceType
from app.extraction import ExtractionOutput, ExtractionOutputItem
from app.llm import LLMClient
from app.main import app
from app.pipeline.enrichment import EnrichmentService
from app.sources.candidates import DiscoveryMethod, SourceCandidate
from app.sources.retrieval import (
    EvidenceRecord,
    ExtractionStatus,
    RetrievalStatus,
)

PAGE = "https://www.acme.com/products/cordless-vacuum"
EVIDENCE = "ev-acme-page-0001"

DESCRIPTIONS_JSON = json.dumps(
    {
        "product_title": "XLC10ZW Cordless Vacuum",
        "short_description": "Cordless vacuum, bare tool.",
        "long_description": "An 18V cordless vacuum (bare tool).",
        "item_features": ["18V", "cordless", "bare tool"],
    }
)


class FakeProvider:
    name = "fake-search"
    kind = DiscoveryMethod.SEARCH

    def __init__(self, candidates: list[SourceCandidate]):
        self._candidates = candidates

    def discover(self, product, context):
        mpn = product.mpn if product and product.mpn else "XLC10ZW"
        mpn_url = f"https://www.acme.com/products/{mpn.lower()}"
        return [
            SourceCandidate(
                id=f"cand-{mpn_url}",
                url=mpn_url,
                source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
                title=f"Acme {mpn} Product Page",
            )
        ]


class FakeRetriever:
    def __init__(self, records: list[EvidenceRecord]):
        self.by_url = {record.url: record for record in records}

    def __call__(self, candidate: SourceCandidate) -> EvidenceRecord:
        text = f"Makita 18V cordless vacuum, bare tool. {candidate.title} {candidate.url}"
        return EvidenceRecord(
            evidence_id=EVIDENCE,
            source_candidate_id=candidate.id,
            url=candidate.url,
            source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
            title=candidate.title,
            text=text,
            content_type="text/html",
            retrieval_status=RetrievalStatus.SUCCESS,
            extraction_status=ExtractionStatus.EXTRACTED,
        )


class FakeLLMClient(LLMClient):
    provider = "fake"

    def _complete(self, prompt, **kwargs) -> str:
        if "PRODUCT IDENTITY" in prompt:
            return DESCRIPTIONS_JSON
        return json.dumps(
            ExtractionOutput(
                items=[
                    ExtractionOutputItem(
                        name="voltage",
                        raw_value="18V",
                        normalized_value="18V",
                        unit="V",
                        confidence=0.9,
                        evidence_ids=[EVIDENCE],
                    )
                ]
            ).model_dump()
        )


def _candidate(url: str) -> SourceCandidate:
    return SourceCandidate(
        id=f"cand-{url}",
        url=url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        title=url,
    )


def _success(url: str) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=EVIDENCE,
        source_candidate_id=f"cand-{url}",
        url=url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        title=url,
        text="Makita 18V cordless vacuum, bare tool.",
        content_type="text/html",
        retrieval_status=RetrievalStatus.SUCCESS,
        extraction_status=ExtractionStatus.EXTRACTED,
    )


def _failed(url: str) -> EvidenceRecord:
    return EvidenceRecord(
        source_candidate_id=f"cand-{url}",
        url=url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        retrieval_status=RetrievalStatus.FAILED,
        error_message="connection refused",
    )


def fake_service() -> EnrichmentService:
    return EnrichmentService(
        providers=[FakeProvider([_candidate(PAGE)])],
        manufacturer_domains=["acme.com"],
        retriever=FakeRetriever([_success(PAGE)]),
        llm_client=FakeLLMClient(),
    )


def fake_factory():
    def factory() -> EnrichmentService:
        return fake_service()

    return factory


@pytest.fixture()
def client_with_overrides(tmp_path, monkeypatch):
    # The per-test in-memory ``get_session`` override (which also covers the
    # dashboard module's re-exported ``get_session``) is provided automatically
    # by the autouse ``isolated_database`` fixture in tests/conftest.py, so the
    # dashboard and batch routes read/write an isolated in-memory SQLite DB.
    app.dependency_overrides[get_batch_factory] = fake_factory
    monkeypatch.setattr(batch_module, "BATCH_DIR", tmp_path / "batch")
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_batch_factory, None)


# --------------------------------------------------------------------------
# lookup
# --------------------------------------------------------------------------


class TestLookup:
    def test_lookup_finds_exact_mpn(self):
        # Seed a persistent record via the enrich endpoint, then look it up.
        TestClient(app).post(
            "/api/enrich",
            json={
                "Mfg_Part_Num": "XLC10ZW",
                "Part_Desc": "XLC10ZW Makita 18V Cordless Vacuum (Bare)",
                "E1_Brand": "-- Unbranded --",
                "Unilog_Brand": "-- No Unilog Brand --",
                "DIB_Brand": "-- No DIB Brand --",
                "Part_Manuf": "Makita Usa Inc (5142)",
            },
        )
        result = TestClient(app).get("/api/lookup", params={"mpn": "XLC10ZW"})
        assert result.status_code == 200
        data = result.json()
        assert data["query"] == "XLC10ZW"
        assert data["source"] == "database"
        assert data["total_matches"] == 1
        record = data["stored_records"][0]
        assert record["part_number"] == "XLC10ZW"

    def test_lookup_is_case_insensitive(self):
        TestClient(app).post(
            "/api/enrich",
            json={
                "Mfg_Part_Num": "XLC10ZW",
                "Part_Desc": "XLC10ZW Makita 18V Cordless Vacuum (Bare)",
                "E1_Brand": "-- Unbranded --",
                "Unilog_Brand": "-- No Unilog Brand --",
                "DIB_Brand": "-- No DIB Brand --",
                "Part_Manuf": "Makita Usa Inc (5142)",
            },
        )
        result = TestClient(app).get("/api/lookup", params={"mpn": "xlc10zw"})
        assert result.status_code == 200
        assert result.json()["total_matches"] == 1

    def test_lookup_unknown_mpn_returns_empty(self):
        result = TestClient(app).get("/api/lookup", params={"mpn": "ZZZZ-NOPE"})
        assert result.status_code == 200
        data = result.json()
        assert data["source"] == "none"
        assert data["total_matches"] == 0
        assert data["stored_records"] == []

    def test_lookup_requires_mpn(self):
        assert TestClient(app).get("/api/lookup").status_code == 422


# --------------------------------------------------------------------------
# dashboard
# --------------------------------------------------------------------------


class TestDashboard:
    def test_dashboard_database_stats_present(self, client_with_overrides):
        client = client_with_overrides
        result = client.get("/api/dashboard")
        assert result.status_code == 200
        data = result.json()
        assert "database" in data
        assert isinstance(data["database"]["total_records"], int)
        assert isinstance(data["database"]["by_status"], dict)
        assert data["last_batch_run"] is None

    def test_dashboard_shows_last_run_after_batch(
        self, client_with_overrides
    ):
        client = client_with_overrides
        run = client.post(
            "/api/batch",
            json={
                "rows": [
                    {"Mfg_Part_Num": "XLC10ZW"},
                    {"Mfg_Part_Num": "DCB518ASTS06G"},
                ]
            },
        )
        assert run.status_code == 200

        data = client.get("/api/dashboard").json()
        summary = data["last_batch_run"]
        assert summary is not None
        assert summary["status"] == "completed"
        assert summary["record_count"] == 2
        assert summary["status_counts"] == {"completed": 2}


# --------------------------------------------------------------------------
# batch + downloads
# --------------------------------------------------------------------------


class TestBatch:
    def test_batch_by_mpns_writes_csv_and_persists(self, client_with_overrides, tmp_path):
        client = client_with_overrides
        response = client.post(
            "/api/batch",
            json={
                "rows": [
                    {"Mfg_Part_Num": "XLC10ZW"},
                    {"Mfg_Part_Num": "DCB518ASTS06G"},
                ],
                "mpns": ["XLC10ZW", "DCB518ASTS06G"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["requested"] == 2
        assert data["processed"] == 2
        assert data["status_counts"] == {"completed": 2}
        assert data["job_id"] is not None
        assert all(row["delivery_columns"] == 252 for row in data["rows"])

        target = tmp_path / "batch" / data["delivery_file"]
        assert target.is_file()
        with target.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        assert len(rows) == 3  # header + 2 rows
        assert len(rows[1]) == 252

    def test_batch_by_start_limit(self, client_with_overrides, tmp_path):
        client = client_with_overrides
        response = client.post(
            "/api/batch",
            json={
                "rows": [
                    {"Mfg_Part_Num": "A"},
                    {"Mfg_Part_Num": "B"},
                    {"Mfg_Part_Num": "C"},
                ],
                "start": 0,
                "limit": 2,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["processed"] == 2
        assert data["rows"][0]["row_id"] == 1

    def test_batch_empty_selection_is_safe(
        self, client_with_overrides, tmp_path
    ):
        client = client_with_overrides
        response = client.post(
            "/api/batch",
            json={
                "rows": [{"Mfg_Part_Num": "NOT-IN-SELECTION"}],
                "mpns": ["NOT-IN-DATASET"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["processed"] == 0
        assert data["rows"] == []
        assert data["status_counts"] == {}

    def test_download_serves_batch_csv(self, client_with_overrides, tmp_path):
        client = client_with_overrides
        data = client.post(
            "/api/batch",
            json={"rows": [{"Mfg_Part_Num": "XLC10ZW"}]},
        ).json()

        response = client.get(data["download_url"])
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        body = response.content.decode("utf-8-sig")
        assert "Mfg_Part_Num" in body

    def test_download_refuses_path_traversal(self, client_with_overrides):
        client = client_with_overrides
        for name in ("..\\..\\unihack.db", "..%2F..%2Funihack.db", "x.csv"):
            assert client.get(f"/api/downloads/{name}").status_code == 404

    def test_download_missing_file_404(self, client_with_overrides):
        client = client_with_overrides
        assert client.get("/api/downloads/nope.csv").status_code == 404

    def test_download_refuses_absolute_windows_path(self, client_with_overrides):
        client = client_with_overrides
        # The path param decodes to C:\Windows\win.ini; it must never resolve
        # outside the managed batch directory.
        assert (
            client.get("/api/downloads/C:%5CWindows%5Cwin.ini").status_code
            == 404
        )

    def test_download_refuses_symlink_escape(self, client_with_overrides, tmp_path):
        client = client_with_overrides
        outside = tmp_path / "outside-secret.txt"
        outside.write_text("secret")
        link = tmp_path / "batch" / "evil-link.csv"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlinks unavailable: {exc}")
        assert client.get("/api/downloads/evil-link.csv").status_code == 404