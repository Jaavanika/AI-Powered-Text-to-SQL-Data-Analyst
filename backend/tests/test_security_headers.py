"""Responses carry headers that stop browsers from caching, sniffing or framing them."""

import pytest


@pytest.mark.parametrize("path", ["/api/datasources", "/api/datasources/nope/schema", "/api/auth/config"])
def test_api_responses_are_not_cached_sniffed_or_framed(client, path):
    r = client.get(path)
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert "default-src 'none'" in r.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_error_responses_have_them_too(accounts):
    r = accounts.get("/api/datasources")
    assert r.status_code == 401 and r.headers["Cache-Control"] == "no-store"
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_the_health_check_and_docs_keep_the_basic_headers_but_are_not_locked_to_json(client):
    r = client.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" not in client.get("/docs").headers      # the docs page needs its own scripts
    assert "no-store" not in client.get("/docs").headers.get("Cache-Control", "")
