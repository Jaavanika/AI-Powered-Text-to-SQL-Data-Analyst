"""PostgreSQL connections go to the address that was checked, and the allowlist applies on every use."""

import ipaddress

import pytest

from app.config import get_settings
from app.core import hosts

PINNED = "pinned.test.invalid"          # a name no DNS server knows: only a pinned address can reach it


def allow(monkeypatch, spec):
    monkeypatch.setenv("ALLOWED_DB_HOSTS", spec)
    get_settings.cache_clear()


def points_at(monkeypatch, address):
    monkeypatch.setattr(hosts, "_resolve", lambda host: [ipaddress.ip_address(address)])


def server_address(pg):
    try:
        return str(ipaddress.ip_address(pg["host"]))
    except ValueError:
        return "127.0.0.1"


def test_the_connection_goes_to_the_checked_address_not_to_whatever_the_name_resolves_to_later(
        pg, pg_connect, monkeypatch, client):
    """The name cannot be resolved by the operating system at all. Only the pinned address can work,
    which is exactly what a DNS answer that changes after the check could not influence."""
    allow(monkeypatch, PINNED)
    points_at(monkeypatch, server_address(pg))
    response = pg_connect(host=PINNED)
    assert response.status_code == 201, response.text
    source = response.json()["datasource"]
    assert client.get(f"/api/datasources/{source['id']}/schema").status_code == 200       # the pooled engine too
    assert client.post("/api/sql/execute", json={
        "datasource_id": source["id"], "sql": "SELECT COUNT(*) AS n FROM customers"}).json()["result"]["rows"] == [[40]]


def test_an_allowed_name_resolving_somewhere_dangerous_is_refused_when_connecting(pg, pg_connect, monkeypatch):
    allow(monkeypatch, PINNED)
    points_at(monkeypatch, "169.254.169.254")
    response = pg_connect(host=PINNED)
    assert response.status_code == 403 and "cannot be used" in response.json()["detail"]


def test_an_allowed_name_that_does_not_exist_is_reported_plainly(pg, pg_connect, monkeypatch):
    allow(monkeypatch, "*.test.invalid")
    response = pg_connect(host="nothing-here.test.invalid")
    assert response.status_code == 400 and "could not be found" in response.json()["detail"]


def test_tightening_the_allowlist_also_stops_connections_that_were_saved_earlier(
        pg_source, client, monkeypatch):
    assert client.get(f"/api/datasources/{pg_source['id']}/schema").status_code == 200
    allow(monkeypatch, "db.other.test.invalid")
    for response in (client.get(f"/api/datasources/{pg_source['id']}/schema"),
                     client.post("/api/sql/execute", json={"datasource_id": pg_source["id"], "sql": "SELECT 1"}),
                     client.get(f"/api/datasources/{pg_source['id']}/tables/customers/preview")):
        assert response.status_code == 403
        assert "not on this server's list of allowed database hosts" in response.json()["detail"]


def test_loosening_the_allowlist_again_restores_access(pg_source, client, monkeypatch):
    allow(monkeypatch, "db.other.test.invalid")
    assert client.get(f"/api/datasources/{pg_source['id']}/schema").status_code == 403
    monkeypatch.delenv("ALLOWED_DB_HOSTS")
    get_settings.cache_clear()
    assert client.get(f"/api/datasources/{pg_source['id']}/schema").status_code == 200


@pytest.mark.parametrize("literal", ["127.0.0.1"])
def test_a_literal_address_still_connects(pg, pg_connect, monkeypatch, literal):
    allow(monkeypatch, "localhost,127.0.0.1,::1")
    if server_address(pg) != literal:
        pytest.skip("the test server is not on 127.0.0.1")
    assert pg_connect(host=literal).status_code == 201
