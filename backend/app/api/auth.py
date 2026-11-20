from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import get_settings
from app.core.workspace import accounts_enabled, auth_http_error, get_current_user, get_token
from app.services import auth_service
from app.services.auth_service import AuthError, User

router = APIRouter(prefix="/api/auth", tags=["accounts"])

# Generous bounds only; the real rules (and their messages) are in auth_service.
MAX_FIELD = 1000


class Credentials(BaseModel):
    username: str = Field(max_length=MAX_FIELD)
    password: str = Field(max_length=MAX_FIELD)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=MAX_FIELD)
    new_password: str = Field(max_length=MAX_FIELD)


class UserOut(BaseModel):
    id: str
    username: str


class Session(BaseModel):
    token: str
    user: UserOut


class AuthConfig(BaseModel):
    mode: str
    registration_open: bool


def _address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _require_accounts() -> None:
    if not accounts_enabled():
        raise HTTPException(status_code=404, detail="Accounts are not enabled on this server.")


def _session(user: User, token: str) -> Session:
    return Session(token=token, user=UserOut(id=user.id, username=user.username))


@router.get("/config", response_model=AuthConfig)
def config():
    """What the login screen needs to know before anyone is logged in."""
    settings = get_settings()
    accounts = settings.auth_mode == "accounts"
    return AuthConfig(mode=settings.auth_mode, registration_open=accounts and settings.allow_registration)


@router.post("/register", response_model=Session, status_code=201)
def register(body: Credentials, request: Request):
    """Create an account and log in to it. Returns the session token."""
    _require_accounts()
    try:
        return _session(*auth_service.register(body.username, body.password, _address(request)))
    except AuthError as exc:
        raise auth_http_error(exc) from exc


@router.post("/login", response_model=Session)
def login(body: Credentials, request: Request):
    """Log in with a username and password. Returns the session token."""
    _require_accounts()
    try:
        return _session(*auth_service.login(body.username, body.password, _address(request)))
    except AuthError as exc:
        raise auth_http_error(exc) from exc


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    """The logged-in user."""
    return UserOut(id=user.id, username=user.username)


@router.post("/logout", status_code=204)
def logout(token: str = Depends(get_token), user: User = Depends(get_current_user)):
    """End this session only; the same user's other sessions stay logged in."""
    auth_service.logout(token)


@router.post("/password", status_code=204)
def change_password(body: PasswordChange, request: Request, token: str = Depends(get_token),
                    user: User = Depends(get_current_user)):
    """Change your password. Your other logged-in sessions end; this one carries on."""
    try:
        auth_service.change_password(user, body.current_password, body.new_password, _address(request), token)
    except AuthError as exc:
        raise auth_http_error(exc) from exc
