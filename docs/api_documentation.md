# API documentation

*Generated from the running application by `backend/scripts/export_api_docs.py`. Do not edit by hand: a test fails when this file no longer matches the code.*

The same information is served interactively by the backend at `/docs` (Swagger UI) and `/openapi.json`.

## Conventions

- **Base address:** the backend, for example `http://localhost:8000`. The frontend's dev server proxies `/api` to it.
- **Format:** JSON requests and responses (except file uploads, which are `multipart/form-data`).
- **Logging in:** every endpoint except the three marked *open* needs `Authorization: Bearer <token>`, where the token comes from `/api/auth/register` or `/api/auth/login`. Without it the answer is `401` with `WWW-Authenticate: Bearer`. (With `AUTH_MODE=none`, for one person on their own machine, a browser names its own workspace in an `X-Workspace-Id` header instead.)
- **Errors:** the body is `{"detail": "…"}`; for a validation failure `detail` is a list of `{loc, msg, type}`. Submitted values are never echoed back.
- **Status codes:** `400` bad request, `401` not logged in, `403` refused (for example a host that is not allowed), `404` not found or not yours, `408` the query ran too long, `409` a conflict (name taken, connection limit), `422` invalid input or SQL that failed validation, `429` too many requests (see `Retry-After`), `503` the database or the language model could not be reached.
- **Privacy:** everything belongs to the logged-in user; another user's data source, conversation or definition is reported as `404`.

## Request limits

| Limit | Applies to | Default (per user, per minute) | Setting |
|---|---|---|---|
| General | every endpoint that needs a login | 300 | `RATE_LIMIT_PER_MINUTE` |
| Language model | endpoints marked *model* | 20, shared by them (a question and its written summary count once each) | `RATE_LIMIT_LLM_PER_MINUTE` |
| Database probing | endpoints marked *probe* | 10, shared by them; refused hosts count | `RATE_LIMIT_DB_PROBE_PER_MINUTE` |
| Failed logins | login and password change | 5 per username and 20 per address, then locked out for 15 minutes | `LOGIN_MAX_FAILURES` |
| New accounts | registration | 10 per address per hour | `REGISTER_PER_HOUR_PER_IP` |

A workspace may also keep at most 10 external databases connected (`MAX_CONNECTIONS_PER_WORKSPACE`). A limit of `0` switches that limit off.

## Accounts

Registering, logging in and out, and changing a password.

| Endpoint | What it does | Needs | Sends | Returns | Notes |
|---|---|---|---|---|---|
| `GET /api/auth/config` | What the login screen needs to know before anyone is logged in. | nothing | — | AuthConfig | *open* |
| `POST /api/auth/login` | Log in with a username and password. | nothing | Credentials | Session | *open* |
| `POST /api/auth/logout` | End this session only; the same user's other sessions stay logged in. | login | — | nothing (204) | — |
| `GET /api/auth/me` | The logged-in user. | login | — | UserOut | — |
| `POST /api/auth/password` | Change your password. | login | PasswordChange | nothing (204) | — |
| `POST /api/auth/register` | Create an account and log in to it. | nothing | Credentials | Session | *open* |

## Data sources

The sample database, uploaded files and connected databases.

| Endpoint | What it does | Needs | Sends | Returns | Notes |
|---|---|---|---|---|---|
| `GET /api/datasources` | Everything the user can ask about: the shared sample, their uploads and their connected databases. | login | — | list of DataSource | — |
| `GET /api/datasources/{datasource_id}` | One data source with its tables and columns. | login | — | DataSource | — |
| `DELETE /api/datasources/{datasource_id}` | Delete an upload or disconnect a database, with its conversations, definitions and blocked columns. | login | — | nothing (204) | — |
| `POST /api/datasources/{datasource_id}/refresh` | Re-read the tables of an external database. | login | — | DataSource | — |
| `GET /api/datasources/{datasource_id}/tables/{table}/preview` | The first rows of a table or view, leaving out blocked columns. | login | — | TablePreview | query: limit? |

## Uploading files

Importing CSV and Excel files in two steps: preview, then import.

| Endpoint | What it does | Needs | Sends | Returns | Notes |
|---|---|---|---|---|---|
| `POST /api/upload/import` | Import selected sheets of a previously previewed upload into a data source. | login | ImportRequest | DataSource | — |
| `POST /api/upload/preview` | Stage a CSV/Excel file and return detected sheets, columns, types and a data preview. | login | a file upload | UploadPreview | — |

## Connecting a database

Testing and saving a connection to an external database.

| Endpoint | What it does | Needs | Sends | Returns | Notes |
|---|---|---|---|---|---|
| `POST /api/database/connect` | Connect a PostgreSQL database. | login | ConnectRequest | ConnectResponse | *probe* |
| `POST /api/database/test` | Check a PostgreSQL connection and list what the account can see. | login | ConnectRequest | ConnectionReport | *probe* |

## Schema and relationships

Tables, columns, keys and the relationships used for joins.

