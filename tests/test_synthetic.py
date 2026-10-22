"""End-to-end test of everything except SQL execution.

YAML -> validate -> engine -> results -> synthetic.verify. The fake query
function computes metric values in Python from the synthetic rows, so this checks that the
framework's wiring (thresholds, issues) produces the outcomes we document.
SQL semantics are covered separately by tests/integration (needs pyspark).
"""

import re
from datetime import datetime, timezone

from dq_framework import synthetic
from dq_framework.config_io import load_assignments
from dq_framework.engine import run_checks
from dq_framework.assignments_sync import validate_all

from helpers import METRICS

NOW = datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc)
CATALOG = "dev_gov"
SCHEMA = synthetic.SYNTHETIC_SCHEMA

TABLES = {
    "customers": (synthetic.customer_rows(NOW), ["customer_id", "email", "country_code", "loaded_at"]),
    "orders": (synthetic.order_rows(NOW), ["order_id", "amount", "status", "loaded_at"]),
}


def column_values(table, column):
    rows, columns = TABLES[table]
    index = columns.index(column)
    return [r[index] for r in rows]


def python_metric(a):
    """Python re-implementation of the seed metrics, used only as a fake for the SQL engine."""
    table = a.table_name.split(".")[-1]
    if a.metric_id == "row_count":
        return float(len(TABLES[table][0]))
    values = column_values(table, a.column_name)
    non_null = [v for v in values if v is not None]
    if a.metric_id == "null_pct":
        return 1 - len(non_null) / len(values)
    if a.metric_id == "duplicate_pct":
        return 1 - len(set(non_null)) / len(non_null)
    if a.metric_id == "regex_fail_pct":
        pattern = a.params["regex"]
        return sum(1 for v in non_null if not re.search(pattern, str(v))) / len(values)
    if a.metric_id == "hours_since_load":
        return (NOW - max(non_null)).total_seconds() / 3600
    raise AssertionError(a.metric_id)


def fake_query_for(assignments):
    def query(sql):
        return {alias: python_metric(assignments[int(alias[1:])]) for alias in re.findall(r"AS (m\d+)", sql)}

    return query


def run_pipeline():
    candidates = load_assignments("dev/synthetic_assignments.yml", CATALOG)
    existing = {
        f"{CATALOG}.{SCHEMA}.{t}": {c.lower() for c in cols} for t, (_, cols) in TABLES.items()
    }
    final, problems = validate_all(candidates, METRICS, existing)
    results = run_checks(final, METRICS.values(), fake_query_for(final), "r1", NOW)
    result_rows = [{"assignment_id": r.assignment_id, "status": r.status} for r in results]
    issues = [{"severity": i.severity, "message": i.message} for i in problems]
    return final, results, result_rows, issues


def test_synthetic_defect_rates_are_exact():
    customers = synthetic.customer_rows(NOW)
    assert len(customers) == 1000
    assert sum(r[1] is None for r in customers) == 50  # 5% null emails
    assert sum(r[2] == "XX1" for r in customers) == 20  # 2% invalid country codes
    assert len({r[0] for r in customers}) == 1000
    assert all((NOW - r[3]).total_seconds() == 48 * 3600 for r in customers)
    orders = synthetic.order_rows(NOW)
    assert len(orders) == 5000 and len({r[0] for r in orders}) == 5000
    assert {r[2] for r in orders} == {"NEW", "PAID", "SHIPPED"}


def test_framework_produces_exactly_the_documented_outcomes():
    final, results, result_rows, issues = run_pipeline()
    problems = synthetic.verify(CATALOG, result_rows, issues)
    assert problems == []
    assert len(final) == len(synthetic.expected_status(CATALOG)) == 12
    assert all(r.status in ("PASS", "FAIL") for r in results)  # nothing errored


def test_bad_yaml_row_becomes_an_issue_not_a_result():
    final, _, _, issues = run_pipeline()
    assert any("no_such_column" in i["message"] for i in issues)
    assert not any("no_such_column" in a.assignment_id for a in final)


def test_verify_detects_a_wrong_outcome_a_missing_issue_and_an_unexpected_result():
    _, _, result_rows, issues = run_pipeline()
    flipped = [dict(r) for r in result_rows]
    flipped[0]["status"] = "FAIL" if flipped[0]["status"] == "PASS" else "PASS"
    assert any("expected" in p for p in synthetic.verify(CATALOG, flipped, issues))
    assert any("expected a ERROR issue" in p for p in synthetic.verify(CATALOG, result_rows, []))
    extra = result_rows + [{"assignment_id": f"{CATALOG}.{SCHEMA}.orders|x|null_pct", "status": "PASS"}]
    assert any("unexpected result" in p for p in synthetic.verify(CATALOG, extra, issues))


def test_verify_ignores_results_for_other_catalogs_and_schemas():
    _, _, result_rows, issues = run_pipeline()
    noise = [{"assignment_id": "prod.crm.customer|email|null_pct", "status": "FAIL"}]
    assert synthetic.verify(CATALOG, result_rows + noise, issues) == []
