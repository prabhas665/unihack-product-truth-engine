"""Offline tests for Step 10B persistent product intelligence.

Covers: freshness verdicts, save/load round-trips, DB-first lookup,
retrieve_from_db, persistence failure, migration idempotence, legacy upgrade,
and secret-free storage. All fake/mocked — no network calls or real LLM
credentials.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import settings
from app.core.domain import ProcessingMetadata, ProcessingStatus, SourceType
from app.extraction import ExtractionOutput, ExtractionOutputItem
from app.api.routes.enrich import get_enrichment_service
from app.db.database import Base
from app.db.models import Job, ProductRecordModel
from app.db.migration import migrate_product_records, run_migrations
from app.db.repository import (
    FreshnessVerdict,
    ProductRepository,
    build_enrichment_from_payload,
)
from app.llm import LLMClient
from app.main import app
from app.pipeline.enrichment import (
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentService,
    InputRowView,
)
from app.sources.candidates import DiscoveryMethod, SourceCandidate
from app.sources.retrieval import (
    EvidenceRecord,
    ExtractionStatus,
    RetrievalStatus,
)

PAGE = "https://www.acme.com/products/dcb518asts06g"
EVIDENCE_A = "ev-acme-page-0001"

DESCRIPTIONS_JSON = json.dumps(
    {
        "product_title": "DCB518ASTS06G Sanding Belt 6-Pack",
        "short_description": "Six-pack of 1/2 x 18 inch sanding belts.",
        "mobile_description": "Diablo 1/2x18 in sanding belt, 6 pack.",
        "invoice_description": "Sanding belt 1/2x18 in, pack of 6.",
        "long_description": (
            "A six-pack of Diablo sanding belts, each 1/2 inch wide and 18 "
            "inches long, for belt sanders."
        ),
        "retail_description": "Diablo 1/2 in x 18 in sanding belt, 6 pack.",
        "marketing_description": "Professional sanding belts in a 6-pack.",
        "item_features": ["1/2 inch width", "18 inch length", "pack of 6"],
        "with": "Six sanding belts",
        "application": "Belt sanders",
        "includes": "6 sanding belts",
        "product_name": "Sanding Belt",
    }
)


# --------------------------------------------------------------------------
# fakes (same pattern as test_enrichment.py / test_api_extras.py)
# --------------------------------------------------------------------------


class FakeProvider:
    name = "fake-search"
    kind = DiscoveryMethod.SEARCH

    def __init__(self, candidates=None):
        self._candidates = candidates or []

    def discover(self, product, context):
        return list(self._candidates)


class FakeRetriever:
    def __init__(self, records):
        self.by_url = {r.url: r for r in records}

    def __call__(self, candidate):
        return self.by_url.get(candidate.url, _failed(candidate.url))


class FakeLLMClient(LLMClient):
    provider = "fake"

    def _complete(self, prompt, **kw):
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
                        evidence_ids=[EVIDENCE_A],
                    )
                ]
            ).model_dump()
        )


def _candidate(url):
    return SourceCandidate(
        id=f"cand-{url}",
        url=url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        title=url,
    )


def _success(url, evidence_id=EVIDENCE_A):
    return EvidenceRecord(
        evidence_id=evidence_id,
        source_candidate_id=f"cand-{url}",
        url=url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        title=url,
        text="DCB518ASTS06G Sanding Belt 18V cordless.",
        content_type="text/html",
        retrieval_status=RetrievalStatus.SUCCESS,
        extraction_status=ExtractionStatus.EXTRACTED,
    )


def _failed(url):
    return EvidenceRecord(
        source_candidate_id=f"cand-{url}",
        url=url,
        source_type=SourceType.MANUFACTURER_PRODUCT_PAGE,
        retrieval_status=RetrievalStatus.FAILED,
        error_message="connection refused",
    )


def fake_service():
    return EnrichmentService(
        providers=[FakeProvider([_candidate(PAGE)])],
        manufacturer_domains=["acme.com"],
        retriever=FakeRetriever([_success(PAGE)]),
        llm_client=FakeLLMClient(),
    )


def default_request(**overrides):
    payload = {
        "Mfg_Part_Num": "DCB518ASTS06G",
        "Part_Desc": 'DCB518ASTS06G Diablo 1/2"x18" - Sanding Belt 6pc',
        "E1_Brand": "-- Unbranded --",
        "Unilog_Brand": "-- No Unilog Brand --",
        "DIB_Brand": "-- No DIB Brand --",
        "Part_Manuf": "Freud Inc (2435)",
    }
    payload.update(overrides)
    return EnrichmentRequest(**payload)


# --------------------------------------------------------------------------
# fixtures (db_engine / session_factory / db_session are provided by
# tests/conftest.py so every test shares one isolated in-memory SQLite DB)
# --------------------------------------------------------------------------


def _make_result(**overrides):
    """Build a minimal valid EnrichmentResult for persistence tests."""
    from app.core.domain import ProductIdentity, ProductIntelligence
    from app.sources.discovery import DiscoveryResult

    req = default_request()
    input_row = InputRowView.from_row(req.to_input_row())
    processing = overrides.pop("processing", None)
    if processing is None:
        processing = ProcessingMetadata(
            status=ProcessingStatus.COMPLETED,
            created_at=datetime.utcnow().isoformat(),
            updated_at=datetime.utcnow().isoformat(),
        )
    stages = overrides.pop("stages", [])
    if not stages:
        from app.pipeline.enrichment import StageName, StageState, StageStatus
        stages = [StageState(stage=s, status=StageStatus.COMPLETED) for s in StageName]
    discovery = overrides.pop("discovery", None)
    if discovery is None:
        discovery = DiscoveryResult(
            product=ProductIdentity(
                manufacturer="Freud",
                brand="Diablo",
                mpn="DCB518ASTS06G",
                raw_description='DCB518ASTS06G Diablo 1/2"x18" - Sanding Belt 6pc',
            )
        )
    return EnrichmentResult(
        request=req,
        input_row=input_row,
        processing=processing,
        stages=stages,
        discovery=discovery,
        product=overrides.pop("product", None)
        or ProductIntelligence(identity=discovery.product, processing=processing),
        delivery=overrides.pop("delivery", None) or _default_delivery(),
        quality=overrides.pop("quality", None) or _default_quality(),
        **overrides,
    )


def _default_delivery():
    from app.pipeline.enrichment import DeliveryRowView
    return DeliveryRowView(
        headers=["Mfg_Part_Num", "PART_NUMBER"],
        values=["DCB518ASTS06G", "DCB518ASTS06G"],
        notes=[],
        column_count=2,
    )


def _make_result_for(mpn: str, part_manuf: str, manufacturer: str):
    """Build a minimal valid EnrichmentResult for a specific product."""
    from app.core.domain import ProductIdentity, ProductIntelligence
    from app.pipeline.enrichment import (
        DeliveryRowView,
        StageName,
        StageState,
        StageStatus,
    )
    from app.sources.discovery import DiscoveryResult

    req = default_request(Mfg_Part_Num=mpn, Part_Manuf=part_manuf)
    input_row = InputRowView.from_row(req.to_input_row())
    processing = ProcessingMetadata(
        status=ProcessingStatus.COMPLETED,
        created_at=datetime.utcnow().isoformat(),
        updated_at=datetime.utcnow().isoformat(),
    )
    stages = [StageState(stage=s, status=StageStatus.COMPLETED) for s in StageName]
    discovery = DiscoveryResult(
        product=ProductIdentity(
            manufacturer=manufacturer,
            brand=manufacturer,
            mpn=mpn,
        )
    )
    return EnrichmentResult(
        request=req,
        input_row=input_row,
        processing=processing,
        stages=stages,
        discovery=discovery,
        product=ProductIntelligence(identity=discovery.product, processing=processing),
        delivery=DeliveryRowView(
            headers=["Mfg_Part_Num", "PART_NUMBER"],
            values=[mpn, mpn],
            notes=[],
            column_count=2,
        ),
        quality=_default_quality(),
    )


def _default_quality():
    from app.core.domain import QualityScore, ConfidenceSummary
    return QualityScore(
        overall=0.0,
        evidence_coverage=0.0,
        validation_coverage=0.0,
        confidence=ConfidenceSummary(count=0, min=0, max=0, mean=0),
    )


def _seed_record(session, *, mpn="DCB518ASTS06G", status="completed",
                 last_enriched_at=None, payload_json=None):
    """Insert a ProductRecordModel directly for testing."""
    job = Job(kind="enrich", status=status)
    session.add(job)
    session.flush()
    record = ProductRecordModel(
        job_id=job.id,
        manufacturer="Freud",
        brand="Diablo",
        part_number=mpn,
        description="Sanding Belt",
        status=status,
        quality_score=85.0,
        payload=payload_json or "{}",
    )
    session.add(record)
    session.flush()
    if last_enriched_at is not None:
        record.last_enriched_at = last_enriched_at
        session.flush()
    session.commit()
    return record


# --------------------------------------------------------------------------
# 1. Freshness verdict
# --------------------------------------------------------------------------


class TestFreshnessVerdict:
    def test_fresh_record_within_window(self, db_session):
        repo = ProductRepository()
        record = _seed_record(db_session, last_enriched_at=datetime.utcnow())
        result, verdict = repo.find_fresh_by_mpn(
            db_session, "DCB518ASTS06G", freshness_days=30
        )
        assert verdict == FreshnessVerdict.FRESH
        assert result.id == record.id

    def test_stale_record_outside_window(self, db_session):
        repo = ProductRepository()
        old = datetime.utcnow() - timedelta(days=60)
        record = _seed_record(db_session, last_enriched_at=old)
        result, verdict = repo.find_fresh_by_mpn(
            db_session, "DCB518ASTS06G", freshness_days=30
        )
        assert verdict == FreshnessVerdict.STALE
        assert result.id == record.id

    def test_stale_when_null_timestamp(self, db_session):
        repo = ProductRepository()
        record = _seed_record(db_session, last_enriched_at=None)
        result, verdict = repo.find_fresh_by_mpn(
            db_session, "DCB518ASTS06G", freshness_days=30
        )
        assert verdict == FreshnessVerdict.STALE

    def test_not_found(self, db_session):
        repo = ProductRepository()
        result, verdict = repo.find_fresh_by_mpn(
            db_session, "NOPE-MPN", freshness_days=30
        )
        assert verdict == FreshnessVerdict.NOT_FOUND
        assert result is None

    def test_freshness_zero_days_always_stale(self, db_session):
        repo = ProductRepository()
        # Seed strictly in the past so the verdict is independent of clock
        # granularity (this machine can return identical timestamps across
        # successive calls); a non-positive freshness window also short-circuits
        # to STALE in the repository (config: "0 disables freshness").
        _seed_record(db_session, last_enriched_at=datetime.utcnow() - timedelta(seconds=1))
        result, verdict = repo.find_fresh_by_mpn(
            db_session, "DCB518ASTS06G", freshness_days=0
        )
        assert verdict == FreshnessVerdict.STALE

    def test_failed_status_excluded_from_fresh(self, db_session):
        repo = ProductRepository()
        # Seed a FAILED record with a recent timestamp
        failed = _seed_record(
            db_session, status="failed", last_enriched_at=datetime.utcnow()
        )
        # Seed a SUCCESSFUL record that is older but still within window
        success = _seed_record(
            db_session, status="completed",
            last_enriched_at=datetime.utcnow() - timedelta(days=5),
        )
        result, verdict = repo.find_fresh_by_mpn(
            db_session, "DCB518ASTS06G", freshness_days=30
        )
        # Successful record is preferred over the more-recent failed one
        assert verdict == FreshnessVerdict.FRESH
        assert result.id == success.id

    def test_most_recent_successful_selected(self, db_session):
        repo = ProductRepository()
        old_success = _seed_record(
            db_session, last_enriched_at=datetime.utcnow() - timedelta(days=5)
        )
        new_success = _seed_record(
            db_session, last_enriched_at=datetime.utcnow() - timedelta(days=1)
        )
        result, verdict = repo.find_fresh_by_mpn(
            db_session, "DCB518ASTS06G", freshness_days=30
        )
        assert verdict == FreshnessVerdict.FRESH
        assert result.id == new_success.id

    def test_find_fresh_records_multiple(self, db_session):
        repo = ProductRepository()
        _seed_record(db_session, last_enriched_at=datetime.utcnow())
        _seed_record(db_session, last_enriched_at=datetime.utcnow())
        records = repo.find_fresh_records_by_mpn(
            db_session, "DCB518ASTS06G", freshness_days=30
        )
        assert len(records) == 2

    def test_find_fresh_records_excludes_stale(self, db_session):
        repo = ProductRepository()
        old = _seed_record(
            db_session, last_enriched_at=datetime.utcnow() - timedelta(days=60)
        )
        records = repo.find_fresh_records_by_mpn(
            db_session, "DCB518ASTS06G", freshness_days=30
        )
        assert len(records) == 0

    def test_find_by_mpn_returns_all(self, db_session):
        repo = ProductRepository()
        _seed_record(db_session, last_enriched_at=datetime.utcnow())
        old = _seed_record(
            db_session, last_enriched_at=datetime.utcnow() - timedelta(days=60)
        )
        records = repo.find_by_mpn(db_session, "DCB518ASTS06G")
        assert len(records) == 2

    def test_mpn_case_insensitive(self, db_session):
        repo = ProductRepository()
        _seed_record(db_session, mpn="MyMPN", last_enriched_at=datetime.utcnow())
        result, verdict = repo.find_fresh_by_mpn(
            db_session, "mympn", freshness_days=30
        )
        assert verdict == FreshnessVerdict.FRESH


# --------------------------------------------------------------------------
# 2. Save -> load round trip
# --------------------------------------------------------------------------


class TestSaveLoadRoundTrip:
    def test_round_trip_preserves_result(self, db_session):
        repo = ProductRepository()
        result = _make_result()
        job = Job(kind="enrich", status="completed")
        db_session.add(job)
        db_session.flush()

        record = repo.save_enrichment(
            db_session, result, job_id=job.id, run_id="run-1"
        )
        db_session.flush()
        record_id = record.id

        loaded = repo.get_product_by_record_id(db_session, record_id)
        assert loaded is not None
        assert loaded.part_number == "DCB518ASTS06G"
        assert loaded.status == "completed"
        assert loaded.last_enriched_at is not None
        assert loaded.source_freshness_days == settings.product_cache_freshness_days

        payload_dict = json.loads(loaded.payload or "{}")
        rebuilt = build_enrichment_from_payload(payload_dict)
        assert rebuilt.processing.status == ProcessingStatus.COMPLETED
        assert rebuilt.input_row.mfg_part_num_value == "DCB518ASTS06G"

    def test_enrichment_history_appended(self, db_session):
        repo = ProductRepository()
        result = _make_result()
        job = Job(kind="enrich", status="completed")
        db_session.add(job)
        db_session.flush()

        repo.save_enrichment(db_session, result, job_id=job.id, run_id="r1")
        db_session.flush()

        loaded = repo.get_product_by_record_id(db_session, 1)
        history = json.loads(loaded.enrichment_history_json or "[]")
        assert len(history) == 1
        assert history[0]["run_id"] == "r1"
        assert history[0]["status"] == "completed"


# --------------------------------------------------------------------------
# 3. Fresh DB record -> /api/lookup returns source="database", stale=false
# --------------------------------------------------------------------------


class TestLookupFresh:
    def test_fresh_lookup_returns_database_source(self, db_engine, session_factory):
        # Seed via the same factory that the override will use
        seed = session_factory()
        try:
            repo = ProductRepository()
            result = _make_result()
            job = Job(kind="enrich", status="completed")
            seed.add(job)
            seed.flush()
            repo.save_enrichment(seed, result, job_id=job.id, run_id="r1")
            seed.commit()
        finally:
            seed.close()

        resp = TestClient(app).get(
            "/api/lookup", params={"mpn": "DCB518ASTS06G"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "database"
        assert data["stale"] is False
        assert len(data["stored_records"]) >= 1
        assert data["rows"] == []

    def test_case_insensitive_lookup(self, db_engine, session_factory):
        seed = session_factory()
        try:
            repo = ProductRepository()
            result = _make_result()
            job = Job(kind="enrich", status="completed")
            seed.add(job)
            seed.flush()
            repo.save_enrichment(seed, result, job_id=job.id, run_id="r1")
            seed.commit()
        finally:
            seed.close()

        resp = TestClient(app).get(
            "/api/lookup", params={"mpn": "dcb518asts06g"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "database"
        assert data["stale"] is False


# --------------------------------------------------------------------------
# 4. Stale DB record -> /api/lookup returns source="database", stale=true
# --------------------------------------------------------------------------


class TestLookupStale:
    def test_stale_lookup_returns_stale_flag(self, db_engine, session_factory):
        seed = session_factory()
        try:
            repo = ProductRepository()
            result = _make_result()
            job = Job(kind="enrich", status="completed")
            seed.add(job)
            seed.flush()
            record = repo.save_enrichment(
                seed, result, job_id=job.id, run_id="r1"
            )
            old = datetime.utcnow() - timedelta(days=60)
            record.last_enriched_at = old
            seed.commit()
        finally:
            seed.close()

        resp = TestClient(app).get(
            "/api/lookup", params={"mpn": "DCB518ASTS06G"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "database"
        assert data["stale"] is True
        assert len(data["stored_records"]) >= 1


# --------------------------------------------------------------------------
# 5. No dataset fallback: lookup only consults the persistent store
# --------------------------------------------------------------------------


class TestLookupFallback:
    def test_unknown_mpn_empty_db(self):
        resp = TestClient(app).get(
            "/api/lookup", params={"mpn": "ZZZZ-NOPE"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "none"
        assert data["stale"] is False
        assert data["total_matches"] == 0
        assert data["stored_records"] == []

    def test_known_mpn_without_record_is_none(self):
        resp = TestClient(app).get(
            "/api/lookup", params={"mpn": "XLC10ZW"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "none"
        assert data["stale"] is False
        assert data["total_matches"] == 0


# --------------------------------------------------------------------------
# 6 & 7 & 8. /api/enrich with retrieve_from_db
# --------------------------------------------------------------------------


class TestEnrichRetrieveFromDb:
    def _override_all(self, db_engine, session_factory, service=None):
        # The per-test in-memory ``get_session`` override is supplied
        # automatically by the autouse ``isolated_database`` fixture in
        # conftest.py; only the enrichment service needs overriding here.
        if service is not None:
            app.dependency_overrides[get_enrichment_service] = lambda: service

    def test_fresh_record_returns_from_db(self, db_engine, session_factory):
        seed = session_factory()
        try:
            repo = ProductRepository()
            result = _make_result()
            job = Job(kind="enrich", status="completed")
            seed.add(job)
            seed.flush()
            repo.save_enrichment(seed, result, job_id=job.id, run_id="r1")
            seed.commit()
        finally:
            seed.close()

        self._override_all(db_engine, session_factory)
        try:
            resp = TestClient(app).post(
                "/api/enrich",
                json=default_request().model_dump(),
                params={"retrieve_from_db": True},
            )
        finally:
            app.dependency_overrides.pop(get_enrichment_service, None)

        assert resp.status_code == 200
        assert resp.headers.get("x-source") == "database"
        data = resp.json()
        assert data["__source__"] == "database"
        assert data["__stale__"] is False

    def test_retrieve_from_db_true_skips_service(self, db_engine, session_factory):
        seed = session_factory()
        try:
            repo = ProductRepository()
            result = _make_result()
            job = Job(kind="enrich", status="completed")
            seed.add(job)
            seed.flush()
            repo.save_enrichment(seed, result, job_id=job.id, run_id="r1")
            seed.commit()
        finally:
            seed.close()

        call_tracker = {"run_called": False}
        real_service = fake_service()

        class SpyService(EnrichmentService):
            def run(self, req):
                call_tracker["run_called"] = True
                return real_service.run(req)

        self._override_all(
            db_engine, session_factory,
            service=SpyService(providers=[], manufacturer_domains=[]),
        )
        try:
            resp = TestClient(app).post(
                "/api/enrich",
                json=default_request().model_dump(),
                params={"retrieve_from_db": True},
            )
        finally:
            app.dependency_overrides.pop(get_enrichment_service, None)

        assert resp.status_code == 200
        assert resp.headers.get("x-source") == "database"
        assert not call_tracker["run_called"]

    def test_no_record_falls_to_pipeline(self, db_engine, session_factory):
        call_tracker = {"run_called": False}
        real_svc = fake_service()

        class SpyService(EnrichmentService):
            def run(self, req):
                call_tracker["run_called"] = True
                return real_svc.run(req)

        self._override_all(
            db_engine, session_factory,
            service=SpyService(providers=[], manufacturer_domains=[]),
        )
        try:
            resp = TestClient(app).post(
                "/api/enrich",
                json=default_request().model_dump(),
                params={"retrieve_from_db": True},
            )
        finally:
            app.dependency_overrides.pop(get_enrichment_service, None)

        assert resp.status_code == 200
        assert call_tracker["run_called"]
        data = resp.json()
        assert data["processing"]["status"] in ("completed", "needs_review")

    def test_false_preserves_existing_behavior(self, db_engine, session_factory):
        call_tracker = {"run_called": False}
        real_svc = fake_service()

        class SpyService(EnrichmentService):
            def run(self, req):
                call_tracker["run_called"] = True
                return real_svc.run(req)

        self._override_all(
            db_engine, session_factory,
            service=SpyService(providers=[], manufacturer_domains=[]),
        )
        try:
            resp = TestClient(app).post(
                "/api/enrich",
                json=default_request().model_dump(),
                params={"retrieve_from_db": False},
            )
        finally:
            app.dependency_overrides.pop(get_enrichment_service, None)

        assert resp.status_code == 200
        assert call_tracker["run_called"]
        assert resp.headers.get("x-source") != "database"

    def test_default_param_also_runs_pipeline(self, db_engine, session_factory):
        call_tracker = {"run_called": False}
        real_svc = fake_service()

        class SpyService(EnrichmentService):
            def run(self, req):
                call_tracker["run_called"] = True
                return real_svc.run(req)

        self._override_all(
            db_engine, session_factory,
            service=SpyService(providers=[], manufacturer_domains=[]),
        )
        try:
            resp = TestClient(app).post(
                "/api/enrich",
                json=default_request().model_dump(),
            )
        finally:
            app.dependency_overrides.pop(get_enrichment_service, None)

        assert resp.status_code == 200
        assert call_tracker["run_called"]

    def test_same_manufacturer_served_from_db(self, db_engine, session_factory):
        """A fresh record whose manufacturer matches the request is served."""
        seed = session_factory()
        try:
            repo = ProductRepository()
            result = _make_result_for(
                "49-94-0013",
                "Milwaukee Electric Tool Corp (2300)",
                "Milwaukee",
            )
            job = Job(kind="enrich", status="completed")
            seed.add(job)
            seed.flush()
            repo.save_enrichment(seed, result, job_id=job.id, run_id="r1")
            seed.commit()
        finally:
            seed.close()

        self._override_all(db_engine, session_factory)
        try:
            resp = TestClient(app).post(
                "/api/enrich",
                json=default_request(
                    Mfg_Part_Num="49-94-0013",
                    Part_Manuf="Milwaukee Electric Tool Corp (2300)",
                ).model_dump(),
                params={"retrieve_from_db": True},
            )
        finally:
            app.dependency_overrides.pop(get_enrichment_service, None)

        assert resp.status_code == 200
        assert resp.headers.get("x-source") == "database"

    def test_different_manufacturer_never_served_from_db(
        self, db_engine, session_factory
    ):
        """MPNs are not globally unique: a Milwaukee record must not be
        returned for a Craftsman request that shares the MPN. Regression:
        the DB-first path was keyed on MPN alone, so a cross-manufacturer
        request could be served a fresh record for the WRONG product."""
        seed = session_factory()
        try:
            repo = ProductRepository()
            result = _make_result_for(
                "49-94-0013",
                "Milwaukee Electric Tool Corp (2300)",
                "Milwaukee",
            )
            job = Job(kind="enrich", status="completed")
            seed.add(job)
            seed.flush()
            repo.save_enrichment(seed, result, job_id=job.id, run_id="r1")
            seed.commit()
        finally:
            seed.close()

        call_tracker = {"run_called": False}
        real_svc = fake_service()

        class SpyService(EnrichmentService):
            def run(self, req):
                call_tracker["run_called"] = True
                return real_svc.run(req)

        self._override_all(
            db_engine,
            session_factory,
            service=SpyService(providers=[], manufacturer_domains=[]),
        )
        try:
            resp = TestClient(app).post(
                "/api/enrich",
                json=default_request(
                    Mfg_Part_Num="49-94-0013",
                    Part_Manuf="Craftsman (9192)",
                ).model_dump(),
                params={"retrieve_from_db": True},
            )
        finally:
            app.dependency_overrides.pop(get_enrichment_service, None)

        assert resp.status_code == 200
        assert resp.headers.get("x-source") != "database"
        assert call_tracker["run_called"]


# --------------------------------------------------------------------------
# 9. Persistence failure returns sanitized error
# --------------------------------------------------------------------------


class TestPersistenceFailure:
    def test_sanitized_500_on_save_failure(self):
        # The in-memory ``get_session`` override is supplied automatically by
        # the autouse ``isolated_database`` fixture in conftest.py, so the
        # failed save below hits the isolated DB rather than the dev DB.
        app.dependency_overrides[get_enrichment_service] = lambda: fake_service()

        def failing_save(*args, **kwargs):
            raise RuntimeError("disk full / DB locked / secret-key-12345")

        from app.db import repository as repo_mod
        original = repo_mod.ProductRepository.save_enrichment
        repo_mod.ProductRepository.save_enrichment = failing_save
        try:
            resp = TestClient(app).post(
                "/api/enrich",
                json=default_request().model_dump(),
            )
        finally:
            repo_mod.ProductRepository.save_enrichment = original
            app.dependency_overrides.pop(get_enrichment_service, None)

        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "persistence" in detail.lower()
        assert "secret-key-12345" not in detail
        assert resp.headers.get("x-persistence-error") == "true"


# --------------------------------------------------------------------------
# 10. SQLite migration is idempotent
# --------------------------------------------------------------------------


class TestMigrationIdempotent:
    def test_fresh_db_no_op(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(bind=engine)
        run_migrations(engine)
        with engine.connect() as conn:
            cols = {r[1] for r in conn.execute(text("PRAGMA table_info(product_records)"))}
        for name in (
            "raw_description", "sources_json", "evidence_json", "attributes_json",
            "descriptions_json", "validation_json", "enrichment_history_json",
            "last_enriched_at", "source_freshness_days",
        ):
            assert name in cols

        # Run again — should be no-op, no error
        run_migrations(engine)
        with engine.connect() as conn:
            cols2 = {r[1] for r in conn.execute(text("PRAGMA table_info(product_records)"))}
        assert cols == cols2

    def test_migration_preserves_existing_rows(self):
        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE product_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER,
                    manufacturer TEXT DEFAULT '',
                    brand TEXT DEFAULT '',
                    part_number TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    quality_score REAL DEFAULT 0.0,
                    payload TEXT DEFAULT '{}'
                )
            """))
            conn.execute(text("""
                INSERT INTO product_records (job_id, part_number, status)
                VALUES (1, 'OLD-MPN', 'completed')
            """))

        migrate_product_records(engine)

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT part_number, status, raw_description, source_freshness_days "
                     "FROM product_records WHERE id = 1")
            ).first()
        assert row.part_number == "OLD-MPN"
        assert row.status == "completed"
        assert row.raw_description == ""
        assert row.source_freshness_days == 30


