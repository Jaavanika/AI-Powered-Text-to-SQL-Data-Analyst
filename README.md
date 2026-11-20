# AI-Powered Text-to-SQL Data Analyst
### A conversational data analytics platform with schema-aware SQL generation, validation, and automatic visualization

> **Status.** This README began as the design for the project. The application is now built, and the parts that
> describe how it really works are marked *as built*. For the details see [docs/architecture.md](docs/architecture.md),
> [docs/api_documentation.md](docs/api_documentation.md), the stage-by-stage record in
> [docs/implementation_plan.md](docs/implementation_plan.md), and the measured answer quality in
> [docs/evaluation_report.md](docs/evaluation_report.md). Database support today is **SQLite (uploads and the sample)
> and PostgreSQL**. MySQL is not supported in this version; see "Extending" in
> [docs/architecture.md](docs/architecture.md) for how a new database is added.

## 1. Project Overview

The **AI-Powered Text-to-SQL Data Analyst** is an AI-assisted analytics platform that allows users to ask questions about structured data in natural language instead of writing SQL manually.

Users can provide data in two primary ways:

1. **Connect an existing database** such as PostgreSQL (MySQL is not supported in this version).
2. **Upload a dataset** such as CSV or Excel, which the application imports into a database for analysis.

After data is made available, users can ask questions such as:

- Which products generated the highest revenue?
- Show monthly sales for 2025.
- Which customers made the most purchases?
- Compare revenue across regions.
- What are the top five product categories by quantity sold?

The application interprets the question, retrieves relevant schema and business context, identifies table relationships, generates SQL with an LLM, validates the query, and executes it through a restricted read-only database connection. It then presents the results as tables, charts, and natural-language explanations.

The goal is not simply to convert text into SQL. It is to build a **reliable, context-aware, conversational data analyst** that helps users explore their data while reducing common SQL-generation errors and preventing unsafe database operations.

---

## 2. Problem Statement

Traditional database analysis requires users to understand SQL, database schemas, column names, table relationships, and query-writing techniques. This becomes difficult when a database contains many interconnected tables.

A general-purpose LLM can generate SQL from a natural-language prompt, but a basic prompt-to-SQL workflow may:

- Guess table or column names incorrectly.
- Miss important relationships between tables.
- Generate incorrect or incomplete joins.
- Misinterpret business terms such as revenue, profit, or active customer.
- Produce syntactically invalid or logically incorrect SQL.
- Generate database-modifying statements.
- Return results without explaining their meaning.
- Require users to manually create charts from query results.

This project addresses these limitations through schema linking, relationship-aware query generation, business-context retrieval, SQL validation, read-only execution, result analysis, and automatic visualization.

---

## 3. Project Objectives

1. Let users explore data using natural-language questions.
2. Support both existing database connections and CSV/Excel uploads.
3. Extract database schemas and understand table relationships.
4. Identify relevant tables and columns for each question.
5. Use an LLM to generate context-aware SQL.
6. Detect ambiguous questions and request clarification when needed.
7. Validate SQL syntax, schema references, and query safety before execution.
8. Enforce read-only access to connected databases.
9. Handle analytical queries involving multiple tables and joins.
10. Analyze returned data and generate appropriate visualizations.
11. Support conversational follow-up questions.
12. Explain generated SQL and summarize query results.
13. Maintain query history for review and reuse.

---

## 4. Supported Input Methods

The application provides multiple ways for users to supply data and analytical context.

### 4.1 Connect an Existing Database

Users can connect an existing database instead of manually uploading individual files.

**Initial supported database systems:**
- PostgreSQL
- MySQL (not supported in this version)

**Connection fields:**
- Database type
- Host
- Port
- Database name
- Username
- Password

The backend tests the connection and retrieves permitted metadata, including table names, columns, data types, primary keys, and foreign keys.

**Important security requirements:**
- Database credentials must be stored and handled on the backend, never exposed to the frontend.
- Use a dedicated database account with read-only permissions.
- Keep credentials out of source control and use environment variables or a secrets manager.
- Restrict connections to approved database hosts where possible.
- Do not expose raw connection strings in logs or error messages.

### 4.2 Upload CSV Files

Users can upload CSV files containing structured data.

Example:
- `customers.csv`
- `orders.csv`
- `products.csv`
- `sales.csv`

The application reads the file, checks its structure, identifies column names and data types, and imports the data into a managed database table.

The user should be able to review the detected table name, columns, data types, and a preview of the records before analysis.