| Endpoint | What it does | Needs | Sends | Returns | Notes |
|---|---|---|---|---|---|
| `POST /api/datasources/{datasource_id}/join-path` | How to join the given tables using declared or confirmed relationships. | login | JoinPathRequest | JoinPlan | — |
| `GET /api/datasources/{datasource_id}/relationships` | The relationships used for joins: declared in the database or confirmed by the user. | login | — | list of Relationship | — |
| `POST /api/datasources/{datasource_id}/relationships` | Confirm a suggested relationship so questions may join through it. | login | RelationshipCreate | Relationship | — |
| `GET /api/datasources/{datasource_id}/relationships/suggestions` | Likely relationships found by comparing column names and values. | login | — | list of RelationshipSuggestion | — |
| `DELETE /api/datasources/{datasource_id}/relationships/{relationship_id}` | Remove a relationship the user confirmed. | login | — | nothing (204) | — |
| `GET /api/datasources/{datasource_id}/schema` | Tables, columns, keys and the relationships usable for joins. | login | — | SchemaInfo | — |

## Blocked columns

Columns and tables questions may never read, per data source.

| Endpoint | What it does | Needs | Sends | Returns | Notes |
|---|---|---|---|---|---|
| `GET /api/datasources/{datasource_id}/blocked-columns` | The columns and tables blocked for this user on a data source, and those the administrator blocked for everyone. | login | — | BlockedColumns | — |
| `PUT /api/datasources/{datasource_id}/blocked-columns` | Replace the columns (`table.column`) and tables (`table.*`) that questions may never read. | login | BlockedColumnsUpdate | BlockedColumns | — |

## Business definitions

Metrics, terms and notes that questions are answered with.

| Endpoint | What it does | Needs | Sends | Returns | Notes |
|---|---|---|---|---|---|
| `GET /api/context/{datasource_id}` | The business definitions and notes for a data source. | login | — | list of ContextEntry | — |
| `POST /api/context/{datasource_id}` | Add a definition. | login | ContextEntryIn | ContextEntry | — |
| `POST /api/context/{datasource_id}/examples` | Add example definitions (revenue, profit, ...) to the sample database, to see how definitions work. | login | — | ContextImportResult | — |
| `POST /api/context/{datasource_id}/import` | Save previewed definitions. | login | ContextImportRequest | ContextImportResult | — |
| `POST /api/context/{datasource_id}/import/preview` | Read a glossary or data dictionary (.csv, .json, .md, .txt) and show what would be imported. | login | a file upload | ContextImportPreview | — |
| `PUT /api/context/{datasource_id}/{entry_id}` | Change a definition. | login | ContextEntryIn | ContextEntry | — |
| `DELETE /api/context/{datasource_id}/{entry_id}` | Delete a definition. | login | — | nothing (204) | — |

## SQL

Generating, validating and running SQL for a question.

| Endpoint | What it does | Needs | Sends | Returns | Notes |
|---|---|---|---|---|---|
| `POST /api/sql/execute` | Run a read-only query. | login | SqlRequest | ExecuteResponse | — |
| `POST /api/sql/generate` | Turn a question into a proposed SQL query. | login | GenerateRequest | GenerateResponse | *model* |
| `POST /api/sql/query` | Question -> SQL -> validation (with bounded correction) -> read-only execution. | login | GenerateRequest | QueryResponse | *model* |
| `POST /api/sql/validate` | Check a SQL statement against the safety rules and the data source's schema. | login | SqlRequest | ValidationResult | — |

## Chat

Questions, follow-ups and conversations.

| Endpoint | What it does | Needs | Sends | Returns | Notes |
|---|---|---|---|---|---|
| `GET /api/chat/conversations` | The user's conversations, newest first, optionally for one data source. | login | — | list of ConversationSummary | query: datasource_id? |
| `GET /api/chat/conversations/{conversation_id}` | A conversation with every question in it and how each was answered. | login | — | ConversationDetail | — |
| `DELETE /api/chat/conversations/{conversation_id}` | Delete a conversation and any results kept for it. | login | — | nothing (204) | — |
| `POST /api/chat/query` | Ask a question, or answer a clarification / ask a follow-up by passing `conversation_id`. | login | ChatRequest | ChatResponse | *model* |

## Query history

Past questions, with search, deletion and optionally kept results.

| Endpoint | What it does | Needs | Sends | Returns | Notes |
|---|---|---|---|---|---|
| `GET /api/history` | Questions asked about a data source, newest first. | login | — | HistoryPage | query: datasource_id, q?, status?, limit?, offset? |
| `DELETE /api/history` | Delete every conversation about a data source, including any rows that were kept. | login | — | nothing (204) | query: datasource_id |
| `DELETE /api/history/turns/{turn_id}` | Delete one question and any result kept for it. | login | — | nothing (204) | — |
| `GET /api/history/turns/{turn_id}/result` | The rows kept for an answer. | login | — | QueryResult | — |

## Charts and summaries

Turning a result into a chart and a checked written summary.

| Endpoint | What it does | Needs | Sends | Returns | Notes |
|---|---|---|---|---|---|
| `POST /api/analytics/chart` | Profile a result and choose a chart for it. | login | ChartRequest | ResultAnalysis | — |
| `POST /api/analytics/summary` | A natural-language summary of a result, checked so that it quotes no figure the data lacks. | login | SummaryRequest | SummaryResponse | *model* |

`?` after a query parameter marks it optional.
