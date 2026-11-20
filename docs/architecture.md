# Architecture

How the system is put together, and why. For the endpoints see [api_documentation.md](api_documentation.md); for what was
built in each stage, what was measured and what is still open see [implementation_plan.md](implementation_plan.md);
for answer quality see [evaluation_report.md](evaluation_report.md).

## The pieces

```text
 Browser (React single-page app)
        │  JSON over HTTP, Authorization: Bearer <token>
        ▼
 FastAPI backend ──────────────► Azure OpenAI (writes SQL, checks ambiguity, writes summaries)
        │
        ├─► registry.sqlite ........ accounts, sessions, data sources, conversations, definitions, blocked columns
        ├─► workspaces/<user>/ ..... one SQLite file per uploaded dataset
        ├─► sample/ ................ the shared, read-only sample business database
        └─► external PostgreSQL .... connected by the user, read-only, host on an allowlist
```

The backend is the only thing that talks to databases and to the language model. The browser never sees database
credentials, never receives SQL it can run itself, and cannot make the backend run SQL that did not pass validation.

## Life of a question

`POST /api/chat/query` (a question, or an answer to a clarification, or a follow-up):

1. **Who is asking.** One dependency (`core/workspace.get_workspace_id`) turns the login token into a workspace and
   applies the per-user rate limits. Every endpoint uses it, and a test fails if any `/api` route does not.
2. **Which data.** The data source is looked up for that workspace only (`api/deps.resolve_source`); someone else's id is a 404.
   A *connector* (SQLite file or external database) hides how it is reached.
3. **What the model may see.** The schema is read, then reduced to what this user may read (`blocked_service.visible`):
   blocked columns and tables, and their sampled values, never reach the prompt.
4. **Linking.** `schema_linking` picks the tables and columns the question is about, finds data values it mentions
   ("Electronics"), and works out how the tables join. Follow-ups are linked together with the earlier question.
5. **Definitions.** `context_service.select` adds the user's business definitions that the question (or the conversation)
   uses, such as what "revenue" means, and any table or column notes.
