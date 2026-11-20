"""CSV / Excel upload: staging, parsing, cleaning, type detection and import into SQLite."""

import csv
import io
import json
import re
import uuid
import warnings
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import Float, Integer, Text, create_engine, inspect

from app.config import get_settings
from app.core.identifiers import sanitize_identifier, unique_identifiers
from app.schemas import (
    ColumnInfo,
    DataSource,
    ImportRequest,
    LogicalType,
    SheetPreview,
    TableInfo,
    TableSelection,
    UploadPreview,
)
from app.services import registry

ALLOWED_EXTENSIONS = {".csv": "csv", ".xlsx": "xlsx", ".xls": "xls"}
PREVIEW_ROWS = 10
_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")
# Year-first (2025-01-31) or year-last (31/01/2025); a 4-digit year is required so
# values like version numbers (1.2.3) are not mistaken for dates.
_DATE_LIKE = re.compile(
    r"^(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4})"
    r"([ T]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?)?(Z|[+-]\d{2}:?\d{2})?$"
)


class FileImportError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class CleanedSheet:
    sheet_name: str
    df: pd.DataFrame
    columns: list[ColumnInfo]
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- staging

def stage_upload(workspace_id: str, filename: str, content: bytes) -> tuple[str, str]:
    """Validate and store an uploaded file. Returns (upload_id, file_type)."""
    settings = get_settings()
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise FileImportError("Unsupported file type. Upload a .csv, .xlsx or .xls file.", 415)
    if not content:
        raise FileImportError("The uploaded file is empty.")
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise FileImportError(f"File is larger than the {settings.max_upload_mb} MB limit.", 413)
    if ext == ".xlsx" and not zipfile.is_zipfile(io.BytesIO(content)):
        raise FileImportError("The file is not a valid .xlsx workbook.")
    if ext == ".xls" and not content.startswith(b"\xd0\xcf\x11\xe0"):
        raise FileImportError("The file is not a valid .xls workbook.")

    upload_id = uuid.uuid4().hex
    folder = registry.staging_dir(workspace_id) / upload_id
    folder.mkdir()
    (folder / f"original{ext}").write_bytes(content)
    (folder / "meta.json").write_text(json.dumps({"filename": Path(filename).name, "ext": ext}))
    return upload_id, ALLOWED_EXTENSIONS[ext]


def _load_staged(workspace_id: str, upload_id: str) -> tuple[Path, str, str]:
    if not _UPLOAD_ID_RE.match(upload_id):
        raise FileImportError("Unknown upload.", 404)
    folder = registry.staging_dir(workspace_id) / upload_id
    meta_path = folder / "meta.json"
    if not meta_path.exists():
        raise FileImportError("Unknown or expired upload.", 404)
    meta = json.loads(meta_path.read_text())
    return folder / f"original{meta['ext']}", meta["filename"], ALLOWED_EXTENSIONS[meta["ext"]]


# ---------------------------------------------------------------- reading

def _read_csv(path: Path, max_rows: int) -> pd.DataFrame:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise FileImportError("Could not decode the CSV file. Save it as UTF-8 and try again.")
    try:
        delimiter = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    try:
        return pd.read_csv(io.StringIO(text), sep=delimiter, nrows=max_rows + 1)
    except Exception as exc:
        raise FileImportError(f"Could not parse the CSV file ({type(exc).__name__}).") from exc


def _read_excel(path: Path, file_type: str) -> dict[str, pd.DataFrame]:
    try:
        return pd.read_excel(path, sheet_name=None, engine="openpyxl" if file_type == "xlsx" else "xlrd")
    except Exception as exc:
        raise FileImportError(f"Could not read the Excel workbook ({type(exc).__name__}).") from exc


def _read_raw(path: Path, filename: str, file_type: str) -> dict[str, pd.DataFrame]:
    max_rows = get_settings().max_upload_rows
    if file_type == "csv":
        return {Path(filename).stem: _read_csv(path, max_rows)}
    return _read_excel(path, file_type)


# --------------------------------------------------------------- cleaning

def _is_text(s: pd.Series) -> bool:
    return pd.api.types.is_string_dtype(s) or s.dtype == object


def _clean_text(value):
    if pd.isna(value):
        return pd.NA
    text = str(value).strip()
    return text if text else pd.NA


