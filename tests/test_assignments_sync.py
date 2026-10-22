import math

import pytest

from dq_framework.assignments_sync import validate_all, validate_assignment
from dq_framework.models import Assignment, Metric

from helpers import METRICS, assignment

EXISTING = {"cat.sch.customers": {"email", "id"}}


def test_a_good_assignment_has_no_errors():
    assert validate_assignment(assignment(), METRICS) == []
    assert validate_assignment(assignment("row_count", None, threshold=10), METRICS) == []
    ok = assignment("regex_fail_pct", "email", params={"regex": "^a$"})
    assert validate_assignment(ok, METRICS) == []


@pytest.mark.parametrize(
    "bad, expected",
    [
        (assignment("nope"), "unknown metric"),
        (assignment("null_pct", None), "needs a column"),
        (Assignment("cat.sch.customers", "row_count", 1.0, "LOW", "o", "email"), "table-level"),
        (assignment("regex_fail_pct", "email"), "needs parameter 'regex'"),
        (assignment(severity="URGENT"), "severity"),
        (assignment(owner=""), "owner"),
        (assignment(threshold=math.nan), "finite"),
        (assignment(threshold=math.inf), "finite"),
        (assignment(table="customers"), "invalid table name"),
        (assignment(column="a b"), "invalid column name"),
        (assignment(params={"filter": "1=1; DROP"}), "filter"),
    ],
)
def test_validation_errors(bad, expected):
    assert any(expected in e for e in validate_assignment(bad, METRICS)), expected


def test_inactive_metric_is_rejected():
    metrics = {"null_pct": Metric("null_pct", "n", "completeness", "COLUMN", "x({col})", "MAX", is_active=False)}
    assert any("inactive" in e for e in validate_assignment(assignment(), metrics))


def test_existence_checks_use_information_schema_data():
    assert validate_assignment(assignment(column="EMAIL"), METRICS, EXISTING) == []  # case-insensitive
    assert any("column 'phone'" in e for e in validate_assignment(assignment(column="phone"), METRICS, EXISTING))
    assert any("table not found" in e for e in validate_assignment(assignment(table="cat.sch.other"), METRICS, EXISTING))
    assert validate_assignment(assignment(column="phone"), METRICS, None) == []  # check skipped


def test_table_level_check_only_needs_the_table_to_exist():
    assert validate_assignment(assignment("row_count", None, threshold=1), METRICS, EXISTING) == []


# ---------------------------------------------------------------------------- validate_all


def test_invalid_rows_are_dropped_and_reported_with_context():
    good = assignment(column="id")
    final, issues = validate_all([assignment(severity="URGENT"), good], METRICS)
    assert final == [good]
    assert len(issues) == 1 and issues[0].severity == "ERROR"
    assert issues[0].table_name == "cat.sch.customers" and "severity" in issues[0].message


def test_unknown_column_is_reported():
    final, issues = validate_all([assignment(column="phone")], METRICS, EXISTING)
    assert final == [] and "column 'phone'" in issues[0].message and issues[0].column_name == "phone"


def test_output_is_sorted_by_assignment_id():
    final, _ = validate_all([assignment(column="b"), assignment(column="a")], METRICS)
    assert [a.column_name for a in final] == ["a", "b"]


def test_duplicates_including_case_variants_keep_the_first_and_report_the_rest():
    first = assignment(threshold=0.1)
    dup = assignment(table="Cat.Sch.Customers", column="EMAIL", threshold=0.9)
    final, issues = validate_all([first, dup], METRICS)
    assert final == [first]
    assert len(issues) == 1 and "duplicate" in issues[0].message
