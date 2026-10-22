"""Synthetic data for dev/test: tables with known defects and the outcomes we expect.

Defect rates are deterministic (every Nth row), so expected PASS/FAIL results are exact. The smoke
test job runs seed -> sync -> run -> verify after each dev/test deployment. Never runs in prod.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from .naming import check_identifier

SYNTHETIC_SCHEMA = "dq_synthetic"
CUSTOMERS_N = 1000
ORDERS_N = 5000
OWNER = "synthetic-owner@example.com"

CUSTOMERS_DDL = "customer_id INT, email STRING, country_code STRING, loaded_at TIMESTAMP"
ORDERS_DDL = "order_id INT, amount DOUBLE, status STRING, loaded_at TIMESTAMP"


def customer_rows(now: datetime, n: int = CUSTOMERS_N) -> list[tuple]:
    """5% null emails, 2% invalid country codes, unique ids, data loaded 48h ago (stale)."""
    countries = ("AU", "US", "NZ", "GB")
    return [
        (
            i,
            None if i % 20 == 0 else f"user{i}@example.com",
            "XX1" if i % 50 == 0 else countries[i % 4],
            now - timedelta(hours=48),
        )
        for i in range(n)
    ]


def order_rows(now: datetime, n: int = ORDERS_N) -> list[tuple]:
    """Clean and fresh, but only 5,000 rows (the assignment asks for 10,000)."""
    statuses = ("NEW", "PAID", "SHIPPED")
    return [(i, 10.0 + (i % 90), statuses[i % 3], now) for i in range(n)]


def _key(catalog: str, table: str, column: str, metric: str) -> str:
    return f"{catalog}.{SYNTHETIC_SCHEMA}.{table}|{column}|{metric}".lower()


def expected_status(catalog: str) -> dict[str, str]:
    k = lambda t, c, m: _key(catalog, t, c, m)  # noqa: E731
    return {
        k("customers", "customer_id", "duplicate_pct"): "PASS",
        k("customers", "email", "null_pct"): "FAIL",  # 5% > 2%
        k("customers", "email", "regex_fail_pct"): "PASS",
        k("customers", "country_code", "regex_fail_pct"): "FAIL",  # 2% > 1%
        k("customers", "loaded_at", "hours_since_load"): "FAIL",  # ~48h > 24h
        k("customers", "", "row_count"): "PASS",  # 1000 >= 500
        k("orders", "order_id", "duplicate_pct"): "PASS",
        k("orders", "order_id", "null_pct"): "PASS",
        k("orders", "amount", "null_pct"): "PASS",  # threshold 0.01
        k("orders", "status", "regex_fail_pct"): "PASS",  # custom regex
        k("orders", "loaded_at", "hours_since_load"): "PASS",
        k("orders", "", "row_count"): "FAIL",  # 5000 < 10000
    }


EXPECTED_ISSUES = (("ERROR", "no_such_column"),)  # the deliberately bad YAML row


def verify(
    catalog: str,
    results: Iterable[Mapping[str, Any]],
    issues: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Returns a list of mismatches; empty means the framework behaved as designed."""
    problems: list[str] = []
    prefix = f"{catalog}.{SYNTHETIC_SCHEMA}.".lower()
    latest = {r["assignment_id"].lower(): r["status"] for r in results
              if r["assignment_id"].lower().startswith(prefix)}  # fmt: skip
    for key, want in expected_status(catalog).items():
        got = latest.get(key)
        if got != want:
            problems.append(f"{key}: expected {want}, got {got}")
    for key in sorted(set(latest) - set(expected_status(catalog))):
        problems.append(f"{key}: unexpected result (status {latest[key]})")
    issue_list = list(issues)
    for severity, text in EXPECTED_ISSUES:
        if not any(i["severity"] == severity and text in (i["message"] or "") for i in issue_list):
            problems.append(f"expected a {severity} issue mentioning '{text}'")
    return problems


def seed_synthetic(spark: Any, catalog: str, now: datetime) -> None:
    """Create/replace the synthetic tables (needs a SparkSession)."""
    check_identifier(catalog, "catalog")
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{SYNTHETIC_SCHEMA}`")
    frames = {
        "customers": (customer_rows(now), CUSTOMERS_DDL),
        "orders": (order_rows(now), ORDERS_DDL),
    }
    for table, (rows, ddl) in frames.items():
        fq = f"`{catalog}`.`{SYNTHETIC_SCHEMA}`.`{table}`"
        (spark.createDataFrame(rows, ddl).write.mode("overwrite")
            .option("overwriteSchema", "true").saveAsTable(fq))  # fmt: skip
