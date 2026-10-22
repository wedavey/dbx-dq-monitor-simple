"""Consolidate assignments from every source into the one table the runner reads.

Sources are independent feeds (yaml, app, csv, tag). Each is validated; the highest-priority valid
row for a given check wins, so an explicit YAML row can override or disable a tag-derived check.
"""

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

SOURCE_PRIORITY = ("yaml", "app", "csv", "tag")


class SyncAbort(RuntimeError):
    """Refuse to overwrite the assignments table with a suspiciously small result."""


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


def consolidate(
    candidates: Iterable[Assignment],
    metrics: Mapping[str, Metric],
    existing_columns: Optional[Mapping[str, set[str]]] = None,
    priority: tuple[str, ...] = SOURCE_PRIORITY,
) -> tuple[list[Assignment], list[Issue]]:
    """Validate every candidate, then keep the highest-priority valid row per assignment_id.

    Invalid rows are dropped with an ERROR issue and do not suppress a lower-priority valid row.
    Winners may be disabled (is_enabled=False), which is how YAML switches off a tag-derived check.
    """
    issues: list[Issue] = []
    valid: list[Assignment] = []
    for a in candidates:
        errors = validate_assignment(a, metrics, existing_columns)
        for message in errors:
            issues.append(Issue(a.source, "ERROR", f"{a.metric_id}: {message}", a.table_name, a.column_name))
        if not errors:
            valid.append(a)

    def rank(a: Assignment) -> int:
        return priority.index(a.source) if a.source in priority else len(priority)

    winners: dict[str, Assignment] = {}
    for a in sorted(valid, key=rank):  # sorted() is stable: ties keep input order
        winners.setdefault(a.assignment_id, a)
    return [winners[key] for key in sorted(winners)], issues


def check_shrink(
    existing_enabled: int, new_enabled: int, min_ratio: float = 0.5, min_existing: int = 10
) -> None:
    """information_schema silently returns nothing for tags the principal lost access to.

    Without this guard a revoked grant would wipe every tag-derived assignment on the next sync.
    """
    if existing_enabled >= min_existing and new_enabled < existing_enabled * min_ratio:
        raise SyncAbort(
            f"refusing to overwrite: enabled assignments would drop from {existing_enabled} to "
            f"{new_enabled}. Check the service principal's access to the tagged catalogs, or rerun "
            "with --allow-shrink if the drop is intended."
        )
