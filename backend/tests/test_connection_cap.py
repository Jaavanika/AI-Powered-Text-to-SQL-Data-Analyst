"""How many external databases one workspace may keep connected."""

import pytest

from app.config import get_settings


def setting(monkeypatch, name, value):
    monkeypatch.setenv(name, str(value))
    get_settings.cache_clear()


def body(pg, **overrides):
    return {"host": pg["host"], "port": pg["port"], "database": pg["database"], "username": "analyst",
            "password": "analyst-pw", "schema_name": pg["schema"], "sslmode": "disable", **overrides}


def connect(c, pg, **kw):
    return c.post("/api/database/connect", json=body(pg, **kw))


def test_a_workspace_cannot_save_more_connections_than_the_cap(client, pg, monkeypatch):
    setting(monkeypatch, "MAX_CONNECTIONS_PER_WORKSPACE", 2)
    assert connect(client, pg, name="one").status_code == 201
    assert connect(client, pg, name="two").status_code == 201
    r = connect(client, pg, name="three")
    assert r.status_code == 409 and "limit of 2" in r.json()["detail"] and "Disconnect" in r.json()["detail"]
    assert len([d for d in client.get("/api/datasources").json() if d["kind"] == "postgres"]) == 2


def test_disconnecting_makes_room(client, pg, monkeypatch):
    setting(monkeypatch, "MAX_CONNECTIONS_PER_WORKSPACE", 1)
    first = connect(client, pg).json()["datasource"]["id"]
    assert connect(client, pg).status_code == 409
    assert client.delete(f"/api/datasources/{first}").status_code == 204
    assert connect(client, pg).status_code == 201


def test_the_cap_is_per_workspace(client, other_client, pg, monkeypatch):
    setting(monkeypatch, "MAX_CONNECTIONS_PER_WORKSPACE", 1)
    assert connect(client, pg).status_code == 201
    assert connect(client, pg).status_code == 409
    assert connect(other_client, pg).status_code == 201


def test_uploads_and_the_sample_do_not_count(client, pg, monkeypatch):
    from tests.helpers import upload_csvs
    setting(monkeypatch, "MAX_CONNECTIONS_PER_WORKSPACE", 1)
    upload_csvs(client, {"t": "a\n1\n"})
    assert connect(client, pg).status_code == 201


def test_testing_a_connection_is_not_blocked_by_the_cap_since_nothing_is_saved(client, pg, monkeypatch):
    setting(monkeypatch, "MAX_CONNECTIONS_PER_WORKSPACE", 1)
    connect(client, pg)
    assert client.post("/api/database/test", json=body(pg)).status_code == 200


def test_a_cap_of_zero_means_no_cap(client, pg, monkeypatch):
    setting(monkeypatch, "MAX_CONNECTIONS_PER_WORKSPACE", 0)
    assert all(connect(client, pg).status_code == 201 for _ in range(4))


def test_a_rejected_connection_does_not_use_up_room(client, pg, monkeypatch):
    setting(monkeypatch, "MAX_CONNECTIONS_PER_WORKSPACE", 1)
    assert connect(client, pg, database="no_such_db").status_code == 400             # the database does not exist
    assert connect(client, pg).status_code == 201


def test_the_cap_is_checked_before_the_server_is_contacted(client, pg, monkeypatch):
    """A full workspace is told so at once, without another trip to the database."""
    from app.services import database_service
    setting(monkeypatch, "MAX_CONNECTIONS_PER_WORKSPACE", 1)
    connect(client, pg)
    monkeypatch.setattr(database_service, "create_pg_engine", lambda *a, **k: (_ for _ in ()).throw(AssertionError("contacted the server")))
    assert connect(client, pg).status_code == 409


def test_the_default_cap_leaves_room_for_normal_use():
    assert get_settings().max_connections_per_workspace >= 3
