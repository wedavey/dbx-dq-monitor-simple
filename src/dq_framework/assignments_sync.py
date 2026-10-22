"""Validate the YAML assignments before they are written to the table the runner reads."""

from __future__ import annotations

import math
from typing import Iterable, Mapping, Optional

from .models import SEVERITIES, Assignment, Issue, Metric
from .rendering import (
    RESERVED_PARAMS,
    RenderError,
    is_valid_column_name,
    is_valid_table_name,
    render_filter,
    template_placeholders,
)


def validate_assignment(
    a: Assignment,
    metrics: Mapping[str, Metric],
    existing_columns: Optional[Mapping[str, set[str]]] = None,
) -> list[str]:
    errors = []
    if not is_valid_table_name(a.table_name):
        errors.append(f"invalid table name '{a.table_name}' (expected catalog.schema.table)")
    metric = metrics.get(a.metric_id)
    if metric is None:
        errors.append(f"unknown metric '{a.metric_id}'")
    elif not metric.is_active:
        errors.append(f"metric '{a.metric_id}' is inactive")
    else:
        if metric.scope == "COLUMN" and not a.column_name:
            errors.append(f"metric '{a.metric_id}' needs a column")
        if metric.scope == "TABLE" and a.column_name:
            errors.append(f"metric '{a.metric_id}' is table-level; leave the column empty")
        needed = template_placeholders(metric.sql_template) - RESERVED_PARAMS
        for name in sorted(needed - set(a.params or {})):
            errors.append(f"metric '{a.metric_id}' needs parameter '{name}'")
    if a.column_name and not is_valid_column_name(a.column_name):
        errors.append(f"invalid column name '{a.column_name}'")
    if a.severity not in SEVERITIES:
        errors.append(f"severity '{a.severity}' must be one of {SEVERITIES}")
    if not a.owner:
        errors.append("owner is required")
    if isinstance(a.threshold, bool) or not isinstance(a.threshold, (int, float)) \
            or not math.isfinite(a.threshold):  # fmt: skip
        errors.append("threshold must be a finite number")
    try:
        render_filter(a)
    except RenderError as exc:
        errors.append(str(exc))
    if existing_columns is not None and is_valid_table_name(a.table_name):
        columns = existing_columns.get(a.table_name.lower())
        if columns is None:
            errors.append("table not found, or not visible to the service principal")
        elif a.column_name and a.column_name.lower() not in columns:
            errors.append(f"column '{a.column_name}' not found in table")
    return errors


def validate_all(
    candidates: Iterable[Assignment],
    metrics: Mapping[str, Metric],
    existing_columns: Optional[Mapping[str, set[str]]] = None,
) -> tuple[list[Assignment], list[Issue]]:
    """Keep the valid assignments, sorted by id. Invalid or duplicate rows are dropped with an ERROR issue."""
    issues: list[Issue] = []
    valid: dict[str, Assignment] = {}
    for a in candidates:
        errors = validate_assignment(a, metrics, existing_columns)
        if not errors and a.assignment_id in valid:
            errors = ["duplicate check (same table, column and metric)"]
        for message in errors:
            issues.append(Issue("ERROR", f"{a.metric_id}: {message}", a.table_name, a.column_name))
        if not errors:
            valid[a.assignment_id] = a
    return [valid[key] for key in sorted(valid)], issues
