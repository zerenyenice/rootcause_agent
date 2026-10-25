"""Output writers: CSV per table, DuckDB file, Postgres SQL dump, data dictionary."""

from __future__ import annotations

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import duckdb
import numpy as np
import pandas as pd
from pandas.api import types as ptypes
from pandas.api.extensions import ExtensionDtype

from rootcause.evals.manufacturing.table_docs import TABLE_DOCS, TableDoc

__all__ = [
    "TABLE_DOCS",
    "TableDoc",
    "write_csv",
    "write_data_dictionary",
    "write_duckdb",
    "write_postgres_sql",
]

DATA_DICTIONARY_FILENAME = "DATA_DICTIONARY.md"
POSTGRES_NULL = r"\N"
# Parent tables first, so CREATE TABLE foreign keys and COPY blocks load in order.
TABLE_ORDER: tuple[str, ...] = (
    "plants",
    "production_lines",
    "machines",
    "product_families",
    "suppliers",
    "materials",
    "products",
    "material_lots",
    "operators",
    "shift_assignments",
    "work_orders",
    "production_runs",
    "quality_inspections",
    "machine_telemetry",
    "downtime_events",
    "maintenance_events",
    "gauge_calibrations",
    "energy_meter_readings",
    "warranty_claims",
    "business_events",
)
PRIMARY_KEYS: Mapping[str, tuple[str, ...]] = {
    "plants": ("plant_id",),
    "production_lines": ("line_id",),
    "machines": ("machine_id",),
    "product_families": ("family_id",),
    "products": ("product_id",),
    "suppliers": ("supplier_id",),
    "materials": ("material_id",),
    "material_lots": ("lot_id",),
    "operators": ("operator_id",),
    "shift_assignments": ("date", "operator_id"),
    "work_orders": ("work_order_id",),
    "production_runs": ("run_id",),
    "quality_inspections": ("inspection_id",),
    "machine_telemetry": ("ts", "machine_id"),
    "downtime_events": ("event_id",),
    "maintenance_events": ("event_id",),
    "gauge_calibrations": ("calibration_id",),
    "energy_meter_readings": ("ts", "line_id"),
    "warranty_claims": ("claim_id",),
    "business_events": ("event_id",),
}
# table -> ((column, parent table, parent column), ...)
FOREIGN_KEYS: Mapping[str, tuple[tuple[str, str, str], ...]] = {
    "production_lines": (("plant_id", "plants", "plant_id"),),
    "products": (
        ("family_id", "product_families", "family_id"),
        ("material_id", "materials", "material_id"),
    ),
    "materials": (("primary_supplier_id", "suppliers", "supplier_id"),),
    "operators": (("home_line_id", "production_lines", "line_id"),),
    "downtime_events": (("machine_id", "machines", "machine_id"),),
    "maintenance_events": (("machine_id", "machines", "machine_id"),),
    "energy_meter_readings": (("line_id", "production_lines", "line_id"),),
    "business_events": (("plant_id", "plants", "plant_id"),),
    "machines": (("line_id", "production_lines", "line_id"),),
    "material_lots": (
        ("supplier_id", "suppliers", "supplier_id"),
        ("material_id", "materials", "material_id"),
    ),
    "shift_assignments": (
        ("operator_id", "operators", "operator_id"),
        ("line_id", "production_lines", "line_id"),
    ),
    "work_orders": (
        ("line_id", "production_lines", "line_id"),
        ("product_id", "products", "product_id"),
    ),
    "production_runs": (
        ("line_id", "production_lines", "line_id"),
        ("work_order_id", "work_orders", "work_order_id"),
        ("product_id", "products", "product_id"),
        ("material_lot_id", "material_lots", "lot_id"),
    ),
    "quality_inspections": (
        ("run_id", "production_runs", "run_id"),
        ("inspector_id", "operators", "operator_id"),
    ),
    "machine_telemetry": (("machine_id", "machines", "machine_id"),),
    "warranty_claims": (
        ("run_id", "production_runs", "run_id"),
        ("product_id", "products", "product_id"),
    ),
}


def _in_load_order(tables: Mapping[str, pd.DataFrame]) -> list[tuple[str, pd.DataFrame]]:
    known = [(name, tables[name]) for name in TABLE_ORDER if name in tables]
    extra = [(name, frame) for name, frame in tables.items() if name not in TABLE_ORDER]
    return known + extra


@dataclass(frozen=True)
class ColumnSpec:
    """SQL-facing view of one DataFrame column."""

    name: str
    sql_type: str
    is_date: bool
    is_timestamp: bool


def _is_date_column(name: str) -> bool:
    return name == "date" or name.endswith("_date")


def column_spec(name: str, dtype: np.dtype[Any] | ExtensionDtype) -> ColumnSpec:
    """Map a pandas dtype (plus naming convention for dates) to a SQL type."""
    if ptypes.is_bool_dtype(dtype):
        return ColumnSpec(name, "BOOLEAN", False, False)
    if ptypes.is_integer_dtype(dtype):
        return ColumnSpec(name, "BIGINT", False, False)
    if ptypes.is_float_dtype(dtype):
        return ColumnSpec(name, "DOUBLE PRECISION", False, False)
    if ptypes.is_datetime64_any_dtype(dtype):
        if _is_date_column(name):
            return ColumnSpec(name, "DATE", True, False)
        return ColumnSpec(name, "TIMESTAMP", False, True)
    return ColumnSpec(name, "TEXT", False, False)


