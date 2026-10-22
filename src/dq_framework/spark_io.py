"""All Spark / Unity Catalog I/O. Deliberately thin: the logic lives in the pure modules.

Not unit-tested without a cluster; tests/integration covers it when pyspark is available.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from .models import Assignment, CheckResult, Issue, Metric
from .rendering import is_valid_table_name

METRICS_DDL = (
    "metric_id STRING, metric_name STRING, dimension STRING, scope STRING, sql_template STRING, "
    "direction STRING, description STRING, version INT, is_active BOOLEAN, "
    "updated_at TIMESTAMP, updated_by STRING"
)
ASSIGNMENTS_DDL = (
    "assignment_id STRING, table_name STRING, column_name STRING, metric_id STRING, "
    "threshold DOUBLE, severity STRING, owner STRING, params MAP<STRING, STRING>, "
    "data_product STRING, is_enabled BOOLEAN, updated_at TIMESTAMP, "
    "updated_by STRING"
)
ISSUES_DDL = (
    "run_id STRING, run_ts TIMESTAMP, severity STRING, table_name STRING, "
    "column_name STRING, message STRING"
)
RESULTS_DDL = (
    "run_id STRING, run_ts TIMESTAMP, assignment_id STRING, table_name STRING, "
    "column_name STRING, metric_id STRING, metric_version INT, dimension STRING, "
    "metric_value DOUBLE, threshold DOUBLE, direction STRING, status STRING, severity STRING, "
    "owner STRING, data_product STRING, error_message STRING"
)


# --------------------------------------------------------------------------- reads


def current_user(spark: Any) -> str:
    return spark.sql("SELECT current_user()").first()[0]


def read_metrics(spark: Any, table: str, active_only: bool = True) -> list[Metric]:
    where = " WHERE is_active" if active_only else ""
    rows = spark.sql(f"SELECT * FROM {table}{where}").collect()
    return [
        Metric(r.metric_id, r.metric_name, r.dimension, r.scope, r.sql_template, r.direction,
               r.description or "", r.version, r.is_active)
        for r in rows
    ]  # fmt: skip


def read_assignments(spark: Any, table: str) -> list[Assignment]:
    rows = spark.sql(f"SELECT * FROM {table} WHERE is_enabled").collect()
    return [
        Assignment(r.table_name, r.metric_id, r.threshold, r.severity, r.owner, r.column_name,
                   dict(r.params or {}), r.data_product, r.is_enabled)
        for r in rows
    ]  # fmt: skip


def read_existing_columns(spark: Any, table_names: Iterable[str]) -> dict[str, set[str]]:
    """{catalog.schema.table (lower): {column names (lower)}} for the schemas the tables live in."""
    schemas = {tuple(t.lower().split(".")[:2]) for t in table_names if is_valid_table_name(t)}
    found: dict[str, set[str]] = {}
    for catalog, schema in sorted(schemas):
        for r in spark.sql(
            f"SELECT table_name, column_name FROM `{catalog}`.information_schema.columns "
            f"WHERE table_schema = '{schema}'"
        ).collect():
            found.setdefault(f"{catalog}.{schema}.{r.table_name}".lower(), set()).add(r.column_name.lower())
    return found


# --------------------------------------------------------------------------- writes


def _overwrite(spark: Any, table: str, rows: list[tuple], ddl: str) -> None:
    # INSERT OVERWRITE keeps the table definition, constraints and history (time travel = audit).
    spark.createDataFrame(rows, ddl).createOrReplaceTempView("_dq_stage")
    spark.sql(f"INSERT OVERWRITE {table} SELECT * FROM _dq_stage")


def write_metrics(spark: Any, table: str, metrics: list[Metric], user: str, ts: datetime) -> None:
    rows = [
        (m.metric_id, m.metric_name, m.dimension, m.scope, m.sql_template, m.direction,
         m.description, m.version, m.is_active, ts, user)
        for m in metrics
    ]  # fmt: skip
    _overwrite(spark, table, rows, METRICS_DDL)


def write_assignments(
    spark: Any, table: str, assignments: list[Assignment], user: str, ts: datetime
) -> None:
    rows = [
        (a.assignment_id, a.table_name, a.column_name, a.metric_id, float(a.threshold), a.severity,
         a.owner, dict(a.params or {}), a.data_product, a.is_enabled, ts, user)
        for a in assignments
    ]  # fmt: skip
    _overwrite(spark, table, rows, ASSIGNMENTS_DDL)


def write_issues(spark: Any, table: str, issues: list[Issue], run_id: str, ts: datetime) -> None:
    rows = [(run_id, ts, i.severity, i.table_name, i.column_name, i.message) for i in issues]
    _overwrite(spark, table, rows, ISSUES_DDL)


def append_results(spark: Any, table: str, results: list[CheckResult]) -> None:
    rows = [
        (r.run_id, r.run_ts, r.assignment_id, r.table_name, r.column_name, r.metric_id,
         r.metric_version, r.dimension, r.metric_value, float(r.threshold), r.direction,
         r.status, r.severity, r.owner, r.data_product, r.error_message)
        for r in results
    ]  # fmt: skip
    if rows:
        spark.createDataFrame(rows, RESULTS_DDL).write.mode("append").saveAsTable(table)
