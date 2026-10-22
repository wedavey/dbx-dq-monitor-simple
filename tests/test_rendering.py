import pytest

from dq_framework.models import Assignment, Metric
from dq_framework.naming import TableNames
from dq_framework.rendering import (
    RenderError,
    build_select,
    escape_sql_literal,
    quote_column,
    quote_table,
    render_expression,
    render_filter,
    unquoted_placeholders,
)

from helpers import METRICS, assignment


def test_quote_table_and_column():
    assert quote_table("main.crm.customer") == "`main`.`crm`.`customer`"
    assert quote_column("email") == "`email`"


@pytest.mark.parametrize("bad", ["a.b", "a.b.c.d", "a.b.c; DROP TABLE x", "a.b.`c`", "", "a..c"])
def test_quote_table_rejects_bad_names(bad):
    with pytest.raises(RenderError):
        quote_table(bad)


@pytest.mark.parametrize("bad", ["x`; DROP", "a b", "", "a.b", "x)"])
def test_quote_column_rejects_bad_names(bad):
    with pytest.raises(RenderError):
        quote_column(bad)


def test_render_null_pct_quotes_the_column():
    sql = render_expression(METRICS["null_pct"], assignment(column="email"))
    assert sql == "avg(CASE WHEN `email` IS NULL THEN 1.0 ELSE 0.0 END)"


def test_render_rejects_injection_via_column():
    with pytest.raises(RenderError):
        render_expression(METRICS["null_pct"], assignment(column="x) OR (1=1"))


def test_regex_param_is_escaped_for_a_sql_literal():
    a = assignment("regex_fail_pct", "code", params={"regex": r"^it's \d+$"})
    sql = render_expression(METRICS["regex_fail_pct"], a)
    assert r"RLIKE '^it\'s \\d+$'" in sql


def test_param_values_are_not_substituted_twice():
    a = assignment("regex_fail_pct", "code", params={"regex": "{col}"})
    assert "RLIKE '{col}'" in render_expression(METRICS["regex_fail_pct"], a)


def test_regex_quantifier_braces_survive():
    a = assignment("regex_fail_pct", "code", params={"regex": "^[A-Z]{3}$"})
    assert "RLIKE '^[A-Z]{3}$'" in render_expression(METRICS["regex_fail_pct"], a)


def test_missing_param_is_an_error():
    with pytest.raises(RenderError, match="missing parameter 'regex'"):
        render_expression(METRICS["regex_fail_pct"], assignment("regex_fail_pct", "code"))


def test_scope_mismatches_are_errors():
    with pytest.raises(RenderError):
        render_expression(METRICS["row_count"], Assignment("a.b.c", "row_count", 1, "LOW", "o", "col"))
    with pytest.raises(RenderError):
        render_expression(METRICS["null_pct"], assignment(column=None))


def test_filter_is_a_reserved_name():
    metric = Metric("m", "m", "validity", "COLUMN", "count({col}) + {filter}", "MAX")
    with pytest.raises(RenderError):
        render_expression(metric, assignment(params={"filter": "1=1"}))


def test_unquoted_placeholder_detection():
    assert unquoted_placeholders("x > {n}") == {"n"}
    assert unquoted_placeholders("a RLIKE '{regex}'") == set()
    # an empty literal ('') before the placeholder must not confuse the scan
    assert unquoted_placeholders("trim({col}) = '' AND a RLIKE '{regex}'") == {"col"}


def test_escape_sql_literal():
    assert escape_sql_literal("a'b\\c") == "a\\'b\\\\c"


@pytest.mark.parametrize("bad", ["a; DROP TABLE x", "x -- comment", "x /* c */"])
def test_filter_rejects_dangerous_text(bad):
    with pytest.raises(RenderError):
        render_filter(assignment(params={"filter": bad}))


def test_filter_passthrough_and_blank():
    assert render_filter(assignment(params={"filter": " load_date = current_date() "})) == "load_date = current_date()"
    assert render_filter(assignment()) is None
    assert render_filter(assignment(params={"filter": "  "})) is None


def test_build_select():
    sql = build_select("a.b.c", [("m0", "count(*)"), ("m1", "avg(x)")], "d > 1")
    assert sql == (
        "SELECT CAST((count(*)) AS DOUBLE) AS m0, CAST((avg(x)) AS DOUBLE) AS m1 "
        "FROM `a`.`b`.`c` WHERE d > 1"
    )


def test_table_names_are_validated():
    names = TableNames("main", "dq")
    assert names.results == "`main`.`dq`.`results`"
    with pytest.raises(ValueError):
        TableNames("main", "dq; DROP")