# --------------------------------------------------------------------------
# 11. Legacy DB upgrades successfully
# --------------------------------------------------------------------------


class TestLegacyUpgrade:
    def test_step9_db_gains_all_columns(self):
        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT DEFAULT 'lookup',
                    status TEXT DEFAULT 'pending',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE TABLE product_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER,
                    manufacturer TEXT DEFAULT '',
                    brand TEXT DEFAULT '',
                    part_number TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    quality_score REAL DEFAULT 0.0,
                    payload TEXT DEFAULT '{}'
                )
            """))
            conn.execute(text("INSERT INTO product_records (job_id, part_number) VALUES (1, 'X')"))

        run_migrations(engine)

        with engine.connect() as conn:
            cols = {r[1] for r in conn.execute(text("PRAGMA table_info(product_records)"))}
            row_count = conn.execute(text("SELECT count(*) FROM product_records")).scalar()
        assert row_count == 1
        for col in [
            "raw_description", "sources_json", "evidence_json", "attributes_json",
            "descriptions_json", "validation_json", "enrichment_history_json",
            "last_enriched_at", "source_freshness_days",
        ]:
            assert col in cols, f"{col} missing after migration"


# --------------------------------------------------------------------------
# 12. No API key/secrets in persisted JSON
# --------------------------------------------------------------------------


class TestNoSecretsInPersistedJson:
    def test_saved_payload_excludes_secrets(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "llm_api_key", "sk-TEST-SECRET-12345")
        monkeypatch.setattr(settings, "search_provider_api_key", "search-SECRET-99999")

        repo = ProductRepository()
        result = _make_result()
        job = Job(kind="enrich", status="completed")
        db_session.add(job)
        db_session.flush()

        repo.save_enrichment(db_session, result, job_id=job.id, run_id="r1")
        db_session.flush()

        record = repo.get_product_by_record_id(db_session, 1)
        assert record is not None

        all_text = " ".join([
            record.payload or "",
            record.raw_description or "",
            record.sources_json or "",
            record.evidence_json or "",
            record.attributes_json or "",
            record.descriptions_json or "",
            record.validation_json or "",
            record.enrichment_history_json or "",
        ])
        assert "sk-TEST-SECRET-12345" not in all_text
        assert "search-SECRET-99999" not in all_text
        assert "Authorization" not in all_text
