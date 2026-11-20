from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    genai_platform_api_key: str = Field(default="", repr=False)
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2025-04-01-preview"
    # Name of the Azure OpenAI deployment used for all LLM calls.
    vision_deployment_name: str = ""

    data_dir: Path = Path("../data")

    max_upload_mb: int = 25
    max_upload_rows: int = 200_000

    query_timeout_seconds: int = 15
    query_max_rows: int = 1000
    # Total LLM attempts per question: the first try plus corrections after a failed check.
    sql_max_attempts: int = 3
    # External databases. Hosts a user may connect to: comma-separated names, '*.example.com'
    # wildcards, IP addresses or CIDR ranges. '*' allows any host. Defaults to this machine only,
    # so the server cannot be used to probe other networks until an administrator opts in.
    allowed_db_hosts: str = "localhost,127.0.0.1,::1"
    # Superuser accounts can switch off the read-only protections, so they are refused by default.
    allow_superuser_connections: bool = False
    # Key (a Fernet key) that encrypts stored database passwords. If empty, one is generated and
    # kept in DATA_DIR/credentials.key; set this (from a secrets manager) in any shared deployment.
    credentials_key: str = Field(default="", repr=False)
    db_connect_timeout_seconds: int = 10
    # Query history. Result rows are sensitive, so none are kept unless this is above 0 (the number of
    # rows kept per answer). Questions, SQL and summaries are always kept, until they are older than
    # HISTORY_RETENTION_DAYS (0 keeps them until deleted).
    history_retain_rows: int = 0
    history_retention_days: int = 90
    # Result rows shown to the LLM when it writes a summary. 0 sends only computed facts, no row data.
    summary_max_rows: int = 50
    # Ask the user to clarify undefined measures ("best-performing") before generating SQL.
    ambiguity_check: bool = True
    # Run that check and the SQL generation at the same time, so a clear question waits for the slower of the
    # two instead of both. If the question turns out to need clarifying, the generation was not needed and its
    # cost is wasted; turn this off to avoid that and accept the extra wait.
    ambiguity_parallel: bool = True
    # Comma-separated 'table.column' or 'table.*' entries that queries may never read.
    blocked_columns: str = ""

    # Who is using the server. "accounts": people register and log in, and each sees only their own data.
    # "none": no login; a browser identifies itself with an X-Workspace-Id header. Only for one person on
    # their own machine, because anyone who can reach the server can claim any workspace.
    auth_mode: Literal["accounts", "none"] = "accounts"
    # Let new people create an account. Turn off once everyone who needs one has an account.
    allow_registration: bool = True
    session_days: int = 7
    # Failed logins before a username (or an address) is locked out, and for how long.
    login_max_failures: int = 5
    login_max_failures_per_ip: int = 20
    login_lockout_minutes: int = 15
    register_per_hour_per_ip: int = 10
    # Requests per minute per user. The LLM-backed endpoints cost money and time, so they get a lower limit.
    # 0 turns a limit off.
    rate_limit_per_minute: int = 300
    rate_limit_llm_per_minute: int = 20
    # Testing or saving a database connection tells you whether something is listening at a host, so it gets a far
    # lower limit than ordinary requests, shared by both, and refused attempts count too.
    rate_limit_db_probe_per_minute: int = 10
    # External databases one workspace may keep connected (0 = no limit). Each keeps a small pool of connections open.
    max_connections_per_workspace: int = 10

    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
