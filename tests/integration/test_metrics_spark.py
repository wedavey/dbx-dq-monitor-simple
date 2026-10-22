"""Runs the shipped metric SQL on a real (local) Spark session. Skipped when pyspark is missing.

This is the test that proves the metric templates are valid Spark SQL with the intended semantics.
Needs `pip install pyspark` (or databricks-connect configured) and a JVM.
"""

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("pyspark")

from dq_framework.engine import run_checks  # noqa: E402

from helpers import METRICS, RUN_TS, assignment  # noqa: E402

pytestmark = pytest.mark.spark
TABLE = "spark_catalog.default.dq_it_sample"


@pytest.fixture(scope="module", autouse=True)
def sample_table(spark):
    """Builds the sample table on the shared session from tests/conftest.py."""
    session = spark
    now = datetime.now(timezone.utc)
    rows = [
        (1, "a@x.com", "AU", now - timedelta(hours=3)),
        (2, "b@x.com", "AU", now - timedelta(hours=2)),
        (3, None, "AU", now - timedelta(hours=2)),
        (4, "", "US", now - timedelta(hours=2)),
        (5, "bad", "US", now - timedelta(hours=2)),
        (6, "f@x.com", "XX1", now - timedelta(hours=2)),
        (7, "g@x.com", "US", now - timedelta(hours=2)),
        (8, "h@x.com", "US", now - timedelta(hours=2)),
        (9, "i@x.com", "NZ", now - timedelta(hours=2)),
        (9, "j@x.com", "NZ", now - timedelta(hours=1)),
    ]
    session.createDataFrame(rows, "id INT, email STRING, code STRING, loaded_at TIMESTAMP").write.mode(
        "overwrite"
    ).saveAsTable(TABLE)
    yield
    session.sql(f"DROP TABLE IF EXISTS {TABLE}")


def value_of(spark, metric, column, threshold=0.0, **kwargs):
    a = assignment(metric, column, TABLE, threshold, **kwargs)
    (result,) = run_checks([a], METRICS.values(), lambda sql: spark.sql(sql).first().asDict(), "r", RUN_TS)
    assert result.status != "ERROR", result.error_message
    return result.metric_value


def test_null_pct(spark):
    assert value_of(spark, "null_pct", "email") == pytest.approx(0.1)


def test_blank_pct_counts_empty_but_not_null(spark):
    assert value_of(spark, "blank_pct", "email") == pytest.approx(0.1)


def test_uniqueness_metrics(spark):
    assert value_of(spark, "duplicate_pct", "id") == pytest.approx(0.1)
    assert value_of(spark, "distinct_ratio", "id", 0.9) == pytest.approx(0.9)


def test_regex_fail_pct_with_escaped_param(spark):
    assert value_of(spark, "regex_fail_pct", "code", 0.0, params={"regex": "^[A-Z]{2}$"}) == pytest.approx(0.1)
    assert value_of(spark, "regex_fail_pct", "email", 0.0, params={"regex": r"^[^@\s]+@[^@\s]+\.[^@\s]+$"}) == pytest.approx(0.2)


def test_row_count_and_filter(spark):
    assert value_of(spark, "row_count", None, 1) == 10
    assert value_of(spark, "row_count", None, 1, params={"filter": "code = 'AU'"}) == 3


def test_hours_since_load(spark):
    assert 0.9 < value_of(spark, "hours_since_load", "loaded_at", 24) < 1.5


def test_a_dropped_column_is_isolated_to_an_error_row(spark):
    good = assignment("null_pct", "email", TABLE, 0.5)
    bad = assignment("null_pct", "no_such_column", TABLE, 0.5)
    results = run_checks([good, bad], METRICS.values(), lambda sql: spark.sql(sql).first().asDict(), "r", RUN_TS)
    assert [r.status for r in results] == ["PASS", "ERROR"]