### 4.3 Upload Excel Files

Users can upload Excel workbooks in `.xlsx` or `.xls` format.

For workbooks with multiple sheets, the application should allow users to select which sheets to import. Each selected sheet can be represented as a separate table, subject to validation and naming rules.

The import process should handle:
- Empty rows and columns.
- Missing values.
- Duplicate or blank column names.
- Inconsistent data types.
- Sheet and table naming.
- File-size and row-count limits.

CSV and Excel uploads should be imported into database tables so that uploaded datasets use the same SQL generation, validation, and execution pipeline as connected databases.

### 4.4 Provide Schema Documentation and Business Definitions

Users may optionally provide supporting documentation to explain their data.

Examples:
- Table and column descriptions.
- Data dictionaries.
- Database documentation.
- Business definitions.
- Metric definitions.
- Relationship notes.

For example, a business may define:
- **Revenue:** total quantity multiplied by selling price.
- **Profit:** revenue minus cost.
- **Active customer:** a customer with at least one purchase during the selected period.

This context helps the system interpret business terminology consistently rather than allowing the LLM to invent definitions.

Documentation can be processed and indexed for retrieval. Sensitive documents should be subject to the same access controls as the associated data.

### 4.5 Natural-Language Questions

Once a data source is available, users can type questions in ordinary language.

Examples:
- "Show total sales by month."
- "Which five customers spent the most?"
- "Compare revenue across regions in 2025."
- "Show products that have never been ordered."
- "What is the average order value for each customer?"

The application uses the question, relevant schema, relationships, business definitions, and conversation context to prepare a query.

### 4.6 Analytical Preferences and Filters

Users can optionally specify how they want results presented.

Examples:
- Date range.
- Region or category.
- Top-N limit.
- Preferred chart type.
- Sort order.
- Grouping preference.

Filters must be validated and safely incorporated into the generated query. User-selected preferences should not override database permissions or query-safety rules.

---

## 5. Main Features

### 5.1 Natural-Language-to-SQL Generation

Users ask questions without needing to know SQL.

**Example question:**

"Show the total revenue generated by each product."

Possible SQL output:

```sql
SELECT
    p.product_name,
    SUM(o.quantity * p.price) AS total_revenue
FROM orders AS o
JOIN products AS p
    ON o.product_id = p.product_id
GROUP BY p.product_name
ORDER BY total_revenue DESC;
```

The generated query is displayed with an explanation before or alongside execution, according to the application's interaction settings.

### 5.2 Intelligent Schema Linking

Schema linking identifies which tables, columns, and relationships are relevant to a user's question.

For example, consider these tables:

**Customers**
- customer_id
- customer_name
- email

**Orders**
- order_id
- customer_id
- product_id
- order_date
- quantity

**Products**
- product_id
- product_name
- price

For the question "Which customers spent the most money?", the system identifies the relevant customer, order, and product fields instead of sending an unnecessarily large schema to the LLM.

Benefits:
- Reduces irrelevant schema context.
- Helps resolve table and column references.
- Improves query generation for larger databases.
- Reduces errors caused by guessed identifiers.

Schema retrieval can use database metadata, keyword or semantic matching, and optional vector-based retrieval. A vector database is not mandatory for the initial implementation.

### 5.3 Database Relationship Mapping

The system extracts and uses:
- Primary keys.
- Foreign keys.
- One-to-one relationships.
- One-to-many relationships.
- Many-to-many relationships, including junction tables where applicable.

For example:

`Customers → Orders → Products`

If a question requires information from all three tables, the system uses the known relationships to identify a valid join path.

If multiple possible join paths exist, or the relationship cannot be established confidently, the system should ask for clarification or report that it cannot determine a safe join. It should not silently invent a relationship.

### 5.4 Context-Aware SQL Generation

The LLM receives relevant context, such as:
- User question.
- Relevant table and column definitions.
- Primary and foreign keys.
- Valid join paths.
- Database SQL dialect.
- Business definitions and metric rules.
- Relevant conversation history.
- User-selected filters and output preferences.

This is more reliable than asking an LLM to generate SQL without providing the database context.

### 5.5 Ambiguous Question Detection

The system identifies questions that have multiple plausible interpretations and asks the user to clarify before generating a query.

Example:

**User:** "Show me the best-performing product."

Possible meanings:
- Highest revenue.
- Highest quantity sold.
- Highest profit.
- Highest customer rating.

