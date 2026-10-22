"""Tag-driven assignments: derive checks from Unity Catalog tags set in Catalog Explorer.

Governance tags a table (opt-in, owner) and its columns (what to check) with the ``dq_`` tags below.
``tag_rules.yml`` maps each tag to a metric, so people tag business intent and never write SQL or
thresholds. Everything here is pure: tags come in as dicts, assignments and issues come out.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from .config_io import ConfigError, read_config_yaml
from .models import SEVERITIES, SCOPES, Assignment, Issue, Metric
from .rendering import template_placeholders

MODES = ("flag", "threshold", "pattern")
_TRUE = {"", "true", "yes", "y", "1"}  # a key-only tag has an empty value, which means "on"
_FALSE = {"false", "no", "n", "0"}
_RULE_KEYS = {"tag", "scope", "metric", "mode", "threshold", "severity", "param", "patterns"}


@dataclass(frozen=True)
class TagRule:
    tag: str
    scope: str  # COLUMN | TABLE
    metric_id: str
    mode: str  # flag: tag on/off | threshold: tag value is the threshold | pattern: value picks a pattern
    threshold: Optional[float] = None
    severity: Optional[str] = None
    param: Optional[str] = None
    patterns: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TagRules:
    prefix: str
    monitor_tag: str
    owner_tag: str
    severity_tag: str
    data_product_tag: str
    default_owner: str
    default_severity: str
    rules: tuple


# --------------------------------------------------------------------------- rules file


def parse_tag_rules(doc: Any) -> TagRules:
    errors: list[str] = []
    if not isinstance(doc, Mapping):
        raise ConfigError(["tag rules must be a mapping"])
    table_tags = doc.get("table_tags") or {}
    defaults = doc.get("defaults") or {}
    for key in ("monitor", "owner", "severity", "data_product"):
        if key not in table_tags:
            errors.append(f"table_tags.{key} is required")
    for key in ("owner", "severity"):
        if key not in defaults:
            errors.append(f"defaults.{key} is required")
    if "prefix" not in doc:
        errors.append("prefix is required")
    raw_rules = doc.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        errors.append("rules must be a non-empty list")
        raw_rules = []
    rules = []
    for i, raw in enumerate(raw_rules, start=1):
        label = raw.get("tag", f"#{i}") if isinstance(raw, Mapping) else f"#{i}"
        if not isinstance(raw, Mapping):
            errors.append(f"rule {label}: expected a mapping")
            continue
        for key in sorted(set(raw) - _RULE_KEYS):
            errors.append(f"rule {label}: unknown key '{key}'")
        missing = [k for k in ("tag", "scope", "metric", "mode") if k not in raw]
        if missing:
            errors.append(f"rule {label}: missing {missing}")
            continue
        threshold = raw.get("threshold")
        rules.append(
            TagRule(
                tag=str(raw["tag"]),
                scope=str(raw["scope"]).upper(),
                metric_id=str(raw["metric"]),
                mode=str(raw["mode"]).lower(),
                threshold=None if threshold is None else float(threshold),
                severity=str(raw["severity"]).upper() if raw.get("severity") else None,
                param=raw.get("param"),
                patterns={str(k).lower(): str(v) for k, v in (raw.get("patterns") or {}).items()},
            )
        )
    if errors:
        raise ConfigError(errors)
    return TagRules(
        prefix=str(doc["prefix"]),
        monitor_tag=str(table_tags["monitor"]),
        owner_tag=str(table_tags["owner"]),
        severity_tag=str(table_tags["severity"]),
        data_product_tag=str(table_tags["data_product"]),
        default_owner=str(defaults["owner"]),
        default_severity=str(defaults["severity"]).upper(),
        rules=tuple(rules),
    )


def load_tag_rules(ref: str = "tag_rules.yml") -> TagRules:
    return parse_tag_rules(read_config_yaml(ref))


def validate_tag_rules(rules: TagRules, metrics: Mapping[str, Metric]) -> list[str]:
    errors = []
    prefix = rules.prefix.lower()
    seen = set()
    meta = (rules.monitor_tag, rules.owner_tag, rules.severity_tag, rules.data_product_tag)
    for tag in meta:
        if not tag.lower().startswith(prefix):
            errors.append(f"table tag '{tag}' must start with prefix '{rules.prefix}'")
    if rules.default_severity not in SEVERITIES:
        errors.append(f"defaults.severity must be one of {SEVERITIES}")
    for r in rules.rules:
        p = f"rule '{r.tag}'"
        if not r.tag.lower().startswith(prefix):
            errors.append(f"{p}: tag must start with prefix '{rules.prefix}'")
        if r.tag.lower() in seen or r.tag.lower() in {t.lower() for t in meta}:
            errors.append(f"{p}: tag name is used more than once")
        seen.add(r.tag.lower())
        if r.scope not in SCOPES:
            errors.append(f"{p}: scope must be one of {SCOPES}")
        if r.mode not in MODES:
            errors.append(f"{p}: mode must be one of {MODES}")
        if r.severity is not None and r.severity not in SEVERITIES:
            errors.append(f"{p}: severity must be one of {SEVERITIES}")
        metric = metrics.get(r.metric_id)
        if metric is None:
            errors.append(f"{p}: unknown metric '{r.metric_id}'")
        elif metric.scope != r.scope:
            errors.append(f"{p}: metric '{r.metric_id}' is {metric.scope}-scoped, rule says {r.scope}")
        if r.mode in ("flag", "pattern") and r.threshold is None:
            errors.append(f"{p}: mode '{r.mode}' needs a default threshold")
        if r.mode == "pattern":
            if not r.param or not r.patterns:
                errors.append(f"{p}: mode 'pattern' needs 'param' and a non-empty 'patterns'")
            elif metric is not None and r.param not in template_placeholders(metric.sql_template):
                errors.append(f"{p}: param '{r.param}' is not used by metric '{r.metric_id}'")
            for name, regex in r.patterns.items():
                try:
                    re.compile(regex)
                except re.error as exc:
                    errors.append(f"{p}: pattern '{name}' is not a valid regex: {exc}")
        elif r.patterns or r.param:
            errors.append(f"{p}: 'param'/'patterns' only apply to mode 'pattern'")
    return errors


# --------------------------------------------------------------------------- derivation


def derive_tag_assignments(
    rules: TagRules,
    table_tags: Iterable[Mapping[str, Any]],
    column_tags: Iterable[Mapping[str, Any]],
) -> tuple[list[Assignment], list[Issue]]:
    """table_tags: {table_name, tag_name, tag_value}; column_tags adds column_name.

    Precedence for severity: table dq_severity tag > rule severity > defaults.severity.
    If two rules produce the same check on a column, the first rule in tag_rules.yml wins (WARN).
    """
    prefix = rules.prefix.lower()
    monitor, owner_tag = rules.monitor_tag.lower(), rules.owner_tag.lower()
    severity_tag, product_tag = rules.severity_tag.lower(), rules.data_product_tag.lower()
    meta = {monitor, owner_tag, severity_tag, product_tag}
    table_rule_tags = {r.tag.lower() for r in rules.rules if r.scope == "TABLE"}
    column_rule_tags = {r.tag.lower() for r in rules.rules if r.scope == "COLUMN"}

    tt: dict[str, dict[str, str]] = defaultdict(dict)
    ct: dict[str, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
    for r in table_tags:
        name = str(r["tag_name"]).strip().lower()
        if name.startswith(prefix):
            tt[str(r["table_name"]).lower()][name] = str(r.get("tag_value") or "").strip()
    for r in column_tags:
        name = str(r["tag_name"]).strip().lower()
        if name.startswith(prefix):
            ct[str(r["table_name"]).lower()][str(r["column_name"]).lower()][name] = str(
                r.get("tag_value") or ""
            ).strip()

    issues: list[Issue] = []

    def warn(message: str, table: str, column: Optional[str] = None) -> None:
        issues.append(Issue("tag", "WARN", message, table, column))

    # Typos and misplaced tags are the main failure mode of a no-code interface: report them all.
    for table, tags in tt.items():
        for name in tags:
            if name in meta or name in table_rule_tags:
                continue
            hint = " - this is a column tag, set it on a column" if name in column_rule_tags else ""
            warn(f"unknown or misplaced table tag '{name}'{hint}", table)
    for table, columns in ct.items():
        for column, tags in columns.items():
            for name in tags:
                if name in column_rule_tags:
                    continue
                hint = " - this is a table tag, set it on the table" if (
                    name in table_rule_tags or name in meta) else ""  # fmt: skip
                warn(f"unknown or misplaced column tag '{name}'{hint}", table, column)

    opted_in = []
    for table in sorted(set(tt) | set(ct)):
        value = tt.get(table, {}).get(monitor)
        if value is None:
            warn(f"has dq tags but is not opted in; add table tag '{rules.monitor_tag}'", table)
        elif value.lower() in _TRUE:
            opted_in.append(table)
        elif value.lower() not in _FALSE:
            warn(f"table tag '{rules.monitor_tag}' has unrecognised value '{value}' (use true/false)", table)

    assignments: list[Assignment] = []
    for table in opted_in:
        tags = tt.get(table, {})
        owner = tags.get(owner_tag) or rules.default_owner
        product = tags.get(product_tag) or None
        table_severity = tags.get(severity_tag, "").upper() or None
        if table_severity and table_severity not in SEVERITIES:
            warn(f"table tag '{rules.severity_tag}' must be one of {SEVERITIES}, got '{table_severity}'", table)
            table_severity = None
        seen: set[str] = set()

        def add(rule: TagRule, value: str, column: Optional[str]) -> None:
            built = _apply_rule(rule, value)
            if isinstance(built, str):
                warn(built, table, column)
                return
            threshold, params = built
            assignment = Assignment(
                table_name=table,
                column_name=column,
                metric_id=rule.metric_id,
                threshold=threshold,
                severity=table_severity or rule.severity or rules.default_severity,
                owner=owner,
                params=params,
                data_product=product,
                is_enabled=True,
                source="tag",
            )
            if assignment.assignment_id in seen:
                warn(f"tag '{rule.tag}' duplicates another tag's check ({rule.metric_id}); ignored", table, column)
                return
            seen.add(assignment.assignment_id)
            assignments.append(assignment)

        for rule in rules.rules:
            if rule.scope == "TABLE":
                if rule.tag.lower() in tags:
                    add(rule, tags[rule.tag.lower()], None)
            else:
                for column in sorted(ct.get(table, {})):
                    value = ct[table][column].get(rule.tag.lower())
                    if value is not None:
                        add(rule, value, column)
    return assignments, issues


def _apply_rule(rule: TagRule, value: str):
    """Returns (threshold, params) or an error string."""
    text = value.strip()
    if rule.mode == "flag":
        if text.lower() in _FALSE:
            return f"tag '{rule.tag}' is set to '{text}', which turns it off; remove the tag instead"
        if text.lower() not in _TRUE:
            return f"tag '{rule.tag}' expects no value or 'true', got '{text}'"
        return rule.threshold, {}
    if rule.mode == "threshold":
        try:
            number = float(text)
        except ValueError:
            return f"tag '{rule.tag}' needs a numeric value, got '{text}'"
        if number < 0:
            return f"tag '{rule.tag}' must be >= 0, got '{text}'"
        return number, {}
    pattern = rule.patterns.get(text.lower())
    if pattern is None:
        return f"tag '{rule.tag}' value '{text}' is not one of: {', '.join(sorted(rule.patterns))}"
    return rule.threshold, {rule.param: pattern}
