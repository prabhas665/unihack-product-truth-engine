"""P0 regression tests: a response and cache hit must describe one MPN."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi.testclient import TestClient

from app.api.routes.enrich import get_enrichment_service
from app.db.models import Job, ProductRecordModel
from app.main import app
from app.pipeline.enrichment import EnrichmentRequest, EnrichmentService


def _mpn_only(mpn: str) -> dict[str, str]:
    return {
        "Mfg_Part_Num": mpn,
        "Part_Desc": "",
        "E1_Brand": "",
        "Unilog_Brand": "",
        "DIB_Brand": "",
        "Part_Manuf": "",
        "source_url": "",
    }


def _delivery_value(payload: dict, header: str) -> str:
    delivery = payload["delivery"]
    return delivery["values"][delivery["headers"].index(header)]


def _assert_single_mpn(payload: dict, expected: str) -> None:
    assert payload["request"]["Mfg_Part_Num"] == expected
    assert payload["input_row"]["mfg_part_num_value"] == expected
    assert payload["product"]["identity"]["mpn"] == expected
    assert _delivery_value(payload, "Mfg_Part_Num") == expected
    assert _delivery_value(payload, "PART_NUMBER") == expected


class SpyOfflineService(EnrichmentService):
    """Real pipeline shape with no providers, retrieval, or LLM calls."""

    def __init__(self) -> None:
        super().__init__(providers=[])
        self.calls: list[str] = []

    def run(self, request):  # type: ignore[override]
        self.calls.append(request.Mfg_Part_Num)
        return super().run(request)


class TestIdentityReuseSafety:
    def _install_service(self) -> SpyOfflineService:
        service = SpyOfflineService()
        app.dependency_overrides[get_enrichment_service] = lambda: service
        return service

    def _remove_service(self) -> None:
        app.dependency_overrides.pop(get_enrichment_service, None)

    def test_sequential_runs_and_cache_hits_keep_each_mpn(self):
        service = self._install_service()
        try:
            client = TestClient(app)
            for mpn in ("XLC10ZW", "1700-1PK-BB40", "49-94-0013"):
                response = client.post("/api/enrich", json=_mpn_only(mpn))
                assert response.status_code == 200
                _assert_single_mpn(response.json(), mpn)
                assert response.headers.get("x-source") != "database"

            for mpn in ("XLC10ZW", "1700-1PK-BB40"):
                response = client.post(
                    "/api/enrich",
                    json=_mpn_only(mpn),
                    params={"retrieve_from_db": True},
                )
                assert response.status_code == 200
                assert response.headers.get("x-source") == "database"
                _assert_single_mpn(response.json(), mpn)

            assert service.calls == ["XLC10ZW", "1700-1PK-BB40", "49-94-0013"]
        finally:
            self._remove_service()

    def test_cache_off_runs_requested_product_even_when_another_is_fresh(self):
        service = self._install_service()
        try:
            client = TestClient(app)
            assert client.post("/api/enrich", json=_mpn_only("XLC10ZW")).status_code == 200

            response = client.post(
                "/api/enrich",
                json=_mpn_only("1700-1PK-BB40"),
                params={"retrieve_from_db": False},
            )
            assert response.status_code == 200
            assert response.headers.get("x-source") != "database"
            _assert_single_mpn(response.json(), "1700-1PK-BB40")
            assert service.calls == ["XLC10ZW", "1700-1PK-BB40"]
        finally:
            self._remove_service()

    def test_case_and_whitespace_share_a_canonical_response_identity(self):
        service = self._install_service()
        try:
            client = TestClient(app)
            assert client.post("/api/enrich", json=_mpn_only(" xlc10zw ")).status_code == 200

            response = client.post(
                "/api/enrich",
                json=_mpn_only("xlc10zw"),
                params={"retrieve_from_db": True},
            )
            assert response.status_code == 200
            assert response.headers.get("x-source") == "database"
            _assert_single_mpn(response.json(), "XLC10ZW")
            assert service.calls == ["XLC10ZW"]
        finally:
            self._remove_service()

    def test_duplicate_mpn_records_reuse_only_their_shared_canonical_mpn(self):
        service = self._install_service()
        try:
            client = TestClient(app)
            for _ in range(2):
                assert client.post("/api/enrich", json=_mpn_only("XLC10ZW")).status_code == 200

            response = client.post(
                "/api/enrich",
                json=_mpn_only("XLC10ZW"),
                params={"retrieve_from_db": True},
            )
            assert response.status_code == 200
            assert response.headers.get("x-source") == "database"
            _assert_single_mpn(response.json(), "XLC10ZW")
            assert service.calls == ["XLC10ZW", "XLC10ZW"]
        finally:
            self._remove_service()

    def test_corrupt_payload_is_rejected_and_requested_pipeline_runs(self, db_session):
        job = Job(kind="enrich", status="completed")
        db_session.add(job)
        db_session.flush()

        # The record key says 1700, but every payload identity says XLC.
        # This must never be returned from the database fast path.
        # A real result is needed for Pydantic rebuild to reach the identity
        # guard. Seed it from the offline pipeline, then change only its key.
        seeded = SpyOfflineService().run(
            EnrichmentRequest(**_mpn_only("XLC10ZW"))
        ).model_dump(mode="json")
        db_session.add(
            ProductRecordModel(
                job_id=job.id,
                part_number="1700-1PK-BB40",
                status="completed",
                payload=json.dumps(seeded),
                last_enriched_at=datetime.utcnow(),
            )
        )
        db_session.commit()

        service = self._install_service()
        try:
            response = TestClient(app).post(
                "/api/enrich",
                json=_mpn_only("1700-1PK-BB40"),
                params={"retrieve_from_db": True},
            )
            assert response.status_code == 200
            assert response.headers.get("x-source") != "database"
            _assert_single_mpn(response.json(), "1700-1PK-BB40")
            assert service.calls == ["1700-1PK-BB40"]
        finally:
            self._remove_service()