The system asks which definition the user intends, or offers the available metric definitions from the supplied business documentation.

This prevents the application from silently making an unsupported assumption.

### 5.6 SQL Validation and Safety

Every generated query must pass validation before execution.

**Syntax validation**
- Parse SQL using a SQL parser.
- Check that the query is valid for the selected database dialect.

**Schema validation**
- Verify referenced tables and columns against the accessible schema.
- Check join references and known relationships.
- Reject unknown or unauthorized objects.

**Read-only validation**
- Allow only permitted read operations.
- Reject INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, and other data-definition or data-modification statements.
- Reject multiple statements.
- Apply an explicit allowlist of supported SQL constructs.

**Execution controls**
- Use a read-only database account.
- Apply statement timeouts.
- Apply row and resource limits.
- Restrict access to sensitive tables and columns.
- Avoid unrestricted access to system schemas.
- Use database permissions as the final security boundary.

Keyword filtering alone is not sufficient. SQL must be parsed and checked structurally, and the database connection itself must have restricted permissions.

### 5.7 Error Detection and Query Correction

If validation or execution fails, the system can send a controlled error description and relevant schema context back to the LLM for correction.

The correction workflow should:
1. Capture the validation or execution error.
2. Remove secrets and sensitive values from error details.
3. Provide the relevant schema and error context to the LLM.
4. Generate a revised query.
5. Validate the revised query from the beginning.
6. Execute only after it passes all checks.

Use a limited retry count. If the query still fails, explain the issue rather than retrying indefinitely.

### 5.8 Safe Query Execution

Only approved queries are sent to the database.

The execution module:
- Uses restricted read-only credentials.
- Executes the validated statement.
- Enforces time and result limits.
- Returns structured results.
- Handles database errors safely.

The application must never execute raw LLM output directly.

### 5.9 Automatic Result Analysis

After execution, the system examines the returned dataset.

It can identify:
- Record count.
- Column names and data types.
- Numerical and categorical fields.
- Highest and lowest values.
- Trends and comparisons.
- Relevant distributions.
- Potentially useful patterns.

The LLM can then produce a concise explanation grounded in the actual query results. It must not invent figures or conclusions that are absent from the returned data.

### 5.10 Automatic Chart Generation

The application selects a visualization based on the user's question and the structure of the returned data.

Supported visualizations:
- Bar chart: category comparisons.
- Line chart: time-based trends.
- Pie or donut chart: proportional distributions with a suitable number of categories.
- Scatter plot: relationships between numerical variables.
- Table: detailed records.
- KPI cards: individual summary metrics.

Users can switch between chart and table views and, where supported, customize chart options.

### 5.11 Conversational Follow-Up Questions

The application maintains relevant conversation context so users can refine an earlier analysis.

Example:

**User:** Show total sales by region.

**System:** Returns a regional sales chart.

**User:** Now show only the top five regions.

**System:** Uses the previous analytical context and generates a revised query.

**User:** Compare those regions by month.

**System:** Generates a monthly comparison.

Every follow-up query must still go through schema retrieval, validation, and safe execution. Previous SQL must not be modified or executed without revalidation.

### 5.12 Visual Database Schema Explorer

A schema explorer displays:
- Available tables.
- Column names and data types.
- Primary keys.
- Foreign keys.
- Relationships.
- Table dependencies.

This helps users understand their data and makes the application's join reasoning easier to inspect.

### 5.13 SQL Explanation

The application explains generated SQL in understandable language.

For example:
- `SELECT` retrieves the requested fields.
- `JOIN` connects related tables.
- `GROUP BY` groups records.
- `SUM` calculates totals.
- `ORDER BY` sorts the results.

This makes the application useful for users who want to learn SQL as well as analyze data.

### 5.14 Query History

Users can review:
- Previous questions.
- Generated SQL.
- Execution status.
- Query explanations.
- Previous results and visualizations, when retained.

History should respect user access controls and data-retention settings. Sensitive query parameters should not be stored unnecessarily.

---

## 6. End-to-End System Workflow

The application uses a common analytics pipeline regardless of whether the user connects a database or uploads a file.

