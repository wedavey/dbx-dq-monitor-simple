"""Load the packaged SQL files and split them into statements."""

from __future__ import annotations

from importlib import resources

from .naming import check_identifier


def split_statements(text: str) -> list[str]:
    """Drop whole-line ``--`` comments, then split on ';'. Files must not use ';' inside strings."""
    lines = [line for line in text.splitlines() if not line.strip().startswith("--")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


def load_sql(name: str, catalog: str, schema: str) -> list[str]:
    check_identifier(catalog, "catalog")
    check_identifier(schema, "schema")
    text = resources.files("dq_framework").joinpath("sql", name).read_text(encoding="utf-8")
    text = text.replace("${catalog}", catalog).replace("${schema}", schema)
    return split_statements(text)
