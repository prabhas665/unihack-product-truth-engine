"""API tests for the evaluation harness endpoint (Step 14B, D3/D4)."""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _enable_token(monkeypatch):
    monkeypatch.setattr(settings, "evaluation_api_token", "test-token")


def _auth(headers: dict | None = None) -> dict:
    merged = {"Authorization": "Bearer test-token"}
    if headers:
        merged.update(headers)
    return merged


def test_evaluation_run_endpoint_offline():
    response = client.post(
        "/api/evaluation/run", json={"limit": 5, "live": False}, headers=_auth()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "offline"
    assert body["rows_evaluated"] == 5
    assert "placeholder_leak_rows" in body
    assert "invoice_rule_pass_rate" in body
    assert body["report_path"]


def test_dashboard_includes_compliance_block():
    # Produce a report so the dashboard compliance block has something to read.
    client.post("/api/evaluation/run", json={"limit": 5, "live": False}, headers=_auth())
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert "compliance" in body
    compliance = body["compliance"]
    assert compliance is not None
    assert "placeholder_leak_rows" in compliance
    assert compliance["last_report_path"] is not None


class TestEvaluationLockdown:
    def test_endpoint_disabled_without_configured_token(self, monkeypatch):
        monkeypatch.setattr(settings, "evaluation_api_token", "")
        response = client.post(
            "/api/evaluation/run", json={"limit": 5}, headers=_auth()
        )
        assert response.status_code == 403

    def test_missing_token_rejected(self):
        response = client.post("/api/evaluation/run", json={"limit": 5})
        assert response.status_code == 401

    def test_wrong_token_rejected(self):
        response = client.post(
            "/api/evaluation/run",
            json={"limit": 5},
            headers=_auth({"Authorization": "Bearer wrong-token"}),
        )
        assert response.status_code == 401

    def test_bad_scheme_rejected(self):
        response = client.post(
            "/api/evaluation/run",
            json={"limit": 5},
            headers=_auth({"Authorization": "Basic dG9rZW46"}),
        )
        assert response.status_code == 401

    def test_path_outside_repo_rejected(self):
        response = client.post(
            "/api/evaluation/run",
            json={"limit": 1, "input_path": "C:\\Windows\\system32\\config\\SAM"},
            headers=_auth(),
        )
        assert response.status_code == 422

    def test_relative_path_inside_repo_allowed(self):
        from pathlib import Path

        # Use absolute fixture path so test passes from either repo root or backend cwd.
        abs_path = (Path(__file__).parent / "fixtures" / "Unihack_ Sample Dataset - Input.csv").resolve()
        response = client.post(
            "/api/evaluation/run",
            json={"limit": 1, "input_path": str(abs_path)},
            headers=_auth(),
        )
        assert response.status_code == 200
        assert response.json()["rows_evaluated"] == 1
