import re
from decimal import Decimal

from dq_framework.engine import run_checks
from dq_framework.models import Metric

from helpers import METRICS, RUN_TS, assignment

METRIC_LIST = list(METRICS.values())


class FakeSpark:
    """Stands in for spark.sql(...).first().asDict(). Aliases are m<index of the assignment>."""

    def __init__(self, values, fail_if=None):
        self.values = values  # one value per assignment, by position
        self.fail_if = fail_if
        self.queries = []

    def __call__(self, sql):
        self.queries.append(sql)
        if self.fail_if and self.fail_if(sql):
            raise RuntimeError("[ANALYSIS_ERROR] boom\nlong stack trace")
        return {a: self.values[int(a[1:])] for a in re.findall(r"AS (m\d+)", sql)}


def run(assignments, spark, metrics=METRIC_LIST, workers=1):
    return run_checks(assignments, metrics, spark, "run-1", RUN_TS, workers)


def test_one_scan_per_table_and_results_are_standardised():
    a = [
        assignment("null_pct", "email", "c.s.a", 0.02),
        assignment("duplicate_pct", "id", "c.s.a", 0.0),
        assignment("row_count", None, "c.s.a", 100, severity="CRITICAL"),
        assignment("null_pct", "x", "c.s.b", 0.1),
    ]
    spark = FakeSpark([0.05, 0.0, 250.0, 0.0])
    results = run(a, spark)
    assert len(spark.queries) == 2
    assert [r.status for r in results] == ["FAIL", "PASS", "PASS", "PASS"]
    first = results[0]
    assert (first.run_id, first.run_ts) == ("run-1", RUN_TS)
    assert (first.assignment_id, first.metric_value, first.threshold) == ("c.s.a|email|null_pct", 0.05, 0.02)
    assert (first.direction, first.dimension, first.metric_version) == ("MAX", "completeness", 1)
    assert (first.severity, first.owner, first.error_message) == ("HIGH", "owner@example.com", None)
    assert results[2].severity == "CRITICAL"


def test_min_direction_fails_when_below_threshold():
    results = run([assignment("row_count", None, "c.s.a", 1000)], FakeSpark([10.0]))
    assert results[0].status == "FAIL"


def test_disabled_assignments_are_skipped():
    spark = FakeSpark([0.0])
    assert run([assignment(is_enabled=False)], spark) == []
    assert spark.queries == []


def test_unknown_and_inactive_metrics_become_error_rows_without_querying():
    inactive = Metric("old", "Old", "validity", "COLUMN", "count({col})", "MAX", is_active=False)
    a = [assignment("nope", "email"), assignment("old", "email")]
    spark = FakeSpark([0.0, 0.0])
    results = run(a, spark, METRIC_LIST + [inactive])
    assert [r.status for r in results] == ["ERROR", "ERROR"]
    assert "unknown metric" in results[0].error_message
    assert "inactive" in results[1].error_message
    assert spark.queries == []


def test_render_problem_becomes_error_row():
    results = run([assignment("regex_fail_pct", "code")], FakeSpark([0.0]))  # regex param missing
    assert results[0].status == "ERROR" and "missing parameter" in results[0].error_message


def test_missing_table_errors_all_its_checks_but_not_others():
    a = [assignment(table="c.s.gone"), assignment("duplicate_pct", "id", "c.s.gone", 0.0),
         assignment(table="c.s.ok")]  # fmt: skip
    spark = FakeSpark([0.0, 0.0, 0.0], fail_if=lambda sql: "`gone`" in sql)
    results = run(a, spark)
    assert [r.status for r in results] == ["ERROR", "ERROR", "PASS"]
    assert results[0].error_message == "[ANALYSIS_ERROR] boom"  # first line only, no stack trace
    assert results[0].metric_value is None


def test_one_broken_check_does_not_hide_the_others_on_the_same_table():
    a = [assignment("null_pct", "good1"), assignment("null_pct", "bad"), assignment("null_pct", "good2")]
    spark = FakeSpark([0.0, 0.0, 0.0], fail_if=lambda sql: "`bad`" in sql)
    results = run(a, spark)
    assert [r.status for r in results] == ["PASS", "ERROR", "PASS"]
    assert len(spark.queries) == 1 + 3  # combined attempt, then one query per check


def test_null_metric_value_is_an_error():
    results = run([assignment("hours_since_load", "ts", threshold=24)], FakeSpark([None]))
    assert results[0].status == "ERROR" and "NULL" in results[0].error_message


def test_decimal_values_are_coerced_to_float():
    results = run([assignment()], FakeSpark([Decimal("0.05000")]))
    assert results[0].metric_value == 0.05 and isinstance(results[0].metric_value, float)


def test_filters_split_scans_and_apply_a_where_clause():
    a = [assignment("null_pct", "a", params={"filter": "d = 1"}), assignment("null_pct", "b"),
         assignment("null_pct", "c", params={"filter": "d = 1"})]  # fmt: skip
    spark = FakeSpark([0.0, 0.0, 0.0])
    run(a, spark)
    assert len(spark.queries) == 2
    assert sum("WHERE d = 1" in q for q in spark.queries) == 1


def test_dangerous_filter_is_an_error_not_a_query():
    spark = FakeSpark([0.0])
    results = run([assignment(params={"filter": "1=1; DROP TABLE x"})], spark)
    assert results[0].status == "ERROR" and spark.queries == []


def test_parallel_run_matches_sequential_and_keeps_order():
    a = [assignment("null_pct", "c", f"cat.sch.t{i}", 0.5) for i in range(12)]
    values = [0.1 if i % 2 else 0.9 for i in range(12)]
    sequential = run(a, FakeSpark(values), workers=1)
    parallel = run(a, FakeSpark(values), workers=4)
    assert [(r.assignment_id, r.status) for r in parallel] == [(r.assignment_id, r.status) for r in sequential]
    assert [r.status for r in parallel] == ["FAIL", "PASS"] * 6
