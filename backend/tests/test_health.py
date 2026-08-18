from fastapi.testclient import TestClient

from app.api.routes import health as health_module
from app.config import settings
from app.llm import LLMProviderUnavailableError
from app.llm.providers.fake import FakeLLMClient
from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "Product Truth Engine"


def test_llm_health_ok(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "llm_provider", "fake")
    monkeypatch.setattr(health_module, "get_client", lambda: FakeLLMClient(responses=["ok"]))
    response = client.get("/api/health/llm")
    assert response.status_code == 200
    body = response.json()
    assert body["key_configured"] is True
    assert body["chat_completions_status"] == 200
    assert body["error"] == ""


def test_llm_health_401_reported(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(
        health_module,
        "get_client",
        lambda: FakeLLMClient(
            error=LLMProviderUnavailableError(
                "openrouter: authentication failed (HTTP 401). Check LLM_API_KEY."
            )
        ),
    )
    response = client.get("/api/health/llm")
    assert response.status_code == 200
    body = response.json()
    assert body["chat_completions_status"] == 401
    assert "authentication failed" in body["error"]


def test_llm_health_no_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_api_key", "")
    response = client.get("/api/health/llm")
    assert response.status_code == 200
    body = response.json()
    assert body["key_configured"] is False
    assert body["chat_completions_status"] is None
    assert "not set" in body["error"]