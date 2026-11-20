import os

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from tests.helpers import FakeLLM


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A test client whose data directory is an empty temp folder.

    The LLM key is blanked so a test can never reach the real LLM by accident;
    use the `fake_llm` fixture, or the opt-in live tests, instead.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GENAI_PLATFORM_API_KEY", "")
    monkeypatch.setenv("AUTH_MODE", "none")          # most tests are about something else; see the `accounts` fixture
    get_settings.cache_clear()
    from app.core import limits
    limits.reset_all()
    from app.api.sql import llm_dependency  # noqa: F401  (imported so overrides can target it)
    from app.main import app

    with TestClient(app, headers={"X-Workspace-Id": "workspace-one"}) as c:
        yield c
    app.dependency_overrides.clear()
    from app.services import postgres_connector
    for datasource_id in list(postgres_connector._engines):      # no pooled connections outlive a test
        postgres_connector.dispose_engine(datasource_id)
    get_settings.cache_clear()


@pytest.fixture
def accounts(client, monkeypatch):
    """Switch the app to real accounts and return a client with no identity of its own.

    Hashing is made cheap here; tests/test_passwords.py covers the real parameters.
    """
    from app.core import passwords

    monkeypatch.setenv("AUTH_MODE", "accounts")
    get_settings.cache_clear()
    monkeypatch.setattr(passwords, "DEFAULT_PARAMS", (2 ** 10, 8, 1))
    return TestClient(client.app)


@pytest.fixture
def sample_schema(client):
    from app.services import connectors, schema_service

    datasource, connector = connectors.resolve("sample", None)
    return schema_service.build_schema(datasource, connector)


@pytest.fixture
def other_client(client):
    """A second client talking to the same app but from a different workspace."""
    c = TestClient(client.app, headers={"X-Workspace-Id": "workspace-two"})
    yield c


@pytest.fixture
def fake_llm(client):
    """A scripted LLM wired into the app. Queue replies with `fake_llm.replies.append(...)`."""
    from app.api.sql import llm_dependency

    llm = FakeLLM()
    client.app.dependency_overrides[llm_dependency] = lambda: llm
    return llm


# ------------------------------------------------------------------- PostgreSQL

@pytest.fixture(scope="session")
def pg_admin_uri(tmp_path_factory):
    """A superuser URI for a real PostgreSQL server: TEST_POSTGRES_URL, or an embedded one.

    The embedded server needs `pip install -r requirements-dev.txt`; without it (and without
    TEST_POSTGRES_URL) the PostgreSQL tests are skipped.
    """
    external = os.environ.get("TEST_POSTGRES_URL")
    if external:
        yield external
        return
    try:
        from app.devtools.postgres_sample import start_embedded_server
        server = start_embedded_server(tmp_path_factory.mktemp("pg"))
    except ImportError:
        pytest.skip("no PostgreSQL available: pip install -r requirements-dev.txt, or set TEST_POSTGRES_URL")
    yield server.get_uri()
    server.cleanup()


@pytest.fixture(scope="session")
def pg(pg_admin_uri):
    """The sample business data in a real PostgreSQL schema `shop`, plus roles to connect as."""
    from app.devtools import postgres_sample as ps

    ps.load_sample(pg_admin_uri)
    ps.create_role(pg_admin_uri, "analyst", password="analyst-pw")                       # read-only
    ps.create_role(pg_admin_uri, "shop_owner", password="owner-pw", can_write=True, owner=True)
    base = ps.split_uri(pg_admin_uri)
    return {"admin_uri": pg_admin_uri, "host": base["host"], "port": base["port"], "database": base["database"],
            "superuser": base["username"], "schema": ps.SCHEMA}


@pytest.fixture
def pg_connect(client, pg):
    """connect(**overrides) -> the /api/database/connect response, as the read-only analyst by default."""
    def connect(**overrides):
        body = {"host": pg["host"], "port": pg["port"], "database": pg["database"], "username": "analyst",
                "password": "analyst-pw", "schema_name": pg["schema"], "sslmode": "disable", **overrides}
        return client.post("/api/database/connect", json=body)

    return connect


@pytest.fixture
def pg_source(pg_connect):
    """A connected read-only data source (its JSON)."""
    response = pg_connect()
    assert response.status_code == 201, response.text
    return response.json()["datasource"]
