"""Secrets and search terms never reach the logs."""

import logging

import pytest

from app.config import get_settings
from app.core import log_redaction

log = logging.getLogger("redaction.test")
API_KEY = "sk-live-1234567890abcdef"
CREDENTIALS_KEY = "Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MDEyMzQ1Njc4OTA="


@pytest.fixture(autouse=True)
def installed(monkeypatch):
    monkeypatch.setenv("GENAI_PLATFORM_API_KEY", API_KEY)
    monkeypatch.setenv("CREDENTIALS_KEY", CREDENTIALS_KEY)
    get_settings.cache_clear()
    log_redaction.install()
    yield
    get_settings.cache_clear()


def logged(caplog, message, *args, level=logging.INFO, **kw):
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="redaction.test"):
        log.log(level, message, *args, **kw)
    return caplog.text


@pytest.mark.parametrize("message,secret", [
    ("calling with Authorization: Bearer abc123.DEF_456-xyz", "abc123.DEF_456-xyz"),
    ("header was bearer  tok_en-value==", "tok_en-value"),
    ("login failed password=hunter2 for alice", "hunter2"),
    ("login failed PASSWORD: hunter2, retrying", "hunter2"),
    ('payload {"username": "alice", "password": "hunter2"}', "hunter2"),
    ("payload {'password': 'hunter2'}", "hunter2"),
    ("dsn postgresql://analyst:hunter2@db.example.com:5432/shop", "hunter2"),
    ("api_key=sk-abc-secret and more", "sk-abc-secret"),
    ("x-api-key: sk-abc-secret", "sk-abc-secret"),
    ("client_secret=s3cr3t!", "s3cr3t!"),
    ("session token=Zk9sdGVuLXRva2Vu", "Zk9sdGVuLXRva2Vu"),
])
def test_known_secret_shapes_are_removed(caplog, message, secret):
    text = logged(caplog, message)
    assert secret not in text and "[redacted]" in text


def test_the_configured_keys_are_removed_wherever_they_appear(caplog):
    text = logged(caplog, "request to endpoint failed: key %s rejected, also %s", API_KEY, f"prefix{CREDENTIALS_KEY}suffix")
    assert API_KEY not in text and CREDENTIALS_KEY not in text


def test_message_arguments_are_formatted_before_redaction(caplog):
    text = logged(caplog, "connecting as %s with %s", "analyst", "password=hunter2")
    assert "hunter2" not in text and "connecting as analyst" in text


def test_secrets_in_a_traceback_are_removed_too(caplog):
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="redaction.test"):
        try:
            raise RuntimeError("could not connect: password=hunter2 rejected")
        except RuntimeError:
            log.exception("connection failed")
    assert "hunter2" not in caplog.text and "connection failed" in caplog.text and "RuntimeError" in caplog.text


def test_the_search_terms_and_values_in_a_request_line_are_removed(caplog):
    text = logged(caplog, '%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", "/api/history?datasource_id=sample&q=salaries%20of%20staff", "1.1", 200)
    assert "salaries" not in text and "/api/history?[redacted]" in text and '" 200' in text


def test_a_path_without_a_query_is_left_alone(caplog):
    text = logged(caplog, '"%s %s HTTP/1.1" 200', "GET", "/api/datasources")
    assert "GET /api/datasources HTTP/1.1" in text


@pytest.mark.parametrize("message", [
    "max_tokens=2000 and token limit exceeded",
    "password rules: at least 10 characters",
    "Loaded 5 definitions for datasource sample",
    "SELECT COUNT(*) FROM customers WHERE city = 'Oslo'",
    "tokens: 5000",
])
def test_ordinary_messages_are_not_touched(caplog, message):
    assert message in logged(caplog, message)


def test_a_badly_formed_log_call_does_not_make_creating_the_record_fail():
    """Logging reports a bad format string when it prints the record. Redaction must not turn that into a crash."""
    record = logging.getLogRecordFactory()("redaction.test", logging.INFO, __file__, 1, "wrong number of %s arguments %s", ("one",), None)
    assert record.msg == "wrong number of %s arguments %s" and record.args == ("one",)


def test_installing_twice_does_not_stack(caplog):
    log_redaction.install(), log_redaction.install()
    assert logged(caplog, "password=hunter2").count("[redacted]") == 1


def test_redact_is_usable_directly():
    assert log_redaction.redact("password=hunter2") == "password=[redacted]"
    assert log_redaction.redact("nothing to see") == "nothing to see"


def test_the_apps_own_flow_does_not_log_what_people_type(accounts, caplog):
    with caplog.at_level(logging.DEBUG):
        accounts.post("/api/auth/register", json={"username": "alice", "password": "a-long-password"})
        accounts.post("/api/auth/login", json={"username": "alice", "password": "wrong-one-here"})
    assert "a-long-password" not in caplog.text and "wrong-one-here" not in caplog.text


def test_uvicorns_access_log_keeps_its_argument_tuple_but_loses_the_query(caplog):
    """uvicorn's formatter unpacks record.args into five fields, so the tuple has to survive."""
    access = logging.getLogger("uvicorn.access")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", "/api/history?q=salaries", "1.1", 200)
    record = caplog.records[0]
    assert isinstance(record.args, tuple) and len(record.args) == 5
    assert record.args[2] == "/api/history?[redacted]" and record.args[4] == 200
    assert "salaries" not in caplog.text
