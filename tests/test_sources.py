from dq_framework.sources import assignment_from_row, assignments_from_rows

import pytest


def row(**overrides):
    base = {"table_name": "c.s.t", "column_name": "email", "metric_id": "null_pct",
            "threshold": "0.02", "severity": "high", "owner": "o@x", "params": "",
            "data_product": "dp", "is_enabled": "TRUE"}  # fmt: skip
    base.update(overrides)
    return base


def test_csv_style_strings_are_converted():
    a = assignment_from_row(row(), "csv")
    assert (a.threshold, a.severity, a.is_enabled, a.source) == (0.02, "HIGH", True, "csv")
    assert a.params == {} and a.data_product == "dp"


def test_blank_cells_become_none_or_defaults():
    a = assignment_from_row(row(column_name="  ", severity="", data_product="", is_enabled=None), "app")
    assert a.column_name is None and a.severity == "MEDIUM" and a.data_product is None and a.is_enabled


def test_params_accept_json_text_and_spark_map_dicts():
    from_json = assignment_from_row(row(params='{"regex": "^[A-Z]{3}$"}'), "csv")
    from_map = assignment_from_row(row(params={"regex": "^[A-Z]{3}$"}), "app")
    assert from_json.params == from_map.params == {"regex": "^[A-Z]{3}$"}


@pytest.mark.parametrize("value, expected", [("no", False), ("N", False), ("0", False), (False, False), ("yes", True)])
def test_is_enabled_values(value, expected):
    assert assignment_from_row(row(is_enabled=value), "csv").is_enabled is expected


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"owner": ""}, "owner is required"),
        ({"table_name": None}, "table_name is required"),
        ({"metric_id": ""}, "metric_id is required"),
        ({"threshold": "abc"}, "not a number"),
        ({"threshold": None}, "not a number"),
        ({"params": "{not json"}, "Expecting"),
        ({"params": "[1, 2]"}, "JSON object"),
        ({"is_enabled": "maybe"}, "true/false"),
    ],
)
def test_bad_rows_raise_readable_errors(overrides, expected):
    with pytest.raises(ValueError, match=expected):
        assignment_from_row(row(**overrides), "csv")


def test_bad_rows_become_issues_and_good_rows_survive():
    rows = [row(), row(threshold="abc", column_name="phone"), row(column_name="id")]
    good, issues = assignments_from_rows(rows, "csv")
    assert [a.column_name for a in good] == ["email", "id"]
    (issue,) = issues
    assert (issue.source, issue.severity, issue.table_name, issue.column_name) == ("csv", "ERROR", "c.s.t", "phone")
    assert issue.message.startswith("row 2:")
