"""API tests for the evaluation harness endpoint (Step 14B, D3/D4)."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_evaluation_run_endpoint_offline():
    response = client.post("/api/evaluation/run", json={"limit": 5, "live": False})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "offline"
    assert body["rows_evaluated"] == 5
    assert "placeholder_leak_rows" in body
    assert "invoice_rule_pass_rate" in body
    assert body["report_path"]


def test_dashboard_includes_compliance_block():
    # Produce a report so the dashboard compliance block has something to read.
    client.post("/api/evaluation/run", json={"limit": 5, "live": False})
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert "compliance" in body
    compliance = body["compliance"]
    assert compliance is not None
    assert "placeholder_leak_rows" in compliance
    assert compliance["last_report_path"] is not None
