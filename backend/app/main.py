from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api import analytics, auth, blocked, chat, context, database, datasources, history, schema, sql, upload
from app.config import get_settings
from app.core import log_redaction
from app.core.hosts import HostNotAllowed
from app.core.secrets import CredentialError
from app.services import conversation_service, registry, sample_data
from app.services.query_execution import ExecutionError

settings = get_settings()
log_redaction.install()

# How an ExecutionError surfaces when it is not handled closer to the endpoint.
EXECUTION_STATUS = {"timeout": 408, "denied": 403, "sql": 400, "unavailable": 503}


@asynccontextmanager
async def lifespan(_: FastAPI):
    registry.init_registry()
    sample_data.ensure_sample_database()
    conversation_service.purge_expired()          # drop history older than the retention period
    yield


app = FastAPI(title="AI-Powered Text-to-SQL Data Analyst", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Stop browsers caching, sniffing or framing what the API returns (answers can contain private data)."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response


app.include_router(auth.router)
app.include_router(datasources.router)
app.include_router(upload.router)
app.include_router(schema.router)
app.include_router(sql.router)
app.include_router(chat.router)
app.include_router(analytics.router)
app.include_router(database.router)
app.include_router(history.router)
app.include_router(context.router)
app.include_router(blocked.router)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    # FastAPI's default response repeats the submitted values, which for a connection request
    # would include the password. Only say which field was wrong and why.
    detail = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", "Invalid value."), "type": e.get("type", "")}
              for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": detail})


@app.exception_handler(ExecutionError)
async def execution_error(_: Request, exc: ExecutionError):
    return JSONResponse(status_code=EXECUTION_STATUS.get(exc.kind, 400), content={"detail": exc.message})


@app.exception_handler(CredentialError)
async def credential_error(_: Request, exc: CredentialError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(HostNotAllowed)
async def host_not_allowed(_: Request, exc: HostNotAllowed):
    # Reached when a saved connection is used after the allowlist changed, or its name stopped resolving.
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.exception_handler(SQLAlchemyError)
async def database_error(_: Request, exc: SQLAlchemyError):
    # Driver errors can mention hosts, users or SQL; the client only needs to know the database failed.
    # 503 for every database outage, whichever step first noticed it, so clients see one consistent signal.
    return JSONResponse(status_code=503, content={"detail": "The database could not be reached or returned an error."})


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "llm_configured": bool(
            settings.genai_platform_api_key
            and settings.azure_openai_endpoint
            and settings.vision_deployment_name
        ),
    }
