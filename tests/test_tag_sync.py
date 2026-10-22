from dataclasses import replace

from dq_framework.tag_sync import (
    TagRule,
    derive_tag_assignments,
    load_tag_rules,
    parse_tag_rules,
    validate_tag_rules,
)

from helpers import METRICS

RULES = load_tag_rules()
T = "cat.sch.customers"


def table_tag(name, value="", table=T):
    return {"table_name": table, "tag_name": name, "tag_value": value}


def col_tag(column, name, value="", table=T):
    return {"table_name": table, "column_name": column, "tag_name": name, "tag_value": value}


def derive(table_tags=(), column_tags=()):
    return derive_tag_assignments(RULES, table_tags, column_tags)


def by_id(assignments):
    return {a.assignment_id: a for a in assignments}


def messages(issues):
    return [i.message for i in issues]


# ---------------------------------------------------------------------------- rules file


def test_shipped_rules_are_valid_against_the_shipped_catalog():
    assert validate_tag_rules(RULES, METRICS) == []


def test_rule_validation_catches_mistakes():
    bad = replace(RULES, rules=(
        TagRule("dq_a", "COLUMN", "nope", "flag", 0.0),  # unknown metric
        TagRule("dq_b", "TABLE", "null_pct", "flag", 0.0),  # wrong scope
        TagRule("dq_c", "COLUMN", "regex_fail_pct", "pattern", 0.0, param="regex", patterns={"x": "("}),
        TagRule("dq_d", "COLUMN", "regex_fail_pct", "pattern", 0.0, param="nope", patterns={"x": "a"}),
        TagRule("other_e", "COLUMN", "null_pct", "flag", 0.0),  # missing prefix
        TagRule("dq_a", "COLUMN", "null_pct", "flag", 0.0),  # duplicate tag
        TagRule("dq_f", "COLUMN", "null_pct", "flag"),  # flag without threshold
        TagRule("dq_g", "COLUMN", "null_pct", "threshold", patterns={"x": "a"}),  # stray patterns
    ))  # fmt: skip
    text = "\n".join(validate_tag_rules(bad, METRICS))
    for expected in ["unknown metric 'nope'", "-scoped, rule says", "not a valid regex",
                     "param 'nope' is not used", "must start with prefix", "used more than once",
                     "needs a default threshold", "only apply to mode 'pattern'"]:  # fmt: skip
        assert expected in text, expected


def test_parse_tag_rules_requires_structure():
    import pytest
    from dq_framework.config_io import ConfigError

    with pytest.raises(ConfigError):
        parse_tag_rules({"prefix": "dq_"})


# ---------------------------------------------------------------------------- derivation


def test_tags_without_opt_in_are_ignored_with_a_warning():
    assignments, issues = derive(column_tags=[col_tag("email", "dq_required")])
    assert assignments == []
    assert any("not opted in" in m for m in messages(issues))


def test_flag_rule_key_only_and_true_values():
    for value in ("", "true", "TRUE", "yes"):
        a, _ = derive([table_tag("dq_monitor")], [col_tag("email", "dq_required", value)])
        (check,) = a
        assert (check.metric_id, check.threshold, check.severity, check.source) == ("null_pct", 0.0, "HIGH", "tag")
        assert check.assignment_id == f"{T}|email|null_pct"


def test_flag_set_to_false_is_reported_not_applied():
    a, issues = derive([table_tag("dq_monitor", "true")], [col_tag("email", "dq_required", "false")])
    assert a == [] and any("turns it off" in m for m in messages(issues))


def test_threshold_rule_uses_the_tag_value():
    a, _ = derive([table_tag("dq_monitor")], [col_tag("email", "dq_max_null_pct", "0.02")])
    assert a[0].threshold == 0.02 and a[0].metric_id == "null_pct"


def test_threshold_rule_rejects_non_numbers_and_negatives():
    for bad in ("two percent", "-1", ""):
        a, issues = derive([table_tag("dq_monitor")], [col_tag("email", "dq_max_null_pct", bad)])
        assert a == [] and any("numeric" in m or ">= 0" in m for m in messages(issues)), bad


