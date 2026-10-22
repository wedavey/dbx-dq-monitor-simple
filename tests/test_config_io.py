import os
import tempfile

import pytest

from dq_framework.config_io import (
    ConfigError,
    load_assignments,
    load_metrics,
    parse_assignments,
    parse_metrics,
    read_config_text,
)


def metric(**overrides):
    base = dict(metric_id="m1", metric_name="M", dimension="validity", scope="COLUMN",
                direction="MAX", sql_template="count({col})")  # fmt: skip
    base.update(overrides)
    return base


def errors_for(*metrics):
    with pytest.raises(ConfigError) as info:
        parse_metrics({"metrics": list(metrics)})
    return info.value.errors


def test_shipped_metric_catalog_is_valid():
    metrics = load_metrics()
    assert {m.metric_id for m in metrics} >= {
        "null_pct", "blank_pct", "distinct_ratio", "duplicate_pct",
        "regex_fail_pct", "row_count", "hours_since_load",
    }  # fmt: skip
    assert all(m.version >= 1 and m.is_active for m in metrics)


def test_valid_custom_metric_parses():
    parsed = parse_metrics({"metrics": [metric(sql_template="count(DISTINCT {col}) / '{n}'")]})
    assert parsed[0].scope == "COLUMN" and parsed[0].version == 1


def test_metric_errors_are_all_reported():
    errs = errors_for(
        metric(metric_id="Bad-Id", scope="CELL", direction="UP", dimension="vibes"),
        metric(metric_id="m2", sql_template="count(*)"),  # column metric without {col}
        metric(metric_id="m3", scope="TABLE"),  # table metric with {col}
        metric(metric_id="m4", sql_template="count({col}) > {limit}"),  # param outside quotes
        metric(metric_id="m5", sql_template="count({col}); DROP TABLE x"),
        metric(metric_id="m6", version=0),
        metric(metric_id="m1"),
        metric(metric_id="m1"),  # duplicate
    )  # fmt: skip
    text = "\n".join(errs)
    for expected in ["lower_snake_case", "scope must be", "direction must be", "dimension must be",
                     "must use {col}", "cannot use {col}", "single-quoted", "';'", "version",
                     "duplicate metric_id"]:  # fmt: skip
        assert expected in text, expected


def test_metric_missing_and_unknown_keys():
    errs = errors_for({"metric_id": "x", "colour": "red"})
    assert any("unknown key 'colour'" in e for e in errs)
    assert any("missing 'sql_template'" in e for e in errs)


def test_catalog_must_be_a_mapping_with_metrics():
    with pytest.raises(ConfigError):
        parse_metrics(["nope"])


# ---------------------------------------------------------------------------- assignments


def test_assignment_precedence_and_catalog_substitution():
    doc = {
        "defaults": {"owner": "default@x", "severity": "LOW", "data_product": "dp"},
        "tables": [
            {"name": "${catalog}.s.t", "owner": "table@x", "severity": "HIGH", "checks": [
                {"column": "a", "metric": "null_pct", "threshold": 0.1},
                {"column": "b", "metric": "null_pct", "threshold": "0.2", "owner": "check@x", "severity": "critical"},
                {"metric": "row_count", "threshold": 5, "enabled": False},
                {"column": "c", "metric": "regex_fail_pct", "threshold": 0, "params": {"regex": 5}},
            ]},
            {"name": "c2.s.u", "checks": [{"column": "a", "metric": "null_pct", "threshold": 0}]},
        ],
    }  # fmt: skip
    a, b, rows, rx, u = parse_assignments(doc, catalog="dev")
    assert a.table_name == "dev.s.t" and a.owner == "table@x" and a.severity == "HIGH"
    assert (b.owner, b.severity, b.threshold) == ("check@x", "CRITICAL", 0.2)
    assert rows.column_name is None and rows.is_enabled is False
    assert rx.params == {"regex": "5"}
    assert (u.owner, u.severity, u.data_product) == ("default@x", "LOW", "dp")


def test_assignment_errors():
    doc = {"tables": [
        {"name": "c.s.t", "bogus": 1, "checks": [
            {"metric": "null_pct"},  # no threshold
            {"column": "a", "metric": "null_pct", "threshold": 1},  # no owner anywhere
            {"column": "a", "metric": "null_pct", "threshold": "high", "owner": "o"},
            {"column": "a", "metric": "null_pct", "threshold": 1, "owner": "o", "typo": 1},
            "not a mapping",
        ]},
        {"nameless": True},
    ]}  # fmt: skip
    with pytest.raises(ConfigError) as info:
        parse_assignments(doc)
    text = "\n".join(info.value.errors)
    for expected in ["unknown key 'bogus'", "'metric' and 'threshold' are required", "no owner",
                     "threshold must be a number", "unknown key 'typo'", "expected a mapping",
                     "expected a mapping with 'name'"]:  # fmt: skip
        assert expected in text, expected


def test_empty_assignment_file_is_fine():
    assert parse_assignments(None) == []
    assert parse_assignments({"tables": []}) == []


def test_shipped_assignment_files_load():
    assert load_assignments("assignments.yml") == []
    synthetic = load_assignments("dev/synthetic_assignments.yml", catalog="dev_gov")
    assert len(synthetic) == 13
    assert {a.table_name for a in synthetic} == {
        "dev_gov.dq_synthetic.customers", "dev_gov.dq_synthetic.orders"}
    assert any(a.params.get("regex") == "^(NEW|PAID|SHIPPED)$" for a in synthetic)


def test_filesystem_refs_are_read_from_disk():
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "x.yml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("tables: []\n")
        assert read_config_text(path).strip() == "tables: []"
        assert load_assignments(path) == []


def test_bad_yaml_is_a_config_error():
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "bad.yml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("tables: [unclosed\n")
        with pytest.raises(ConfigError):
            load_assignments(path)
