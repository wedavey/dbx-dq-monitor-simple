import math

import pytest

from dq_framework.assignments_sync import SyncAbort, check_shrink, consolidate, validate_assignment
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


# ---------------------------------------------------------------------------- consolidation


def from_source(source, **kwargs):
    return assignment(source=source, **kwargs)


def test_priority_is_yaml_then_app_then_csv_then_tag():
    tag = from_source("tag", threshold=0.1)
    csv = from_source("csv", threshold=0.2)
    app = from_source("app", threshold=0.3)
    yml = from_source("yaml", threshold=0.4)
    final, issues = consolidate([tag, csv, app, yml], METRICS)
    assert [(a.source, a.threshold) for a in final] == [("yaml", 0.4)] and issues == []
    final, _ = consolidate([tag, csv, app], METRICS)
    assert final[0].source == "app"
    final, _ = consolidate([tag, csv], METRICS)
    assert final[0].source == "csv"


def test_yaml_can_disable_a_tag_derived_check():
    final, _ = consolidate([from_source("tag"), from_source("yaml", is_enabled=False)], METRICS)
    assert len(final) == 1 and final[0].source == "yaml" and final[0].is_enabled is False


def test_invalid_high_priority_row_does_not_suppress_a_valid_lower_one():
    bad_yaml = from_source("yaml", severity="URGENT")
    good_tag = from_source("tag")
    final, issues = consolidate([bad_yaml, good_tag], METRICS)
    assert [a.source for a in final] == ["tag"]
    assert len(issues) == 1 and issues[0].source == "yaml" and issues[0].severity == "ERROR"
    assert issues[0].table_name == "cat.sch.customers" and "severity" in issues[0].message


def test_dropped_rows_are_reported_with_context():
    final, issues = consolidate([assignment(column="phone", source="app")], METRICS, EXISTING)
    assert final == [] and "column 'phone'" in issues[0].message and issues[0].column_name == "phone"


def test_output_is_sorted_and_deduplicated_by_assignment_id():
    rows = [from_source("tag", column="b"), from_source("tag", column="a"), from_source("tag", column="a")]
    final, _ = consolidate(rows, METRICS)
    assert [a.column_name for a in final] == ["a", "b"]


def test_unknown_source_ranks_last():
    final, _ = consolidate([from_source("mystery", threshold=0.9), from_source("tag", threshold=0.1)], METRICS)
    assert final[0].source == "tag"


def test_case_variants_collapse_to_one_check():
    final, _ = consolidate(
        [from_source("tag", table="Cat.Sch.Customers", column="EMAIL"), from_source("yaml")], METRICS
    )
    assert len(final) == 1 and final[0].source == "yaml"


# ---------------------------------------------------------------------------- shrink guard


def test_shrink_guard():
    check_shrink(0, 0)  # first ever sync
    check_shrink(9, 0)  # too small to judge
    check_shrink(100, 50)  # exactly half is allowed
    check_shrink(100, 200)
    with pytest.raises(SyncAbort, match="100 to 49"):
        check_shrink(100, 49)
    with pytest.raises(SyncAbort):
        check_shrink(10, 0)