def test_explicit_threshold_beats_flag_when_both_tags_exist():
    a, issues = derive([table_tag("dq_monitor")],
                       [col_tag("email", "dq_required"), col_tag("email", "dq_max_null_pct", "0.05")])  # fmt: skip
    assert len(a) == 1 and a[0].threshold == 0.05
    assert any("duplicates another tag" in m for m in messages(issues))


def test_pattern_rule_picks_a_named_pattern_case_insensitively():
    a, _ = derive([table_tag("dq_monitor")], [col_tag("email", "dq_format", "EMAIL")])
    (check,) = a
    assert check.metric_id == "regex_fail_pct" and check.threshold == 0.01
    assert check.params["regex"].startswith("^[^@")


def test_unknown_pattern_lists_the_options():
    a, issues = derive([table_tag("dq_monitor")], [col_tag("email", "dq_format", "emial")])
    assert a == [] and any("email" in m and "uuid" in m for m in messages(issues))


def test_table_scoped_rule():
    a, _ = derive([table_tag("dq_monitor"), table_tag("dq_min_rows", "1000")])
    (check,) = a
    assert (check.metric_id, check.column_name, check.threshold, check.severity) == ("row_count", None, 1000.0, "HIGH")
    assert check.assignment_id == f"{T}||row_count"


def test_owner_and_data_product_come_from_table_tags_with_a_default_owner():
    tags = [table_tag("dq_monitor"), table_tag("dq_owner", "crm@x"), table_tag("dq_data_product", "crm")]
    a, _ = derive(tags, [col_tag("id", "dq_unique")])
    assert (a[0].owner, a[0].data_product) == ("crm@x", "crm")
    b, _ = derive([table_tag("dq_monitor")], [col_tag("id", "dq_unique")])
    assert b[0].owner == RULES.default_owner and b[0].data_product is None


def test_severity_precedence_table_tag_then_rule_then_default():
    cols = [col_tag("id", "dq_unique"), col_tag("id", "dq_not_blank")]
    plain, _ = derive([table_tag("dq_monitor")], cols)
    assert {a.metric_id: a.severity for a in plain} == {"duplicate_pct": "CRITICAL", "blank_pct": "MEDIUM"}
    low, _ = derive([table_tag("dq_monitor"), table_tag("dq_severity", "low")], cols)
    assert {a.severity for a in low} == {"LOW"}
    bad, issues = derive([table_tag("dq_monitor"), table_tag("dq_severity", "urgent")], cols)
    assert {a.metric_id: a.severity for a in bad}["duplicate_pct"] == "CRITICAL"
    assert any("dq_severity" in m for m in messages(issues))


def test_typos_and_misplaced_tags_are_reported():
    _, issues = derive(
        [table_tag("dq_monitor"), table_tag("dq_unique")],  # column tag on the table
        [col_tag("a", "dq_requird"), col_tag("b", "dq_min_rows")],  # typo; table tag on a column
    )
    text = "\n".join(messages(issues))
    assert "'dq_requird'" in text
    assert "column tag, set it on a column" in text
    assert "table tag, set it on the table" in text


def test_explicit_opt_out_is_silent():
    a, issues = derive([table_tag("dq_monitor", "false")], [col_tag("email", "dq_required")])
    assert a == [] and issues == []


def test_non_dq_tags_are_ignored_and_names_are_case_insensitive():
    a, issues = derive(
        [table_tag("DQ_MONITOR", "true"), table_tag("pii", "yes")],
        [col_tag("Email", "DQ_REQUIRED"), col_tag("Email", "classification", "pii")],
    )
    assert len(a) == 1 and issues == []
    assert a[0].assignment_id == f"{T}|email|null_pct"


def test_output_is_deterministic_and_multi_table():
    tags = [table_tag("dq_monitor", table="c.s.b"), table_tag("dq_monitor", table="c.s.a")]
    cols = [col_tag("z", "dq_unique", table="c.s.a"), col_tag("y", "dq_unique", table="c.s.a"),
            col_tag("x", "dq_unique", table="c.s.b")]  # fmt: skip
    first, _ = derive(tags, cols)
    second, _ = derive(list(reversed(tags)), list(reversed(cols)))
    assert [a.assignment_id for a in first] == [a.assignment_id for a in second]
    assert [a.assignment_id for a in first][:2] == ["c.s.a|y|duplicate_pct", "c.s.a|z|duplicate_pct"]
