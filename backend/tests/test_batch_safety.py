"""Offline tests for batch guardrails and security hardening (Step 9B).

Hard limit enforcement (HTTP 422, no silent truncation), row-level failure
isolation (blank delivery row, sanitized review reason, remaining rows keep
processing), collision-free CSV filenames, CSV-before-commit cleanup, payload
evidence-text capping, and secret non-leakage in responses and persisted
payloads. The caller supplies its own rows (no dataset dependency): fake
everything, in-memory SQLite, tmp_path batch directory.
"""

from __future__ import annotations

import csv
import json
from typing import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import batch as batch_module
from app.api.routes.batch import get_batch_factory
from app.api.routes.enrich import get_enrichment_service
from app.config import settings
from app.core.domain import ProcessingStatus, SourceType
from app.db.database import Base, get_session
from app.db.models import ProductRecordModel
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

PAGE = "https://www.acme.com/products/xlc10zw"
EVIDENCE = "ev-acme-page-0001"

SENTINEL_LLM = "sentinel-llm-key-9b"
SENTINEL_SEARCH = "sentinel-search-key-9b"

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
        return list(self._candidates)


class FakeRetriever:
    def __init__(self, records: list[EvidenceRecord]):
        self.by_url = {record.url: record for record in records}

    def __call__(self, candidate: SourceCandidate) -> EvidenceRecord:
        return self.by_url.get(candidate.url, _failed(candidate.url))


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
                        raw_value="18 V",
                        normalized_value="18 V",
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


def _success(url: str, text: str = "XLC10ZW Makita 18V cordless vacuum.") -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=EVIDENCE,
        source_candidate_id=f"cand-{url}",
        url=url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        title=url,
        text=text,
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


def rows(*mpns: str) -> list[dict]:
    """Build a batch request body's ``rows`` from a list of MPNs."""
    return [{"Mfg_Part_Num": mpn} for mpn in mpns]


class FakeFactory:
    """Override value for get_batch_factory: returns the real factory."""

    def __init__(self, failing_mpns: set[str] | None = None, always: bool = False):
        self.failing = failing_mpns or set()
        self.always = always

    def __call__(self) -> Callable[[], EnrichmentService]:
        failing = self.failing
        always = self.always

        def build() -> EnrichmentService:
            inner = fake_service()
            if always or failing:
                return FlakyService(inner, failing=failing, always=always)
            return inner

        return build


class FlakyService:
    """Wraps an EnrichmentService and raises for chosen MPNs."""

    def __init__(self, inner: EnrichmentService, failing: set[str], always: bool = False):
        self._inner = inner
        self._failing = failing
        self._always = always

    def run(self, request, **kwargs):
        if self._always or request.Mfg_Part_Num in self._failing:
            raise RuntimeError("row processing exploded")
        return self._inner.run(request)


