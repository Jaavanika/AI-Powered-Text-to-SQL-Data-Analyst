from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr

LogicalType = Literal["integer", "float", "boolean", "date", "datetime", "text"]


class ColumnInfo(BaseModel):
    name: str
    original_name: str
    type: LogicalType


class TableInfo(BaseModel):
    name: str
    original_name: str
    row_count: int                 # 0 for a view: counting one runs its whole query
    columns: list[ColumnInfo]
    is_view: bool = False


class ConnectionInfo(BaseModel):
    """How an external database is reached. The password is deliberately not part of this model."""

    host: str
    port: int
    database: str
    username: str
    schema_name: str = "public"
    sslmode: str = "prefer"


class DataSource(BaseModel):
    id: str
    name: str
    kind: Literal["sample", "upload", "postgres"]
    dialect: str = "sqlite"
    read_only: bool = False
    created_at: str
    tables: list[TableInfo] = Field(default_factory=list)
    connection: ConnectionInfo | None = None


class ConnectRequest(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=63)
    username: str = Field(min_length=1, max_length=63)
    # SecretStr keeps the password out of reprs, logs and tracebacks.
    password: SecretStr = SecretStr("")
    schema_name: str = Field(default="public", min_length=1, max_length=63)
    sslmode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = "prefer"


class ConnectionReport(BaseModel):
    server_version: str
    database: str
    schema_name: str
    username: str
    tables: list[TableInfo]
    warnings: list[str] = Field(default_factory=list)


class ConnectResponse(BaseModel):
    datasource: DataSource
    warnings: list[str] = Field(default_factory=list)


class SheetPreview(BaseModel):
    sheet_name: str
    suggested_table_name: str
    row_count: int
    columns: list[ColumnInfo]
    preview_rows: list[dict[str, Any]]


class UploadPreview(BaseModel):
    upload_id: str
    filename: str
    file_type: Literal["csv", "xlsx", "xls"]
    sheets: list[SheetPreview]
    warnings: list[str] = Field(default_factory=list)


class TableSelection(BaseModel):
    sheet_name: str
    table_name: str | None = None


class ImportRequest(BaseModel):
    upload_id: str
    datasource_id: str | None = None
    datasource_name: str | None = None
    tables: list[TableSelection] = Field(default_factory=list)
    if_exists: Literal["fail", "replace"] = "fail"


class SchemaColumn(BaseModel):
    name: str
    original_name: str
    type: LogicalType
    db_type: str
    nullable: bool
    is_primary_key: bool = False


class SchemaTable(BaseModel):
    name: str
    original_name: str
    row_count: int
    columns: list[SchemaColumn]
    primary_key: list[str] = Field(default_factory=list)
    is_view: bool = False
    # True when no primary key is declared and the key was inferred from unique id-like columns.
    primary_key_inferred: bool = False
    # True for link tables that mostly consist of foreign keys to two or more tables.
    is_junction: bool = False


class Relationship(BaseModel):
    id: str
    from_table: str
    from_columns: list[str]
    to_table: str
    to_columns: list[str]
    # declared: a foreign key in the database; user: confirmed by the user.
    source: Literal["declared", "user"]
    cardinality: Literal["many_to_one", "one_to_one"]


