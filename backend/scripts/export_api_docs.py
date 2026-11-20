"""Write docs/api_documentation.md from the running app, so the document cannot drift from the code.

    python scripts/export_api_docs.py            # rewrite the document
    python scripts/export_api_docs.py --check    # exit 1 if it is out of date (the test suite does this too)
"""

import argparse
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import Settings  # noqa: E402

DOC = BACKEND.parent / "docs" / "api_documentation.md"

# Endpoints that work without logging in.
OPEN = {"/api/auth/register", "/api/auth/login", "/api/auth/config"}
# Endpoints that call the language model: they share a lower per-user limit (RATE_LIMIT_LLM_PER_MINUTE).
MODEL_LIMITED = {("POST", "/api/chat/query"), ("POST", "/api/sql/generate"), ("POST", "/api/sql/query"),
                 ("POST", "/api/analytics/summary")}
# Endpoints that try to reach a database host: they share a tight per-user limit (RATE_LIMIT_DB_PROBE_PER_MINUTE).
PROBE_LIMITED = {("POST", "/api/database/test"), ("POST", "/api/database/connect")}

SECTIONS = [  # (tag in the app, heading, what it is for)
    ("accounts", "Accounts", "Registering, logging in and out, and changing a password."),
    ("datasources", "Data sources", "The sample database, uploaded files and connected databases."),
    ("upload", "Uploading files", "Importing CSV and Excel files in two steps: preview, then import."),
    ("database", "Connecting a database", "Testing and saving a connection to an external database."),
    ("schema", "Schema and relationships", "Tables, columns, keys and the relationships used for joins."),
    ("blocked columns", "Blocked columns", "Columns and tables questions may never read, per data source."),
    ("context", "Business definitions", "Metrics, terms and notes that questions are answered with."),
    ("sql", "SQL", "Generating, validating and running SQL for a question."),
    ("chat", "Chat", "Questions, follow-ups and conversations."),
    ("history", "Query history", "Past questions, with search, deletion and optionally kept results."),
    ("analytics", "Charts and summaries", "Turning a result into a chart and a checked written summary."),
]
METHOD_ORDER = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 3, "DELETE": 4}


def _ref(schema: dict | None) -> str | None:
    if not schema:
        return None
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if schema.get("type") == "array" and "items" in schema:
        inner = _ref(schema["items"])
        return f"list of {inner}" if inner else "list"
    return None


def _request(op: dict) -> str:
    content = (op.get("requestBody") or {}).get("content", {})
    if "multipart/form-data" in content:
        return "a file upload"
    return _ref(content.get("application/json", {}).get("schema")) or "—"


def _response(op: dict) -> str:
    for status in ("200", "201"):
        if status in op.get("responses", {}):
            content = op["responses"][status].get("content", {})
            return _ref(content.get("application/json", {}).get("schema")) or ("JSON" if content else "—")
    return "nothing (204)" if "204" in op.get("responses", {}) else "—"


def _query(op: dict) -> str:
    names = [(p["name"] + ("" if p.get("required") else "?")) for p in op.get("parameters", [])
             if p["in"] == "query"]
    return ", ".join(names) or "—"


def _first_sentence(text: str) -> str:
    flat = " ".join((text or "").split())
    match = re.match(r"(.+?[.!?])(\s|$)", flat)
    return (match.group(1) if match else flat).replace("|", "\\|")


def _default(name: str):
    return Settings.model_fields[name].default