class ControlledSession(SaSession):
    fail_commit = False

    def commit(self):
        if ControlledSession.fail_commit:
            raise RuntimeError("database down")
        return super().commit()


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    """(TestClient, Session factory, batch dir) with all fakes wired in."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(
        bind=engine, class_=ControlledSession, autoflush=False, autocommit=False
    )

    def override_get_session():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    def factory() -> Callable[[], EnrichmentService]:
        def build() -> EnrichmentService:
            return fake_service()

        return build

    batch_dir = tmp_path / "batch"
    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_batch_factory] = factory
    app.dependency_overrides[get_enrichment_service] = lambda: fake_service()
    monkeypatch.setattr(batch_module, "BATCH_DIR", batch_dir)
    try:
        yield TestClient(app, raise_server_exceptions=False), Session, batch_dir
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# hard limits
# --------------------------------------------------------------------------


class TestBatchLimits:
    def test_limit_above_max_rejected(self, ctx, monkeypatch):
        client, _, _ = ctx
        monkeypatch.setattr(settings, "batch_max_rows", 100)
        assert (
            client.post(
                "/api/batch",
                json={"rows": rows("A", "B", "C"), "start": 0, "limit": 101},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/batch", json={"rows": rows("A", "B", "C"), "start": 0, "limit": 0}
            ).status_code
            == 200
        )

    def test_unbounded_request_rejected(self, ctx):
        client, _, _ = ctx
        assert client.post("/api/batch", json={}).status_code == 422
        assert client.post("/api/batch", json={"start": 5}).status_code == 422

    def test_limit_at_and_below_max_accepted(self, ctx, monkeypatch):
        client, _, _ = ctx
        monkeypatch.setattr(settings, "batch_max_rows", 100)
        assert (
            client.post(
                "/api/batch",
                json={"rows": rows("A", "B", "C"), "start": 0, "limit": 3},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/batch",
                json={
                    "rows": rows("1", "2", "3", "4", "5", "6", "7"),
                    "start": 5,
                    "limit": 2,
                },
            ).status_code
            == 200
        )

    def test_oversized_mpns_rejected(self, ctx, monkeypatch):
        client, _, _ = ctx
        monkeypatch.setattr(settings, "batch_max_rows", 3)
        assert (
            client.post(
                "/api/batch",
                json={"rows": rows("A"), "mpns": ["A", "B", "C", "D"]},
            ).status_code
            == 422
        )
        # whitespace-only entries are stripped before counting
        assert (
            client.post(
                "/api/batch",
                json={"rows": rows("X"), "mpns": ["A", " ", "B", "", "C"]},
            ).status_code
            == 200
        )

    def test_negative_start_rejected(self, ctx):
        client, _, _ = ctx
        assert (
            client.post(
                "/api/batch", json={"rows": rows("A", "B"), "start": -1, "limit": 2}
            ).status_code
            == 422
        )

    def test_empty_rows_rejected(self, ctx):
        client, _, _ = ctx
        assert client.post("/api/batch", json={"rows": []}).status_code == 422


# --------------------------------------------------------------------------
# row isolation
# --------------------------------------------------------------------------


class TestRowIsolation:
    def test_failed_row_is_isolated(self, ctx, tmp_path):
        client, Session, batch_dir = ctx
        app.dependency_overrides[get_batch_factory] = FakeFactory(
            failing_mpns={"DCB518ASTS06G"}
        )
        response = client.post(
            "/api/batch",
            json={"rows": rows("XLC10ZW", "DCB518ASTS06G")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status_counts"] == {"completed": 1, "failed": 1}
        assert len(data["rows"]) == 2
        # XLC10ZW is first (completed); DCB518ASTS06G is second (failed).
        completed_row = data["rows"][0]
        failed_row = data["rows"][1]
        assert failed_row["processing_status"] == "failed"
        assert failed_row["review_reasons"] == [
            "unexpected row failure: RuntimeError"
        ]
        assert completed_row["processing_status"] == "completed"

        # Mixed outcome -> the job needs review; the failed row is a blank,
        # exactly-252-cell CSV row so the file keeps 1:1 row alignment.
        summary = client.get("/api/dashboard").json()["last_batch_run"]
        assert summary["status"] == "needs_review"
        assert summary["status_counts"] == {"completed": 1, "failed": 1}
        csv_path = next(batch_dir.glob("*.csv"))
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            csv_rows = list(csv.reader(handle))
        assert len(csv_rows) == 3  # header + 2 rows
        assert any(cell for cell in csv_rows[1])  # first data row is populated
        assert all(cell == "" for cell in csv_rows[2])  # second data row blank

        # The persisted record carries a sanitized error payload - never the
        # exception message, stack trace or any secret.
        with Session() as session:
            record = (
                session.query(ProductRecordModel)
                .filter(ProductRecordModel.part_number == "DCB518ASTS06G")
                .one()
            )
            assert record.status == "failed"
            payload = json.loads(record.payload)
            assert payload["row_error"]["type"] == "RuntimeError"
            assert "exploded" not in record.payload
            completed = (
                session.query(ProductRecordModel)
                .filter(ProductRecordModel.part_number == "XLC10ZW")
                .one()
            )
            assert completed.status == "completed"

    def test_all_failed_job_status(self, ctx):
        client, _, _ = ctx
        app.dependency_overrides[get_batch_factory] = FakeFactory(always=True)
        response = client.post("/api/batch", json={"rows": rows("XLC10ZW")})
        assert response.status_code == 200
        data = response.json()
        assert data["status_counts"] == {"failed": 1}
        assert data["rows"][0]["review_reasons"] == [
            "unexpected row failure: RuntimeError"
        ]
        summary = client.get("/api/dashboard").json()["last_batch_run"]
        assert summary["status"] == "failed"

    def test_transient_service_error_does_not_abort_run(self, ctx):
        client, _, _ = ctx
        calls = {"n": 0}

        def flaky_factory() -> Callable[[], EnrichmentService]:
            calls["n"] += 1
            failing = {"XLC10ZW"} if calls["n"] == 1 else set()

            def build() -> EnrichmentService:
                return FlakyService(fake_service(), failing=failing)

            return build

        app.dependency_overrides[get_batch_factory] = flaky_factory
        # First call: XLC10ZW fails -> needs_review; second call runs clean.
        first = client.post("/api/batch", json={"rows": rows("XLC10ZW")}).json()
        assert first["status_counts"] == {"failed": 1}
        second = client.post("/api/batch", json={"rows": rows("XLC10ZW")}).json()
        assert second["status_counts"] == {"completed": 1}
        assert client.get("/api/dashboard").json()["last_batch_run"][
            "status_counts"
        ] == {"completed": 1}


# --------------------------------------------------------------------------
# filenames + commit cleanup
# --------------------------------------------------------------------------


class TestFilenamesAndCommit:
    def test_filenames_are_collision_free(self, ctx):
        client, _, _ = ctx
        first = client.post("/api/batch", json={"rows": rows("XLC10ZW")}).json()
        second = client.post("/api/batch", json={"rows": rows("XLC10ZW")}).json()
        assert first["delivery_file"] != second["delivery_file"]
        assert first["delivery_file"].startswith("batch-")
        stem = first["delivery_file"].rsplit(".", 1)[0]
        assert all(ch.isalnum() or ch in "-_" for ch in stem)

    def test_commit_failure_writes_no_orphan_csv(self, ctx):
        client, Session, batch_dir = ctx
        ControlledSession.fail_commit = True
        try:
            response = client.post("/api/batch", json={"rows": rows("XLC10ZW")})
            assert response.status_code == 500
        finally:
            ControlledSession.fail_commit = False
        assert list(batch_dir.glob("*.csv")) == []
        assert client.get("/api/dashboard").json()["last_batch_run"] is None


# --------------------------------------------------------------------------
# payload safety
# --------------------------------------------------------------------------


class TestPayloadSafety:
    def test_payload_evidence_text_is_capped(self, ctx, monkeypatch):
        client, Session, batch_dir = ctx
        monkeypatch.setattr(settings, "batch_payload_evidence_cap_chars", 40)
        long_text = "x" * 500
        records = [_success(PAGE, text=long_text)]
        app.dependency_overrides[get_batch_factory] = lambda: (
            lambda: EnrichmentService(
                providers=[FakeProvider([_candidate(PAGE)])],
                manufacturer_domains=["acme.com"],
                retriever=FakeRetriever(records),
                llm_client=FakeLLMClient(),
            )
        )
        response = client.post("/api/batch", json={"rows": rows("XLC10ZW")})
        assert response.status_code == 200
        with Session() as session:
            record = (
                session.query(ProductRecordModel)
                .filter(ProductRecordModel.part_number == "XLC10ZW")
                .one()
            )
            payload = json.loads(record.payload)
            evidence = payload["evidence"][0]
            assert evidence["url"] == PAGE
            assert evidence["evidence_id"] == EVIDENCE
            assert "[truncated by persistence]" in evidence["text"]
            assert evidence["text"].startswith("x" * 40)

    def test_secrets_never_leak_in_responses_or_payloads(self, ctx, monkeypatch):
        client, Session, _ = ctx
        monkeypatch.setattr(settings, "llm_api_key", SENTINEL_LLM)
        monkeypatch.setattr(settings, "search_provider_api_key", SENTINEL_SEARCH)

        batch = client.post("/api/batch", json={"rows": rows("XLC10ZW")})
        assert batch.status_code == 200
        assert SENTINEL_LLM not in batch.text
        assert SENTINEL_SEARCH not in batch.text

        enrich = client.post(
            "/api/enrich",
            json={
                "Mfg_Part_Num": "XLC10ZW",
                "Part_Desc": "XLC10ZW Makita 18V Cordless Vacuum (Bare)",
                "E1_Brand": "Makita",
                "Unilog_Brand": "",
                "DIB_Brand": "",
                "Part_Manuf": "Makita Usa Inc (5142)",
            },
        )
        assert enrich.status_code == 200
        assert SENTINEL_LLM not in enrich.text
        assert SENTINEL_SEARCH not in enrich.text

        with Session() as session:
            for record in session.query(ProductRecordModel).all():
                assert SENTINEL_LLM not in record.payload
                assert SENTINEL_SEARCH not in record.payload