```text
                  USER
                    |
                    v
          SELECT DATA INPUT METHOD
                    |
          +---------+----------+
          |                    |
          v                    v
   CONNECT DATABASE       UPLOAD CSV / EXCEL
          |                    |
          v                    v
   Test Connection       Validate File
          |                    |
          v                    v
   Extract Metadata      Import as DB Tables
          |                    |
          +---------+----------+
                    |
                    v
          SCHEMA AND RELATIONSHIP
                 EXTRACTION
                    |
                    v
          OPTIONAL DOCUMENTATION
              AND RAG RETRIEVAL
                    |
                    v
          NATURAL LANGUAGE QUESTION
                    |
                    v
           QUESTION UNDERSTANDING
                    |
                    v
          AMBIGUITY / INTENT CHECK
                    |
             +------+------+
             |             |
          Unclear         Clear
             |             |
             v             v
       Ask User       SCHEMA LINKING
       to Clarify          |
                           v
                  RELATIONSHIP MAPPING
                           |
                           v
                   LLM SQL GENERATION
                           |
                           v
                    SQL VALIDATION
                           |
                    +------+------+
                    |             |
                  Invalid        Valid
                    |             |
                    v             v
              Controlled     SAFE QUERY
              Correction     EXECUTION
                    |             |
                    +-- Recheck   v
                              QUERY RESULTS
                                   |
                                   v
                              RESULT ANALYSIS
                                   |
                                   v
                           CHART / TABLE GENERATION
                                   |
                                   v
                           NATURAL LANGUAGE SUMMARY
                                   |
                                   v
                           INTERACTIVE DASHBOARD
```

---

## 7. Role of LLM, RAG, and Agents

### Large Language Model (LLM)

The LLM is responsible for:
- Understanding natural-language questions.
- Identifying analytical intent.
- Generating SQL using supplied context.
- Correcting queries based on controlled error feedback.
- Explaining SQL.
- Summarizing query results.

### Retrieval-Augmented Generation (RAG)

RAG retrieves relevant context and supplies it to the LLM.

Possible retrieval sources:
- Table and column descriptions.
- Database documentation.
- Business definitions.
- Metric definitions.
- Data dictionaries.
- Relationship notes.

For an initial version, database metadata can be retrieved directly using SQLAlchemy or database catalog queries. Vector-based RAG is most useful when the project includes substantial documentation or business terminology.

### Agent / Workflow Orchestration

The agent coordinates the application workflow:

1. Receive the question.
2. Retrieve relevant schema and documentation.
3. Identify relationships.
4. Check whether clarification is required.
5. Request SQL generation.
6. Validate the query.
7. Request a correction when appropriate.
8. Execute the approved query.
9. Analyze results.
10. Generate visualizations and explanations.

The agent coordinates tools and services; it does not replace SQL validation or database security.

### Supporting Components

| Component | Responsibility |
|---|---|
| SQL parser | Parses SQL and checks its structure |
| Schema service | Retrieves tables, columns, and keys |
| Relationship service | Identifies valid join paths |
| Database engine | Executes approved queries |
| Pandas | Processes returned datasets |
| Visualization library | Creates charts |
| Conversation manager | Maintains relevant follow-up context |

---

## 8. Suggested Technology Stack

| Layer | Technology |
|---|---|
| Frontend | React.js |
| Backend | Python |
| API framework | FastAPI |
| LLM | OpenAI API or another compatible LLM |
| Databases | PostgreSQL (MySQL not supported in this version) |
| Database connectivity | SQLAlchemy |
| Schema inspection | SQLAlchemy Inspector / database metadata |
| SQL parsing and validation | SQLGlot |
| Data processing | Pandas |
| RAG framework | LangChain (optional) |
| Vector database | FAISS / ChromaDB (optional) |
| Charts | Plotly / Recharts |
| Excel support | openpyxl / pandas |
| CSV support | Python / pandas |
| Version control | Git and GitHub |

**As built:** React 19 with Vite and Recharts (no router library); FastAPI on Python; SQLAlchemy and SQLGlot; pandas and
openpyxl for imports; Azure OpenAI as the model; PostgreSQL for connected databases and SQLite for uploads and the sample.
RAG and agent frameworks were not needed: business definitions are matched by words, and the workflow is ordinary Python services.

The stack can be adjusted as implementation progresses. RAG frameworks and agent frameworks are optional; the core workflow can also be implemented with ordinary Python services.

---

## 9. Folder Structure (as built)

