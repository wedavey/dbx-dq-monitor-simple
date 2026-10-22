"""Turn a metric template plus an assignment into a safe SQL aggregate expression.

Safety model: identifiers are validated and back-quoted; parameter values may only be used inside
single-quoted string literals (enforced when the catalog is validated) and are escaped; the one
free-text parameter, ``filter``, is restricted to the reviewed YAML assignments.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from .models import Assignment, Metric

_TABLE_RE = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")
_COLUMN_RE = re.compile(r"^[A-Za-z0-9_]+$")
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_FORBIDDEN_IN_FILTER = (";", "--", "/*", "*/")
RESERVED_PARAMS = {"col", "filter"}


class RenderError(ValueError):
    """The assignment cannot be turned into SQL. Becomes an ERROR result, never a crash."""


def is_valid_table_name(name: str) -> bool:
    return bool(_TABLE_RE.match(name or ""))


def is_valid_column_name(name: str) -> bool:
    return bool(_COLUMN_RE.match(name or ""))


def quote_table(name: str) -> str:
    if not is_valid_table_name(name):
        raise RenderError(f"invalid table name {name!r}; expected catalog.schema.table")
    return ".".join(f"`{part}`" for part in name.split("."))


def quote_column(name: str) -> str:
    if not is_valid_column_name(name):
        raise RenderError(f"invalid column name {name!r}")
    return f"`{name}`"


def escape_sql_literal(value: str) -> str:
    """Escape for use inside a single-quoted Spark SQL string literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def template_placeholders(template: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(template))


def unquoted_placeholders(template: str) -> set[str]:
    """Placeholders that do not sit inside a single-quoted literal (a catalog error for params)."""
    outside = set()
    for match in _PLACEHOLDER_RE.finditer(template):
        quotes_before = template.count("'", 0, match.start())
        if quotes_before % 2 == 0:
            outside.add(match.group(1))
    return outside


def render_expression(metric: Metric, assignment: Assignment) -> str:
    params = assignment.params or {}
    if metric.scope == "COLUMN":
        if not assignment.column_name:
            raise RenderError(f"metric '{metric.metric_id}' is column-scoped but no column was given")
    elif assignment.column_name:
        raise RenderError(f"metric '{metric.metric_id}' is table-scoped; remove the column")

    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name == "col":
            if metric.scope != "COLUMN":
                raise RenderError("template uses {col} but the metric is table-scoped")
            return quote_column(assignment.column_name)
        if name in RESERVED_PARAMS or name not in params:
            raise RenderError(f"missing parameter '{name}' required by metric '{metric.metric_id}'")
        return escape_sql_literal(str(params[name]))

    return _PLACEHOLDER_RE.sub(substitute, metric.sql_template)


def render_filter(assignment: Assignment) -> Optional[str]:
    text = (assignment.params or {}).get("filter")
    if text is None or not str(text).strip():
        return None
    text = str(text).strip()
    for token in _FORBIDDEN_IN_FILTER:
        if token in text:
            raise RenderError(f"filter may not contain {token!r}")
    return text


def build_select(table_name: str, expressions: Iterable[tuple[str, str]], where: Optional[str]) -> str:
    """One scan of the table computing every expression, each cast to DOUBLE."""
    columns = ", ".join(f"CAST(({expr}) AS DOUBLE) AS {alias}" for alias, expr in expressions)
    sql = f"SELECT {columns} FROM {quote_table(table_name)}"
    if where:
        sql += f" WHERE {where}"
    return sql
