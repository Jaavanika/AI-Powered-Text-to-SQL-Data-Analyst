"""The server's limiters, shared by the login and request checks. Limits come from settings on every call."""

from app.config import get_settings
from app.core.ratelimit import FailureLimiter, SlidingWindow

MINUTE, HOUR = 60, 3600

requests = SlidingWindow(MINUTE)          # every authenticated request, per user
llm_requests = SlidingWindow(MINUTE)      # the endpoints that call the model, per user
registrations = SlidingWindow(HOUR)       # new accounts, per client address
db_probes = SlidingWindow(MINUTE)         # testing or saving a database connection, per user

# Locked out after too many wrong passwords. Counted per username, so one account cannot be guessed at,
# and per address, so one machine cannot try many accounts. Rebuilt when the settings change.
_failures: dict[str, FailureLimiter] = {}


def failures(kind: str) -> FailureLimiter:
    settings = get_settings()
    limit = settings.login_max_failures if kind == "user" else settings.login_max_failures_per_ip
    window = settings.login_lockout_minutes * 60
    current = _failures.get(kind)
    if current is None or (current.max_failures, current.window) != (limit, window):
        current = _failures[kind] = FailureLimiter(limit, window)
    return current


def reset_all() -> None:
    for limiter in (requests, llm_requests, registrations, db_probes):
        limiter.reset()
    _failures.clear()