```text
.
├── frontend/                      React single-page app
│   └── src/
│       ├── App.jsx, AuthContext.jsx, AppContext.jsx
│       ├── pages/                 AnalyticsWorkspace (Ask), History, Definitions, DataSources, SchemaExplorer, Login, Account
│       ├── components/            ChatInterface, AnswerCard, ResultPanel, ChartViewer, ResultTable, SQLViewer, QueryHistory,
│       │                          FileUpload, ConnectionForm, SchemaDiagram, DataSourceSelector, ThemeToggle
│       ├── hooks/useChat.js
│       ├── lib/                   formatting, suggestions, diagram layout, saved results, session token
│       └── services/api.js       the only place that calls the backend
│
├── backend/
│   ├── app/
│   │   ├── main.py                the app, error handlers, security headers
│   │   ├── config.py, schemas.py  settings and every request/response model
│   │   ├── api/                   thin HTTP routers: auth, datasources, upload, database, schema, blocked, context, sql, chat, history, analytics
│   │   ├── core/                  identity and limits, host policy, encryption, log redaction, SQLite helpers
│   │   ├── services/              the behaviour: analyst_workflow, schema_linking, ambiguity_service, sql_generation,
│   │   │                          sql_validation, query_execution, connectors, postgres_connector, result_analysis,
│   │   │                          visualization, context_service, blocked_service, conversation_service, registry, ...
│   │   └── devtools/              development helpers (PostgreSQL sample loader)
│   ├── evaluation/                the evaluation suite (cases, scoring, runner, metrics, report)
│   ├── scripts/                   dev_postgres.py, evaluate.py, export_api_docs.py
│   ├── tests/                     the test suite
│   ├── requirements.txt, requirements-dev.txt
│   └── .env.example
│
├── sample_database/               sample_schema.sql
├── docs/                          architecture, API documentation, implementation plan, evaluation report
├── data/                          created at run time: registry, uploads, sample database (not committed)
└── README.md
```

See [docs/architecture.md](docs/architecture.md) for what each part does.

---

## 10. Sample Database Design

For the initial implementation, use a business database with interconnected tables.

### Customers

| Column | Description |
|---|---|
| customer_id | Unique customer identifier |
| customer_name | Customer name |
| email | Customer email |
| city | Customer location |

### Products

| Column | Description |
|---|---|
| product_id | Unique product identifier |
| product_name | Product name |
| category | Product category |
| price | Product selling price |

### Orders

| Column | Description |
|---|---|
| order_id | Unique order identifier |
| customer_id | Customer reference |
| product_id | Product reference |
| quantity | Number of products |
| order_date | Purchase date |

### Relationships

- `Customers.customer_id` → `Orders.customer_id`
- `Products.product_id` → `Orders.product_id`

Additional tables such as `Payments`, `Regions`, `Suppliers`, and `Order_Items` can be added to test more complex joins and analytical questions.

For realistic testing, the sample database should include multiple records, different dates, categories, regions, and edge cases.

---

## 11. Example End-to-End Scenarios

### Scenario A: Existing Database

**Input method:** User connects a PostgreSQL database.

**Question:** "Which product generated the highest revenue in 2025?"

The system:
1. Tests the database connection.
2. Retrieves accessible schema metadata.
3. Identifies relevant tables and relationships.
4. Generates SQL using the database dialect.
5. Validates the SQL.
6. Executes it using read-only credentials.
7. Analyzes the returned result.
8. Displays the product, revenue, and a suitable visualization.

### Scenario B: CSV Upload

**Input method:** User uploads `sales.csv`.

Example columns:
- order_id
- product_name
- quantity
- unit_price
- order_date
- region

The system:
1. Validates the uploaded file.
2. Detects columns and data types.
3. Displays a preview.
4. Imports the data into a managed database table.
5. Extracts the imported table's schema.
6. Accepts natural-language questions through the same analytics pipeline.

**Question:** "Show monthly revenue by region."

The system generates and validates SQL, executes it, and displays a monthly comparison chart.

### Scenario C: Excel Workbook with Multiple Sheets

**Input method:** User uploads `business_data.xlsx`.

The workbook contains:
- Customers sheet.
- Orders sheet.
- Products sheet.

The system:
1. Lists available sheets.
2. Lets the user select sheets for import.
3. Imports selected sheets as separate tables.
4. Extracts their columns and data types.
5. Uses detected or user-confirmed relationships.
6. Allows questions involving multiple imported tables.

If relationships cannot be determined from reliable metadata or user-provided information, the application asks the user to define them instead of guessing.

### Scenario D: Ambiguous Question

**User:** "Show the best-performing product."

**System:** "How would you like to measure product performance?"

