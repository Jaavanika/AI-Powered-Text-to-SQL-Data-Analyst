"""docs/api_documentation.md is generated from the running app, and must always match it."""

import re
from pathlib import Path

import pytest

from scripts import export_api_docs as docs

DOC = Path(__file__).resolve().parents[2] / "docs" / "api_documentation.md"


@pytest.fixture(scope="module")
def spec():
    from app.main import app
    return app.openapi()


def test_the_committed_document_matches_what_the_app_serves_now(spec):
    """If this fails, an endpoint changed: run `python scripts/export_api_docs.py` and commit the result."""
    assert DOC.read_text(encoding="utf-8") == docs.render(spec)


def test_every_api_endpoint_is_in_the_document(spec):
    text = docs.render(spec)
    for path, operations in spec["paths"].items():
        if path.startswith("/api/"):
            for method in operations:
                assert f"`{method.upper()} {path}`" in text, f"{method.upper()} {path}"


def test_the_endpoints_that_need_no_login_are_the_ones_the_security_tests_expect(spec):
    assert docs.OPEN == {"/api/auth/register", "/api/auth/login", "/api/auth/config"}


def test_the_limit_groups_name_real_endpoints(spec):
    real = {(m.upper(), p) for p, ops in spec["paths"].items() for m in ops}
    for group in (docs.MODEL_LIMITED, docs.PROBE_LIMITED):
        assert group <= real, group - real


def test_the_model_limited_endpoints_are_the_four_that_call_the_model(spec):
    assert {p for _, p in docs.MODEL_LIMITED} == {
        "/api/chat/query", "/api/sql/generate", "/api/sql/query", "/api/analytics/summary"}


def test_the_document_says_how_to_log_in_and_what_errors_look_like(spec):
    text = docs.render(spec)
    assert "Authorization: Bearer" in text and '"detail"' in text
    assert re.search(r"429", text) and "Retry-After" in text


def test_the_document_is_stable_between_runs(spec):
    assert docs.render(spec) == docs.render(spec)


def test_each_endpoint_row_names_its_request_and_response_shapes(spec):
    text = docs.render(spec)
    assert "ChatRequest" in text and "ChatResponse" in text and "HistoryPage" in text