def _format_datetimes(s: pd.Series) -> tuple[pd.Series, LogicalType]:
    if getattr(s.dt, "tz", None) is not None:
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    non_null = s.dropna()
    has_time = bool((non_null != non_null.dt.normalize()).any())
    fmt = "%Y-%m-%d %H:%M:%S" if has_time else "%Y-%m-%d"
    return s.dt.strftime(fmt).astype(object).where(s.notna(), None), "datetime" if has_time else "date"


def _try_parse_dates(s: pd.Series) -> tuple[pd.Series, LogicalType] | None:
    """Parse a text column as dates only if every non-null value looks like and parses as a date."""
    non_null = s.dropna()
    if non_null.empty:
        return None
    sample = non_null.head(200).astype(str)
    if not sample.map(lambda v: bool(_DATE_LIKE.match(v))).all():
        return None
    present = s.notna()
    values = s.astype(object)
    # Each attempt applies one format to the whole column: ISO first, then a format inferred
    # from the data month-first, then day-first. A column is never read with mixed conventions.
    attempts = (
        {"format": "ISO8601"},
        {"dayfirst": False},
        {"dayfirst": True},
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for kwargs in attempts:
            parsed = pd.to_datetime(values, errors="coerce", utc=True, **kwargs)
            if not parsed[present].isna().any():
                return _format_datetimes(parsed)
    return None


def _convert_column(s: pd.Series) -> tuple[pd.Series, LogicalType]:
    if pd.api.types.is_bool_dtype(s):
        return s.astype("Int64"), "boolean"
    if pd.api.types.is_integer_dtype(s):
        return s.astype("Int64"), "integer"
    if pd.api.types.is_float_dtype(s):
        non_null = s.dropna()
        if len(non_null) and np.isfinite(non_null).all() and (non_null % 1 == 0).all():
            return s.astype("Int64"), "integer"
        return s, "float"
    if pd.api.types.is_datetime64_any_dtype(s):
        return _format_datetimes(s)
    cleaned = s.map(_clean_text).astype(object)
    parsed = _try_parse_dates(cleaned)
    if parsed:
        return parsed
    return cleaned, "text"


def clean_frame(sheet_name: str, df: pd.DataFrame) -> CleanedSheet | None:
    """Drop empty rows/columns, sanitize headers and detect column types."""
    notes: list[str] = []
    df = df.dropna(how="all")
    before = df.shape[1]
    df = df.dropna(axis=1, how="all")
    if df.shape[1] < before:
        notes.append(f"Dropped {before - df.shape[1]} empty column(s).")
    if df.empty or df.shape[1] == 0:
        return None

    originals = ["" if str(c).startswith("Unnamed:") else str(c) for c in df.columns]
    if any(not o for o in originals):
        notes.append("Some columns had blank headers and were given generated names.")
    names = unique_identifiers(originals, fallback="col")
    if len(set(sanitize_identifier(o, "col") for o in originals)) < len(originals):
        notes.append("Duplicate column names were made unique.")

    converted: dict[str, pd.Series] = {}
    columns: list[ColumnInfo] = []
    for name, original, (_, series) in zip(names, originals, df.items()):
        series = series.reset_index(drop=True)
        new_series, logical = _convert_column(series)
        converted[name] = new_series
        columns.append(ColumnInfo(name=name, original_name=original or name, type=logical))
    return CleanedSheet(sheet_name, pd.DataFrame(converted), columns, notes)


def _load_sheets(workspace_id: str, upload_id: str) -> tuple[str, str, list[CleanedSheet], list[str]]:
    path, filename, file_type = _load_staged(workspace_id, upload_id)
    max_rows = get_settings().max_upload_rows
    sheets: list[CleanedSheet] = []
    warns: list[str] = []
    for sheet_name, df in _read_raw(path, filename, file_type).items():
        if len(df) > max_rows:
            raise FileImportError(
                f"'{sheet_name}' has more than the {max_rows:,} row limit.", 413)
        cleaned = clean_frame(str(sheet_name), df)
        if cleaned is None:
            warns.append(f"Sheet '{sheet_name}' is empty and was skipped.")
            continue
        warns.extend(f"{sheet_name}: {n}" for n in cleaned.warnings)
        sheets.append(cleaned)
    if not sheets:
        raise FileImportError("The file contains no data.")
    return filename, file_type, sheets, warns


# ---------------------------------------------------------------- preview

def _records(df: pd.DataFrame, limit: int) -> list[dict]:
    return json.loads(df.head(limit).to_json(orient="records"))


def build_preview(workspace_id: str, upload_id: str) -> UploadPreview:
    filename, file_type, sheets, warns = _load_sheets(workspace_id, upload_id)
    table_names = unique_identifiers([s.sheet_name for s in sheets], fallback="table")
    return UploadPreview(
        upload_id=upload_id,
        filename=filename,
        file_type=file_type,  # type: ignore[arg-type]
        sheets=[
            SheetPreview(
                sheet_name=s.sheet_name,
                suggested_table_name=table,
                row_count=len(s.df),
                columns=s.columns,
                preview_rows=_records(s.df, PREVIEW_ROWS),
            )
            for s, table in zip(sheets, table_names)
        ],
        warnings=warns,
    )


# ----------------------------------------------------------------- import

_SQL_TYPES = {
    "integer": Integer, "boolean": Integer, "float": Float,
    "text": Text, "date": Text, "datetime": Text,
}


def import_upload(workspace_id: str, req: ImportRequest) -> DataSource:
    filename, _, sheets, _ = _load_sheets(workspace_id, req.upload_id)
    by_name = {s.sheet_name: s for s in sheets}

    selections = req.tables or [TableSelection(sheet_name=s.sheet_name) for s in sheets]
    chosen: list[CleanedSheet] = []
    requested_names: list[str] = []
    for sel in selections:
        if sel.sheet_name not in by_name:
            raise FileImportError(f"Sheet '{sel.sheet_name}' was not found in the upload.", 404)
        requested_names.append(sel.table_name or sel.sheet_name)
        chosen.append(by_name[sel.sheet_name])
    table_names = unique_identifiers(requested_names, fallback="table")

    if req.datasource_id:
        datasource = registry.get_datasource(req.datasource_id, workspace_id)
        if datasource is None:
            raise FileImportError("Data source not found.", 404)
        if datasource.kind != "upload":
            raise FileImportError("The sample database cannot be modified.", 403)
        db_path = registry.get_db_path(req.datasource_id, workspace_id)
        existing = {t.name: t for t in datasource.tables}
    else:
        datasource = None
        db_path = None
        existing = {}

    if req.if_exists == "fail":
        clashes = [t for t in table_names if t in existing]
        if clashes:
            raise FileImportError(
                f"Table(s) already exist: {', '.join(clashes)}. Rename them or use if_exists='replace'.", 409)

    new_infos = [
        TableInfo(name=table, original_name=sheet.sheet_name, row_count=len(sheet.df),
                  columns=sheet.columns)
        for table, sheet in zip(table_names, chosen)
    ]

    created = datasource is None
    if datasource is None:
        name = (req.datasource_name or Path(filename).stem).strip()[:100] or "Uploaded data"
        datasource = registry.create_upload_datasource(workspace_id, name, new_infos)
        db_path = registry.get_db_path(datasource.id, workspace_id)
    assert db_path is not None

    try:
        _write_tables(db_path, table_names, chosen, req.if_exists)
    except Exception:
        if created:
            registry.delete_datasource(datasource.id, workspace_id)
        raise

    if req.datasource_id:
        merged = {t.name: t for t in datasource.tables}
        merged.update({t.name: t for t in new_infos})
        registry.update_tables(datasource.id, list(merged.values()))

    registry.remove_staging(workspace_id, req.upload_id)
    return registry.get_datasource(datasource.id, workspace_id)  # type: ignore[return-value]


def _write_tables(db_path: Path, table_names: list[str], sheets: list[CleanedSheet], if_exists: str) -> None:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.begin() as conn:
            present = set(inspect(conn).get_table_names())
            for table, sheet in zip(table_names, sheets):
                if table in present and if_exists == "fail":
                    raise FileImportError(f"Table '{table}' already exists.", 409)
                dtypes = {c.name: _SQL_TYPES[c.type] for c in sheet.columns}
                sheet.df.to_sql(table, conn, if_exists="replace", index=False, dtype=dtypes)
    finally:
        engine.dispose()
