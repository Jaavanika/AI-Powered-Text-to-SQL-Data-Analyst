"""Keep secrets and people's search terms out of the logs.

Redaction happens when a log record is created, so it applies to every logger and handler, including
uvicorn's access log and the tracebacks of logged exceptions. It looks for known shapes (bearer tokens,
password= and api_key= pairs, passwords inside connection URLs, the configured keys) and removes the query
string from request lines, since that is where search terms end up.
"""

import logging
import re
import traceback

from app.config import get_settings

REDACTED = "[redacted]"
_KEY = (r"(?:password|passwd|pwd|secret|client[_-]?secret|api[_-]?key|authorization"
        r"|(?:access|refresh|session|auth|id)?[_-]?token)")
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), f"Bearer {REDACTED}"),
    # password=x   "password": "x"   'password': 'x'   token: x
    (re.compile(rf"""(?i)(\b{_KEY}\b["']?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,;&)}}\]]+)"""), rf"\1{REDACTED}"),
    # scheme://user:password@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+):[^\s/@]+@"), rf"\1:{REDACTED}@"),
    # GET /path?query -> GET /path?[redacted]
    (re.compile(r"(?i)\b((?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+\S*?)\?\S*"), rf"\1?{REDACTED}"),
]
MIN_SECRET_LENGTH = 8
_installed = False


def _configured_secrets() -> list[str]:
    settings = get_settings()
    return [s for s in (settings.genai_platform_api_key, settings.credentials_key) if s and len(s) >= MIN_SECRET_LENGTH]


def redact(text: str) -> str:
    for secret in _configured_secrets():
        text = text.replace(secret, REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _redact_access_args(args: tuple) -> tuple:
    """uvicorn's access formatter unpacks five fields, so keep the tuple and clean its parts."""
    return tuple(
        re.sub(r"\?.*", f"?{REDACTED}", a) if isinstance(a, str) and a.startswith("/") else redact(a) if isinstance(a, str) else a
        for a in args)


def install() -> None:
    """Redact every log record from now on. Safe to call more than once."""
    global _installed
    if _installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        try:
            if record.name == "uvicorn.access" and isinstance(record.args, tuple):
                record.args = _redact_access_args(record.args)
            else:
                record.msg = redact(record.getMessage())
                record.args = None
            if record.exc_info and record.exc_info[0] is not None:
                record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)).rstrip("\n"))
        except Exception:                     # a malformed log call must still fail the way logging normally does
            pass
        return record

    logging.setLogRecordFactory(factory)
    _installed = True