Possible interpretations:
- Highest revenue.
- Highest quantity sold.
- Highest profit.
- Highest rating.

After the user clarifies, the application generates and validates the corresponding query.

### Scenario E: Conversational Analysis

**User:** "Show monthly sales."

**System:** Returns a monthly sales chart.

**User:** "Only show 2025."

**System:** Applies the year filter and regenerates the query.

**User:** "Compare it with 2024."

**System:** Produces a year-over-year comparison, provided the required data is available.

---

## 12. Proposed User Interface

The application should contain the following screens.

### A. Data Source Selection

Users choose between:
- Connect an existing database.
- Upload a CSV file.
- Upload an Excel workbook.

### B. Database Connection Screen

Fields:
- Database type.
- Host and port.
- Database name.
- Username and password.
- Test connection action.

### C. File Upload Screen

Features:
- CSV/Excel upload.
- File validation.
- Sheet selection for Excel.
- Data preview.
- Column and data-type review.
- Import confirmation.

### D. Schema Explorer

Displays:
- Tables.
- Columns.
- Data types.
- Primary keys.
- Foreign keys.
- Table relationships.

### E. AI Analytics Workspace

Displays:
- Natural-language question input.
- Suggested questions.
- Conversation history.
- Generated SQL.
- SQL explanation.
- Validation status.
- Query results.
- Automatic charts.
- Natural-language result summary.

### F. Query History

Displays previous questions, SQL, execution status, and saved results according to the application's retention settings.

---

## 13. API Design (as built)

The backend exposes a JSON API under `/api`, documented endpoint by endpoint in
[docs/api_documentation.md](docs/api_documentation.md) (generated from the code, so it cannot drift) and served interactively at
`/docs` while the backend runs. In outline:

| Area | Endpoints |
|---|---|
| Accounts | `POST /api/auth/register`, `/login`, `/logout`, `/password`; `GET /api/auth/me`, `/config` |
| Data sources | `GET /api/datasources`; `POST /api/upload/preview` then `/import`; `POST /api/database/test` and `/connect`; `POST /api/datasources/{id}/refresh` |
| Schema | `GET /api/datasources/{id}/schema`, `/relationships`, `/relationships/suggestions`; blocked columns under `/blocked-columns` |
| Questions | `POST /api/chat/query`; conversations under `/api/chat/conversations`; `POST /api/sql/validate`, `/execute`, `/query` |
| History and definitions | `GET /api/history`; `/api/context/{datasource_id}` |
| Results | `POST /api/analytics/chart`, `/summary` |

Every endpoint except registration, login and the login screen's configuration needs a login, enforces per-user limits, and
only sees that user's data. The frontend cannot bypass validation: the only way to run SQL is `POST /api/sql/execute` or a
question, and both validate first and run read-only.

---

## 14. Security and Data Protection

Database and uploaded data can contain sensitive information. Security must be designed into the system.

1. Use dedicated read-only credentials for external databases.
2. Never expose database credentials or API keys in frontend code.
3. Reject data-modifying and schema-modifying SQL.
4. Parse SQL and enforce an allowlist of permitted query structures.
5. Reject multiple statements.
6. Restrict accessible schemas, tables, and columns.
7. Apply query timeouts and result-size limits.
8. Use parameterized values where applicable.
9. Validate uploaded files and enforce file-size and row-count limits.
10. Generate safe table and column identifiers during file import.
11. Isolate uploaded datasets by user or workspace.
12. Avoid logging passwords, secrets, or sensitive result data.
13. Apply authentication and authorization to data sources and query history.
14. Treat uploaded documents and database content as untrusted data, not as instructions to the LLM.
15. Use database-level permissions as the final protection against unauthorized changes.

Read-only access does not automatically prevent data exposure. Access controls must also protect sensitive records and columns.

### How this is implemented (as built)

| # | Requirement | How |
|---|---|---|
| 1, 15 | Read-only credentials; the database as the final protection | Connected accounts are checked (superusers refused, write access warned about); sessions are read-only and locked; SQLite is opened read-only with an authorizer |
| 2, 12 | No secrets in the frontend or logs | Passwords and the model key stay in the backend; log records are redacted when created (tokens, passwords, keys, query strings) |
| 3-5 | Reject modifying SQL and multiple statements; allowlist structures | SQLGlot parse: one `SELECT`, per-dialect function and construct allowlists |
| 6 | Restrict tables and columns | Unknown tables and columns are rejected; blocked columns and tables per data source (and administrator-wide) |
| 7 | Timeouts and result limits | Query time limit and row limit, on every query |
| 9, 10 | Validate uploads; safe identifiers | Size, row and type limits; generated identifiers |
| 11, 13 | Isolation; authentication; authorization | Accounts with scrypt hashes and expiring sessions; every query scoped to the user's workspace; a test fails if any endpoint skips the login |
| 14 | Treat data as untrusted | Data in prompts is flattened text with a rule not to follow it, and the output must still pass validation |