6. **Ambiguity and generation, at the same time.** One model call decides whether the question needs clarifying ("best-performing
   by what?"); another writes the SQL. If the first says to ask, the user gets the clarification at once and the SQL is discarded
   unread. Otherwise the SQL is used (`AMBIGUITY_PARALLEL`).
7. **Validation.** The SQL is parsed (`sql_validation`, with SQLGlot): exactly one `SELECT`, only allowlisted functions and
   constructs for the dialect, only known tables and columns, nothing blocked.
8. **Read-only execution.** `connector.execute` runs it with a time limit and a row limit in a read-only session (see below).
9. **Correction.** If a check or the database rejects the SQL, a sanitised description of the problem goes back to the model and the
   new SQL starts again from step 7, at most `SQL_MAX_ATTEMPTS` times.
10. **Analysis.** The result is profiled and a chart type chosen (`result_analysis`, `visualization`). A written summary is a separate
    request after the answer is shown; every figure in it is checked against the result.
11. **Remembering.** The question, SQL, status and summary are stored (rows only if `HISTORY_RETAIN_ROWS` allows).

Nothing a model writes is run without passing step 7, and nothing is run outside step 8's read-only session.

## What stops what

| Risk | Defence | Where |
|---|---|---|
| The model writes `DROP`, `DELETE`, several statements, or a dangerous function | Single-`SELECT` parse and per-dialect allowlists | `services/sql_validation.py`, `sql_allowlist.py` |
| SQL that slips past the parser | SQLite opened read-only with an authorizer that denies everything but reads; PostgreSQL transactions are read-only and locked | `services/query_execution.py`, `postgres_connector.py` |
| A runaway query | Time limit (progress handler / `statement_timeout`) and a row limit | same |
| Reading something private | Blocked columns and tables, per data source, enforced in validation and in the database layer, and hidden from the model, hints and previews | `services/blocked_service.py` |
| Prompt injection through data or definitions | Everything from the data is placed in the prompt as flattened text, with a rule not to follow it; the output still has to pass validation | `services/sql_generation.py` |
| Another user's data | Every query is scoped by the logged-in user's workspace; tests enumerate all endpoints | `core/workspace.py`, `api/deps.py` |
| Guessing passwords, scraping, cost | Scrypt hashes, lockouts, per-user rate limits (stricter for model calls and database probing) | `core/passwords.py`, `core/ratelimit.py`, `core/limits.py` |
| The backend used to probe a network | Database hosts must be on `ALLOWED_DB_HOSTS`; names are resolved once and the connection goes to exactly those checked addresses | `core/hosts.py` |
| Stolen credentials | Database passwords encrypted at rest; never returned; scrubbed from errors and logs | `core/secrets.py`, `core/log_redaction.py` |
| An over-powerful database account | Superusers refused; write access and powerful roles reported as warnings | `services/database_service.py` |
| Hostile uploads | Size and row limits, generated safe identifiers, per-workspace storage | `services/file_import_service.py` |

Database-level permissions remain the final protection: connect with a dedicated read-only role.

## Where data lives

Everything is under `DATA_DIR` (default `data/` at the project root):

- `registry.sqlite`: users, sessions (token hashes only), data sources (a connected database's password encrypted),
  relationships, conversations, turns, kept results, definitions, blocked columns.
- `sample/`: the sample business database, created on first start and shared read-only by everyone.
- `workspaces/<user id>/`: staged uploads and the SQLite file for each imported dataset.
- `credentials.key`: the key that encrypts stored database passwords, unless `CREDENTIALS_KEY` is set.

## Backend map (`backend/app`)

| Folder | Contents |
|---|---|
| `api/` | The HTTP layer: one router per area, thin. Parsing, status codes and nothing else. |
| `core/` | Identity and limits (`workspace`, `passwords`, `ratelimit`, `limits`), host policy (`hosts`), encryption (`secrets`), log redaction, SQLite helpers. |
| `services/` | The behaviour. `analyst_workflow` is the entry for a chat message and calls linking, ambiguity, generation, validation, execution and analysis in order. |
| `schemas.py` | Every request and response model. |
| `devtools/` | Test and development helpers (the PostgreSQL sample loader). Not used by the running app. |

Related code outside the app: `backend/evaluation/` (the evaluation suite), `backend/scripts/` (dev PostgreSQL, evaluation, API docs),
`backend/tests/` (the test suite).

## Frontend map (`frontend/src`)

A React single-page app with hash routes (`#/ask`, `#/history`, `#/context`, `#/data`, `#/schema`, `#/account`), no router
library. `AuthContext` knows who is logged in and mounts the rest of the app per user, so nothing one person loaded can remain on
screen for the next. `AppContext` holds the data sources. `services/api.js` is the only place that calls the backend; it adds the
token and reports a rejected token so the app can return to the login screen. Charts are drawn with Recharts and loaded on first use.

## Extending

- **A new kind of database:** implement the `Connector` protocol (`services/connectors.py`): a SQLAlchemy engine, a read-only
  `execute`, `preview`, `cache_key`, and a SQLGlot dialect name. Add that dialect's rules to `sql_allowlist.py` and its notes to
  `sql_generation.py`. PostgreSQL (`postgres_connector.py`) is the worked example; the validator, linking, charts and history need no change.
- **A new endpoint:** use `Depends(get_workspace_id)`, and `get_llm_workspace_id` if it calls the model. Add a docstring, then
  run `python scripts/export_api_docs.py`; the test suite fails until the document matches.

## Testing

`pytest` in `backend/` runs the fast suite. The opt-in live tests (`RUN_LIVE_LLM=1`) call the real model. PostgreSQL tests use an embedded
server (`requirements-dev.txt`) or `TEST_POSTGRES_URL`, and are skipped if neither exists. `python scripts/evaluate.py` runs the
evaluation suite against the real model. `npm test` in `frontend/` runs the interface tests.