class RelationshipSuggestion(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cardinality: Literal["many_to_one", "one_to_one"]
    match_ratio: float
    reason: str


class RelationshipCreate(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str


class ManyToMany(BaseModel):
    table_a: str
    table_b: str
    via: str


class SchemaInfo(BaseModel):
    datasource_id: str
    dialect: str
    tables: list[SchemaTable]
    relationships: list[Relationship]
    many_to_many: list[ManyToMany] = Field(default_factory=list)


class Join(BaseModel):
    """One join step, oriented from the table already in the plan (left) to the new one (right)."""

    left_table: str
    left_columns: list[str]
    right_table: str
    right_columns: list[str]
    relationship_id: str


class JoinAmbiguity(BaseModel):
    table: str
    options: list[list[Join]]


class JoinPlan(BaseModel):
    status: Literal["ok", "ambiguous", "disconnected"]
    tables: list[str]
    joins: list[Join]
    ambiguities: list[JoinAmbiguity] = Field(default_factory=list)
    unreachable: list[str] = Field(default_factory=list)
    message: str | None = None


class JoinPathRequest(BaseModel):
    tables: list[str] = Field(min_length=1)
    max_hops: int = Field(4, ge=1, le=8)


class ValueHint(BaseModel):
    """A value from the data that the question mentions (e.g. products.category = 'Electronics')."""

    table: str
    column: str
    value: str


class LinkedTable(BaseModel):
    name: str
    original_name: str
    row_count: int
    columns: list[SchemaColumn]
    primary_key: list[str] = Field(default_factory=list)
    is_view: bool = False
    matched: bool = False
    score: float = 0.0


class LinkedSchema(BaseModel):
    """The slice of the schema relevant to one question."""

    tables: list[LinkedTable]
    relationships: list[Relationship]
    join_plan: JoinPlan | None = None
    value_hints: list[ValueHint] = Field(default_factory=list)
    # True when nothing in the question matched the schema and the whole schema was used.
    fallback: bool = False


class GeneratedSQL(BaseModel):
    sql: str | None = None
    explanation: str = ""
    tables_used: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    # Set instead of `sql` when the question cannot be answered safely without more input.
    clarification: str | None = None


class GenerateRequest(BaseModel):
    datasource_id: str
    question: str = Field(min_length=1, max_length=1000)


class GenerateResponse(GeneratedSQL):
    question: str
    dialect: str
    linked: LinkedSchema


ColumnKind = Literal["numeric", "temporal", "categorical", "text", "boolean", "identifier"]


class ValueCount(BaseModel):
    value: str
    count: int


class ColumnProfile(BaseModel):
    name: str
    original_name: str
    kind: ColumnKind
    null_count: int = 0
    distinct_count: int = 0
    is_integer: bool = False
    # numeric columns
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    median: float | None = None
    sum: float | None = None
    # temporal columns (ISO text, so the first and last by sort order)
    first: str | None = None
    last: str | None = None
    top_values: list[ValueCount] = Field(default_factory=list)


class ResultProfile(BaseModel):
    row_count: int
    truncated: bool = False
    columns: list[ColumnProfile]
    # Plain-language findings computed from the data (highest, lowest, change over time, totals).
    facts: list[str] = Field(default_factory=list)


ChartType = Literal["bar", "line", "pie", "scatter", "kpi", "table"]


class ChartSpec(BaseModel):
    """What to draw. `data` is ready to hand to a charting library: a list of flat records."""

    type: ChartType
    title: str
    x: str | None = None
    y: list[str] = Field(default_factory=list)
    x_kind: Literal["category", "time", "number"] | None = None
    data: list[dict[str, Any]] = Field(default_factory=list)
    horizontal: bool = False
    caption: str | None = None       # for KPI cards: the row's label, e.g. the product name
    notes: list[str] = Field(default_factory=list)
    alternatives: list[ChartType] = Field(default_factory=list)


class ResultAnalysis(BaseModel):
    profile: ResultProfile
    chart: ChartSpec | None = None
    summary: str | None = None
    summary_source: Literal["llm", "template"] | None = None


class Turn(BaseModel):
    """One question in a conversation and how it was answered. Result rows are not stored."""

    id: str
    seq: int
    question: str
    status: Literal["ok", "clarification", "failed"]
    sql: str | None = None
    explanation: str = ""
    tables_used: list[str] = Field(default_factory=list)
    clarification: str | None = None
    options: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    row_count: int | None = None
    error: str | None = None
    summary: str | None = None
    # True when the rows of this answer were kept (see HISTORY_RETAIN_ROWS).
    has_result: bool = False
    created_at: str


class HistoryItem(Turn):
    """A turn as listed in the history, with the conversation it belongs to."""

    conversation_id: str
    conversation_title: str


class RetentionInfo(BaseModel):
    retain_rows: int
    retention_days: int


class HistoryPage(BaseModel):
    items: list[HistoryItem]
    total: int
    retention: RetentionInfo


ContextKind = Literal["metric", "term", "table", "column"]


class ContextEntryIn(BaseModel):
    """A business definition or data-dictionary note supplied by the user."""

    kind: ContextKind
    name: str = Field(min_length=1, max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=5)
    description: str = Field(min_length=1, max_length=600)
    # A SQL expression for a metric, e.g. SUM(orders.quantity * products.price). Guidance for the model.
    expression: str | None = Field(default=None, max_length=400)


class ContextEntry(ContextEntryIn):
    id: str
    created_at: str
    updated_at: str


class ContextImportPreview(BaseModel):
    entries: list[ContextEntryIn]
    warnings: list[str] = Field(default_factory=list)


class ContextImportRequest(BaseModel):
    entries: list[ContextEntryIn] = Field(max_length=200)
    mode: Literal["skip", "replace"] = "skip"


class ContextImportResult(BaseModel):
    created: int
    updated: int
    skipped: int
    errors: list[str] = Field(default_factory=list)


class BlockedColumnsUpdate(BaseModel):
    """'table.column' or 'table.*' entries that questions may never read, for one data source."""

    columns: list[str] = Field(default_factory=list, max_length=200)


class BlockedColumns(BaseModel):
    columns: list[str]
    # Set by the administrator (BLOCKED_COLUMNS); always applies and cannot be changed here.
    locked: list[str] = Field(default_factory=list)


class ConversationSummary(BaseModel):
    id: str
    datasource_id: str
    title: str
    created_at: str
    updated_at: str
    turn_count: int = 0


class ConversationDetail(ConversationSummary):
    turns: list[Turn]


class ChatRequest(BaseModel):
    datasource_id: str
    message: str = Field(min_length=1, max_length=1000)
    conversation_id: str | None = None
    # Set to false to get the chart straight away and fetch the summary separately
    # (POST /api/analytics/summary), because the summary needs another LLM call.
    include_summary: bool = True


class TablePreview(BaseModel):
    table: str
    row_count: int
    columns: list[ColumnInfo]
    rows: list[dict[str, Any]]