Also: login lockouts, per-user rate limits (stricter for model calls and for testing database connections), a limit on connected
databases per user, an allowlist of database hosts with the connection pinned to the checked address, encrypted stored passwords,
and no-cache and anti-framing response headers. The known limits are listed in [docs/implementation_plan.md](docs/implementation_plan.md).

---

## 15. Testing and Evaluation

The application should be evaluated using a range of questions and database structures.

| Test category | Example |
|---|---|
| Simple selection | Show all customers |
| Filtering | Show orders from 2025 |
| Aggregation | Calculate total revenue |
| Sorting | Show the top five products |
| Single join | Show customer names with orders |
| Multiple joins | Calculate spending by customer and product |
| Time analysis | Show monthly sales trends |
| Ambiguity | Show the best-performing product |
| Invalid schema | Reference a nonexistent column |
| Unsafe SQL | Attempt to delete customer records |
| Uploaded data | Analyze an imported CSV |
| Multi-sheet data | Join related Excel sheets |

### Evaluation Metrics

- Schema linking accuracy.
- Table and column identification accuracy.
- Join correctness.
- SQL execution accuracy.
- Result correctness against expected answers.
- SQL validation success rate.
- Unsafe query rejection rate.
- Ambiguity detection quality.
- Query execution time.
- Visualization suitability.

A successful execution alone does not prove that a query is logically correct. Results should be compared with expected outputs using a representative test dataset.

---

## 16. Development Roadmap

Build the project in phases rather than implementing every feature simultaneously.

### Phase 1: Data Input and Database Foundation
- Set up the FastAPI backend.
- Connect PostgreSQL.
- Extract table and column metadata.
- Build the database connection interface.
- Add CSV and Excel upload.
- Import uploaded data into database tables.

### Phase 2: Schema Understanding
- Extract primary and foreign keys.
- Build relationship mapping.
- Implement relevant schema retrieval.
- Add the visual schema explorer.

### Phase 3: Text-to-SQL
- Integrate an LLM.
- Generate SQL using retrieved schema context.
- Support the selected database dialect.
- Display generated SQL and explanations.

### Phase 4: Validation and Safe Execution
- Integrate SQLGlot.
- Validate SQL syntax and query structure.
- Enforce read-only restrictions.
- Add row limits and timeouts.
- Implement controlled error correction.

### Phase 5: Analytics and Visualization
- Process query results using Pandas.
- Generate charts using Plotly or Recharts.
- Display result tables.
- Generate natural-language summaries.

### Phase 6: Intelligent Interaction
- Add ambiguity detection.
- Add conversational follow-ups.
- Retrieve business definitions and documentation.
- Add optional RAG-based retrieval.
- Implement query history.

### Phase 7: Testing and Refinement
- Test simple and complex joins.
- Test invalid and unsafe queries.
- Test uploaded datasets.
- Evaluate result correctness.
- Improve error handling and user experience.

---

## 17. Project Differentiation

A basic LLM application can convert a question into SQL. The proposed system goes further by adding a structured and controlled analytics workflow.

| Capability | Basic LLM SQL Generator | Proposed Project |
|---|---|---|
| Natural-language input | Yes | Yes |
| Database connection | Not necessarily | Yes |
| CSV and Excel input | Not necessarily | Yes |
| Schema linking | Often limited or prompt-dependent | Dedicated retrieval module |
| Relationship mapping | May guess joins | Metadata-based join mapping |
| Business definitions | May be missing | Optional documentation retrieval |
| Ambiguity detection | Not guaranteed | Dedicated clarification step |
| SQL validation | Not guaranteed | Dedicated validation engine |
| Read-only execution | Not guaranteed | Restricted database credentials |
| Error correction | Prompt-dependent | Controlled correction and revalidation |
| Result analysis | Basic explanation | Structured result analysis |
| Automatic visualization | May require extra work | Integrated chart generation |
| Follow-up questions | Possible | Context-aware analytical workflow |
| Schema exploration | Not necessarily | Visual schema explorer |

