"""Who is calling.

With AUTH_MODE=accounts (the default) a caller proves who they are with a login token, and their workspace is
their user id. With AUTH_MODE=none there is no login and a browser names its own workspace in X-Workspace-Id,
which is only suitable for one person using the server on their own machine.

Every endpoint gets its identity from `get_workspace_id`, so this is the one place that decides.
"""

import re

from fastapi import Depends, Header, HTTPException

from app.config import get_settings
from app.core import limits
from app.services import auth_service
from app.services.auth_service import AuthError, User

_WORKSPACE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


def bearer_token(authorization: str | None) -> str:
    """The token in an `Authorization: Bearer <token>` header, or a 401."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Log in to continue.", headers=_UNAUTHORIZED)
    return token.strip()


def auth_http_error(exc: AuthError) -> HTTPException:
    headers = dict(_UNAUTHORIZED) if exc.status_code == 401 else {}
    if exc.retry_after:
        headers["Retry-After"] = str(exc.retry_after)
    return HTTPException(status_code=exc.status_code, detail=exc.message, headers=headers or None)


def accounts_enabled() -> bool:
    return get_settings().auth_mode == "accounts"


def get_token(authorization: str | None = Header(None)) -> str:
    if not accounts_enabled():
        raise HTTPException(status_code=404, detail="Accounts are not enabled on this server.")
    return bearer_token(authorization)


def get_current_user(authorization: str | None = Header(None)) -> User:
    token = get_token(authorization)
    try:
        return auth_service.user_for_token(token)
    except AuthError as exc:
        raise auth_http_error(exc) from exc


def _too_many(wait: float) -> HTTPException:
    seconds = int(wait) + 1
    return HTTPException(
        status_code=429, headers={"Retry-After": str(seconds)},
        detail=f"You are asking too fast. Try again in {seconds} second{'s' if seconds != 1 else ''}.")


def get_workspace_id(
    authorization: str | None = Header(None),
    x_workspace_id: str | None = Header(None),
) -> str:
    """The caller's workspace: their user id, or in AUTH_MODE=none the id the browser chose."""
    settings = get_settings()
    if settings.auth_mode == "accounts":
        workspace = get_current_user(authorization).id          # the workspace header is ignored: it proves nothing
    else:
        if x_workspace_id is None or not _WORKSPACE_RE.match(x_workspace_id):
            raise HTTPException(
                status_code=400,
                detail="X-Workspace-Id must be 8-64 characters: letters, digits, '-' or '_'.")
        workspace = x_workspace_id
    wait = limits.requests.hit(workspace, settings.rate_limit_per_minute)
    if wait is not None:
        raise _too_many(wait)
    return workspace


def get_probe_workspace_id(workspace_id: str = Depends(get_workspace_id)) -> str:
    """Like get_workspace_id, for the endpoints that try to reach a database host. Each try can reveal whether
    something is listening there, so each person gets few of them, whether or not the host was allowed."""
    wait = limits.db_probes.hit(workspace_id, get_settings().rate_limit_db_probe_per_minute)
    if wait is not None:
        raise _too_many(wait)
    return workspace_id


def get_llm_workspace_id(workspace_id: str = Depends(get_workspace_id)) -> str:
    """Like get_workspace_id, for the endpoints that call the model. Those cost money and time, so each person
    also has a lower limit on them, shared by all of them. Refused before the model is called."""
    wait = limits.llm_requests.hit(workspace_id, get_settings().rate_limit_llm_per_minute)
    if wait is not None:
        raise _too_many(wait)
    return workspace_id