def _intro() -> list[str]:
    return [
        "# API documentation", "",
        "*Generated from the running application by `backend/scripts/export_api_docs.py`. Do not edit by hand: "
        "a test fails when this file no longer matches the code.*", "",
        "The same information is served interactively by the backend at `/docs` (Swagger UI) and `/openapi.json`.", "",
        "## Conventions", "",
        "- **Base address:** the backend, for example `http://localhost:8000`. The frontend's dev server proxies `/api` to it.",
        "- **Format:** JSON requests and responses (except file uploads, which are `multipart/form-data`).",
        "- **Logging in:** every endpoint except the three marked *open* needs `Authorization: Bearer <token>`, where the "
        "token comes from `/api/auth/register` or `/api/auth/login`. Without it the answer is `401` with "
        "`WWW-Authenticate: Bearer`. (With `AUTH_MODE=none`, for one person on their own machine, a browser names its own "
        "workspace in an `X-Workspace-Id` header instead.)",
        "- **Errors:** the body is `{\"detail\": \"…\"}`; for a validation failure `detail` is a list of "
        "`{loc, msg, type}`. Submitted values are never echoed back.",
        "- **Status codes:** `400` bad request, `401` not logged in, `403` refused (for example a host that is not allowed), "
        "`404` not found or not yours, `408` the query ran too long, `409` a conflict (name taken, connection limit), "
        "`422` invalid input or SQL that failed validation, `429` too many requests (see `Retry-After`), `503` the "
        "database or the language model could not be reached.",
        "- **Privacy:** everything belongs to the logged-in user; another user's data source, conversation or "
        "definition is reported as `404`.", "",
        "## Request limits", "",
        "| Limit | Applies to | Default (per user, per minute) | Setting |", "|---|---|---|---|",
        f"| General | every endpoint that needs a login | {_default('rate_limit_per_minute')} | `RATE_LIMIT_PER_MINUTE` |",
        f"| Language model | endpoints marked *model* | {_default('rate_limit_llm_per_minute')}, shared by them (a question and its written summary count once each) | `RATE_LIMIT_LLM_PER_MINUTE` |",
        f"| Database probing | endpoints marked *probe* | {_default('rate_limit_db_probe_per_minute')}, shared by them; refused hosts count | `RATE_LIMIT_DB_PROBE_PER_MINUTE` |",
        f"| Failed logins | login and password change | {_default('login_max_failures')} per username and "
        f"{_default('login_max_failures_per_ip')} per address, then locked out for {_default('login_lockout_minutes')} minutes | `LOGIN_MAX_FAILURES` |",
        f"| New accounts | registration | {_default('register_per_hour_per_ip')} per address per hour | `REGISTER_PER_HOUR_PER_IP` |", "",
        f"A workspace may also keep at most {_default('max_connections_per_workspace')} external databases connected "
        "(`MAX_CONNECTIONS_PER_WORKSPACE`). A limit of `0` switches that limit off.", "",
    ]


def render(spec: dict) -> str:
    by_tag: dict[str, list[tuple[str, str, dict]]] = {}
    for path, operations in spec["paths"].items():
        if not path.startswith("/api/"):
            continue
        for method, op in operations.items():
            by_tag.setdefault(op["tags"][0], []).append((method.upper(), path, op))

    lines = _intro()
    known = {tag for tag, _, _ in SECTIONS}
    assert set(by_tag) <= known, f"endpoints with a tag that has no section: {set(by_tag) - known}"
    for tag, heading, purpose in SECTIONS:
        if tag not in by_tag:
            continue
        lines += [f"## {heading}", "", purpose, "",
                  "| Endpoint | What it does | Needs | Sends | Returns | Notes |", "|---|---|---|---|---|---|"]
        for method, path, op in sorted(by_tag[tag], key=lambda t: (t[1], METHOD_ORDER.get(t[0], 9))):
            notes = []
            if path in OPEN:
                notes.append("*open*")
            if (method, path) in MODEL_LIMITED:
                notes.append("*model*")
            if (method, path) in PROBE_LIMITED:
                notes.append("*probe*")
            query = _query(op)
            if query != "—":
                notes.append(f"query: {query}")
            needs = "nothing" if path in OPEN else "login"
            lines.append(f"| `{method} {path}` | {_first_sentence(op.get('description') or op.get('summary', ''))} | "
                         f"{needs} | {_request(op)} | {_response(op)} | {'; '.join(notes) or '—'} |")
        lines.append("")
    lines += ["`?` after a query parameter marks it optional.", ""]
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only check that the document is up to date")
    args = parser.parse_args(argv)
    from app.main import app

    text = render(app.openapi())
    if args.check:
        if not DOC.exists() or DOC.read_text(encoding="utf-8") != text:
            print("docs/api_documentation.md is out of date. Run: python scripts/export_api_docs.py", file=sys.stderr)
            return 1
        return 0
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(text, encoding="utf-8")
    print(f"Wrote {DOC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
