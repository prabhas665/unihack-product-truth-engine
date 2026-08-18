"""Test isolation: force every test session to run fully offline.

The development backend/.env can ship real API keys (e.g. GROQ_API_KEY,
LLM_API_KEY) left over from live runs. This fixture neutralizes them for the
whole session so no test ever contacts a real provider. Provider adapters are
still exercised offline via httpx.MockTransport; individual tests may
monkeypatch the specific settings they need. Provider *selection* fields
(DISCOVERY_PROVIDER / LLM_PROVIDER) are left untouched so tests that assert on
them keep working.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import pytest

from app.config import settings
from app.db.database import Base, get_session
from app.db.migration import run_migrations
from app.main import app

# Settings attributes (case-sensitive; pydantic-settings field names).
# Provider *selection* is forced to neutral values so tests never depend on
# whatever backend/.env currently has (e.g. LLM_PROVIDER=nvidia after a live
# run): discovery uses the empty registry (zero candidates deterministically)
# and the LLM layer uses the legacy default branch whose key is cleared, so
# get_client() degrades gracefully instead of calling the network. Tests that
# exercise a specific provider set the selection + key themselves.
_OFFLINE_KEYS = {
    "discovery_provider": "",
    "llm_provider": "",
    "llm_api_key": "",
    "llm_fallback_model": "",
    "llm_fallback_model_2": "",
    "llm_fallback_provider": "",
    "llm_fallback_provider_2": "",
    "GROQ_API_KEY": "",
    "search_provider_api_key": "",
    "GEMINI_API_KEY": "",
    "NVIDIA_NIM_API_KEY": "",
}


@pytest.fixture(autouse=True, scope="session")
def _offline_providers():
    saved = {key: getattr(settings, key) for key in _OFFLINE_KEYS}
    for key, value in _OFFLINE_KEYS.items():
        setattr(settings, key, value)
    try:
        yield
    finally:
        for key, value in saved.items():
            setattr(settings, key, value)


@pytest.fixture(scope="function")
def db_engine():
    """Fresh in-memory SQLite engine with the full schema + migrations.

    Every test that needs a database gets its own engine, so no test ever
    reads from or writes to the real ``backend/data/unihack.db``.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    run_migrations(engine)
    return engine


@pytest.fixture(scope="function")
def session_factory(db_engine):
    """Session factory bound to the per-test ``db_engine``."""
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


@pytest.fixture(scope="function")
def db_session(db_engine):
    """Direct session for repository-level tests (same engine as the route)."""
    Session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True, scope="function")
def isolated_database(session_factory):
    """Override ``get_session`` for every test with the per-test in-memory DB.

    Centralizes the isolation that was previously duplicated across several
    test modules: every ``TestClient`` database route now resolves to an empty
    in-memory SQLite database, regardless of which test imports it. Tests that
    need a custom session (e.g. ``test_batch_safety``) may override
    ``get_session`` themselves; this fixture only pops its own key on
    teardown and never calls ``clear()``, so it cannot wipe unrelated overrides.
    """
    def override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override
    yield
    app.dependency_overrides.pop(get_session, None)
