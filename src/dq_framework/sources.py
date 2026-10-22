"""Parse assignment rows from tabular low-code sources (a governance app table, a CSV/Excel export).

Row problems become Issues shown back to the people who own the source; they never fail the sync.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Optional

from .models import Assignment, Issue

_TRUE = {"true", "yes", "y", "1"}
_FALSE = {"false", "no", "n", "0"}


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_bool(value: Any, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"cannot read '{value}' as true/false")


def _to_params(value: Any) -> dict:
    if value is None or value == "":
        return {}
    if isinstance(value, Mapping):
        return {str(k): str(v) for k, v in value.items()}
    data = json.loads(str(value))
    if not isinstance(data, dict):
        raise ValueError("params must be a JSON object, e.g. {\"regex\": \"^[A-Z]{3}$\"}")
    return {str(k): str(v) for k, v in data.items()}


def assignment_from_row(row: Mapping[str, Any], source: str) -> Assignment:
    """Raises ValueError with a human-readable message when the row is unusable."""
    table = _text(row.get("table_name"))
    metric = _text(row.get("metric_id"))
    owner = _text(row.get("owner"))
    if not table:
        raise ValueError("table_name is required")
    if not metric:
        raise ValueError("metric_id is required")
    if not owner:
        raise ValueError("owner is required")
    try:
        threshold = float(row.get("threshold"))
    except (TypeError, ValueError):
        raise ValueError(f"threshold '{row.get('threshold')}' is not a number") from None
    return Assignment(
        table_name=table,
        column_name=_text(row.get("column_name")),
        metric_id=metric,
        threshold=threshold,
        severity=(_text(row.get("severity")) or "MEDIUM").upper(),
        owner=owner,
        params=_to_params(row.get("params")),
        data_product=_text(row.get("data_product")),
        is_enabled=_to_bool(row.get("is_enabled")),
        source=source,
    )


def assignments_from_rows(
    rows: Iterable[Mapping[str, Any]], source: str
) -> tuple[list[Assignment], list[Issue]]:
    assignments: list[Assignment] = []
    issues: list[Issue] = []
    for number, row in enumerate(rows, start=1):
        try:
            assignments.append(assignment_from_row(row, source))
        except ValueError as exc:  # includes json.JSONDecodeError
            issues.append(
                Issue(source, "ERROR", f"row {number}: {exc}", _text(row.get("table_name")),
                      _text(row.get("column_name")))
            )  # fmt: skip
    return assignments, issues
