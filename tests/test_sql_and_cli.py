import os
import re
import tempfile
from importlib import resources

import pytest

from dq_framework import cli, spark_io
from dq_framework.sqlfiles import load_sql, split_statements


def ddl_columns(table):
    text = resources.files("dq_framework").joinpath("sql", "ddl.sql").read_text(encoding="utf-8")
    body = re.search(rf"\$\{{schema\}}\.{table} \((.*?)\n\)", text, re.S).group(1)
    return [m.group(1) for m in re.finditer(r"^  ([a-z_]+)\s+[A-Z]", body, re.M)]


def ddl_string_columns(ddl):
    # split on commas that are not inside MAP<K, V>
    return [part.split()[0] for part in re.split(r",\s*(?![^<>]*>)", ddl)]


def test_split_statements_drops_comments_and_empties():
    text = "-- header; with semicolon\nCREATE TABLE a (x INT);\n\n-- note\nSELECT 1;\n"
    assert split_statements(text) == ["CREATE TABLE a (x INT)", "SELECT 1"]


def test_ddl_and_views_load_with_substitution():
    ddl = load_sql("ddl.sql", "main", "dq")
    views = load_sql("views.sql", "main", "dq")
    assert all(s.startswith("CREATE") for s in ddl + views)
    assert all("${" not in s for s in ddl + views)
    assert sum("CREATE TABLE" in s for s in ddl) == 4 and len(views) == 4
    assert any("main.dq.results" in s for s in ddl)


def test_sql_rejects_unsafe_identifiers():
    with pytest.raises(ValueError):
        load_sql("ddl.sql", "main; DROP", "dq")


def test_python_writers_match_table_column_order():
    """INSERT OVERWRITE ... SELECT * is positional, so the DDL and the DataFrame schema must agree."""
    assert ddl_string_columns(spark_io.METRICS_DDL) == ddl_columns("metric_catalog")
    assert ddl_string_columns(spark_io.ASSIGNMENTS_DDL) == ddl_columns("assignments")
    assert ddl_string_columns(spark_io.ISSUES_DDL) == ddl_columns("assignment_issues")
    results = ddl_columns("results")
    assert results[-1] == "run_date"  # generated column, not written by Python
    assert ddl_string_columns(spark_io.RESULTS_DDL) == results[:-1]


def test_alert_queries_ship_and_reference_the_views():
    text = resources.files("dq_framework").joinpath("sql", "alerts.sql").read_text(encoding="utf-8")
    for needle in ["v_latest_results", "v_missing_results", "assignment_issues", "alert_count"]:
        assert needle in text


def test_job_parameter_splitting():
    assert cli._split("a, b,c") == ["a", "b", "c"]
    assert cli._split("none") == [] and cli._split("") == [] and cli._split(None) == []


def test_validate_config_passes_on_shipped_config():
    cli.validate_config_main([])  # raises SystemExit on any problem


def test_validate_config_fails_on_bad_config():
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "metrics.yml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("metrics:\n  - {metric_id: Bad Id}\n")
        with pytest.raises(SystemExit) as info:
            cli.validate_config_main(["--metrics-file", path])
        assert "config validation failed" in str(info.value)


def test_seed_and_verify_refuse_to_run_in_prod():
    for main in (cli.seed_synthetic_main, cli.verify_synthetic_main):
        with pytest.raises(SystemExit, match="refusing"):
            main(["--catalog", "prod", "--environment", "prod"])