def table_specs(frame: pd.DataFrame) -> list[ColumnSpec]:
    """Column specs for every column of ``frame`` in order."""
    return [column_spec(str(name), dtype) for name, dtype in frame.dtypes.items()]


def write_csv(tables: Mapping[str, pd.DataFrame], out_dir: Path) -> None:
    """Write ``<table>.csv`` for every table, creating ``out_dir`` if needed."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in tables.items():
        frame.to_csv(out_dir / f"{name}.csv", index=False)


def _duckdb_select(spec: ColumnSpec) -> str:
    if spec.is_date:
        return f'CAST("{spec.name}" AS DATE) AS "{spec.name}"'
    if spec.is_timestamp:
        return f'CAST("{spec.name}" AS TIMESTAMP) AS "{spec.name}"'
    return f'"{spec.name}"'


def write_duckdb(tables: Mapping[str, pd.DataFrame], path: Path) -> None:
    """Write every table into a fresh DuckDB database file at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    connection = duckdb.connect(str(path))
    try:
        for name, frame in tables.items():
            selected = ", ".join(_duckdb_select(spec) for spec in table_specs(frame))
            connection.register("source_frame", frame)
            connection.execute(f'CREATE TABLE "{name}" AS SELECT {selected} FROM source_frame')
            connection.unregister("source_frame")
    finally:
        connection.close()


def create_table_sql(name: str, frame: pd.DataFrame) -> str:
    """``CREATE TABLE`` statement with typed columns and a primary key when known."""
    lines = [f"    {spec.name} {spec.sql_type}" for spec in table_specs(frame)]
    key = PRIMARY_KEYS.get(name)
    if key is not None and all(column in frame.columns for column in key):
        lines.append(f"    PRIMARY KEY ({', '.join(key)})")
    for column, parent, parent_column in FOREIGN_KEYS.get(name, ()):
        if column in frame.columns:
            lines.append(f"    FOREIGN KEY ({column}) REFERENCES {parent} ({parent_column})")
    body = ",\n".join(lines)
    return f"CREATE TABLE {name} (\n{body}\n);\n"


def _copy_ready(frame: pd.DataFrame) -> pd.DataFrame:
    """Format every column as Postgres text-format ``COPY`` expects."""
    columns: dict[str, pd.Series] = {}
    for spec in table_specs(frame):
        series = frame[spec.name]
        if spec.is_date:
            series = series.dt.strftime("%Y-%m-%d")
        elif spec.is_timestamp:
            series = series.dt.strftime("%Y-%m-%d %H:%M:%S")
        elif spec.sql_type == "BOOLEAN":
            series = series.map({True: "t", False: "f"})
        elif spec.sql_type == "TEXT":
            series = series.astype("str").str.replace("\\", "\\\\", regex=False)
            series = series.str.replace("\t", "\\t", regex=False)
            series = series.str.replace("\n", "\\n", regex=False)
            series = series.str.replace("\r", "\\r", regex=False)
        columns[spec.name] = series
    return pd.DataFrame(columns)


def _write_copy_block(handle: TextIO, name: str, frame: pd.DataFrame) -> None:
    columns = ", ".join(str(column) for column in frame.columns)
    handle.write(f"COPY {name} ({columns}) FROM STDIN;\n")
    _copy_ready(frame).to_csv(
        handle,
        sep="\t",
        header=False,
        index=False,
        na_rep=POSTGRES_NULL,
        quoting=csv.QUOTE_NONE,
        lineterminator="\n",
    )
    handle.write("\\.\n\n")


def write_postgres_sql(tables: Mapping[str, pd.DataFrame], path: Path) -> None:
    """Write a ``psql``-loadable dump: one CREATE TABLE and one COPY block per table."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("-- RootCause synthetic manufacturing warehouse (generated; do not edit)\n")
        handle.write("BEGIN;\n\n")
        ordered = _in_load_order(tables)
        for name, frame in ordered:
            handle.write(create_table_sql(name, frame))
            handle.write("\n")
        for name, frame in ordered:
            _write_copy_block(handle, name, frame)
        handle.write("COMMIT;\n")


def render_data_dictionary(docs: Mapping[str, TableDoc] = TABLE_DOCS) -> str:
    """Markdown with one ``## <table>`` section and a column table per table."""
    parts = ["# Data dictionary: synthetic manufacturing warehouse\n"]
    for name, doc in docs.items():
        parts.append(f"\n## {name}\n\n{doc.description}\n\n| Column | Description |\n| --- | --- |")
        parts.extend(f"| `{column}` | {text} |" for column, text in doc.columns.items())
        parts.append("")
    return "\n".join(parts)


def write_data_dictionary(out_dir: Path) -> Path:
    """Write ``DATA_DICTIONARY.md`` under ``out_dir`` and return its path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / DATA_DICTIONARY_FILENAME
    target.write_text(render_data_dictionary(), encoding="utf-8")
    return target