The differentiation comes from the integration, reliability, safety, and evaluation of these capabilities—not merely from using an LLM, RAG, or an agent.

---

## 18. Future Enhancements

Potential future improvements include:

- Support for additional database systems.
- Advanced semantic schema retrieval.
- More sophisticated business metric definitions.
- Role-based access control.
- Query optimization recommendations.
- Predictive analytics.
- Automated report generation.
- PDF, Excel, and CSV exports.
- Scheduled analytical reports.
- Dashboard sharing.
- Voice-based data querying.
- Multi-database analytics.
- Data quality and anomaly detection.

---

## 19. Expected Outcome

The final application should allow a user to:

1. Connect an existing database or upload CSV/Excel data.
2. Explore the available schema and relationships.
3. Ask questions in natural language.
4. Receive clarification when a question is ambiguous.
5. View the SQL generated using relevant schema context.
6. See the query's validation outcome.
7. Execute only approved read-only queries.
8. View results in tables and automatically generated charts.
9. Understand the results through natural-language explanations.
10. Ask follow-up questions without restarting the analysis.

The project aims to transform traditional database querying into an accessible, conversational, and visual analytics experience.

**Final Vision:** Build an AI-powered data analyst that does more than generate SQL—it understands the available data, reasons about relationships, validates its queries, safely retrieves information, and helps users turn raw data into meaningful insights.

---

## 20. Getting Started (as built)

### Prerequisites

- **Python 3.14** (what it was developed and tested on; other recent versions are likely to work but are untested).
- **Node.js and npm** (developed on Node 24).
- **A language model.** The application is configured for an Azure OpenAI deployment: an endpoint, an API key and a deployment name.
- *Optional:* PostgreSQL, to analyse an external database. The built-in sample database and file uploads need nothing else.

### Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate            # Windows        (macOS/Linux: source venv/bin/activate)
pip install -r requirements.txt
```

Create `backend/.env` from `backend/.env.example` (do not commit it) and set at least:

```text
GENAI_PLATFORM_API_KEY=<your key>
AZURE_OPENAI_ENDPOINT=<your endpoint>
AZURE_OPENAI_API_VERSION=2025-04-01-preview
VISION_DEPLOYMENT_NAME=<your deployment name>
```

Start it from the `backend` folder:

```bash
uvicorn app.main:app --reload
```

The API is then at `http://localhost:8000` (interactive documentation at `/docs`). The sample database is created on first start in
`data/` at the project root.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The dev server forwards `/api` to the backend on port 8000.

### First use

Accounts are on by default: **create an account on the login screen**, then ask a question about the sample database. Add your own data
under **Data** (upload a CSV or Excel file, or connect a PostgreSQL database), and teach it your terms under **Definitions**.
If you are the only user and want no login, set `AUTH_MODE=none` in `.env`; that is only safe on your own machine, because anyone who
can reach the server could then read anyone's data. To stop new sign-ups once everyone has an account, set `ALLOW_REGISTRATION=false`.

### Connecting a database

Use a dedicated read-only account. The host must be on `ALLOWED_DB_HOSTS` (by default only this machine). To try the feature without a
server of your own, `python scripts/dev_postgres.py` (needs `pip install -r requirements-dev.txt`) starts a local PostgreSQL with the
sample data and prints the connection details.

### Tests and evaluation

```bash
cd backend  && pytest                    # the test suite (PostgreSQL tests use the embedded server from requirements-dev.txt)
cd frontend && npm test                  # the interface tests
cd backend  && python scripts/evaluate.py   # answer quality against the real model; writes docs/evaluation_report.md
cd backend  && python scripts/export_api_docs.py   # regenerate docs/api_documentation.md after changing an endpoint
```

### Configuration notes

- Everything is configured in `backend/.env`; each setting is explained in `backend/.env.example` (limits, accounts, history, blocked columns, hosts).
- Database passwords are encrypted before they are stored. Set `CREDENTIALS_KEY` from a secrets manager when sharing a server.
- Limits and lockouts are kept in memory, so run a single server process for exact limits.
- Do not use production credentials or sensitive production datasets while evaluating.

---

## 21. License and Contributions

A license can be selected when the project is ready for distribution.

Contributions can be organized through feature branches and pull requests. Suggested contribution areas include:
- Additional database connectors.
- Improved schema retrieval.
- SQL validation tests.
- Visualization improvements.
- Documentation and usability enhancements.
