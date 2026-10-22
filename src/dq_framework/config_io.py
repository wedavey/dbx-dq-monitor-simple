"""Load and validate the YAML config (metric catalog, explicit assignments). No Spark here."""

from __future__ import annotations

import os
import re
from importlib import resources
from typing import Any, Mapping, Optional

import yaml

from .models import DIMENSIONS, DIRECTIONS, SCOPES, SEVERITIES, Assignment, Metric
from .rendering import template_placeholders, unquoted_placeholders

_METRIC_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_METRIC_KEYS = {
    "metric_id", "metric_name", "dimension", "scope", "direction",
    "sql_template", "description", "version", "is_active",
}  # fmt: skip
_METRIC_REQUIRED = {"metric_id", "metric_name", "dimension", "scope", "direction", "sql_template"}
_TABLE_KEYS = {"name", "owner", "severity", "data_product", "checks"}
_CHECK_KEYS = {"column", "metric", "threshold", "severity", "owner", "params", "enabled"}


class ConfigError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def read_config_text(ref: str) -> str:
    """``ref`` is a filesystem path (absolute, ./ or ../) or a file shipped inside the wheel."""
    if os.path.isabs(ref) or ref.startswith(("./", "../")):
        with open(ref, encoding="utf-8") as handle:
            return handle.read()
    node = resources.files("dq_framework").joinpath("config", *ref.split("/"))
    return node.read_text(encoding="utf-8")


def read_config_yaml(ref: str) -> Any:
    try:
        return yaml.safe_load(read_config_text(ref))
    except yaml.YAMLError as exc:
        raise ConfigError([f"{ref}: invalid YAML: {exc}"]) from exc


# --------------------------------------------------------------------------- metric catalog


def validate_metric(m: Metric) -> list[str]:
    p = f"metric '{m.metric_id}'"
    errors = []
    if not _METRIC_ID_RE.match(m.metric_id):
        errors.append(f"{p}: metric_id must be lower_snake_case")
    if m.scope not in SCOPES:
        errors.append(f"{p}: scope must be one of {SCOPES}")
    if m.direction not in DIRECTIONS:
        errors.append(f"{p}: direction must be one of {DIRECTIONS}")
    if m.dimension not in DIMENSIONS:
        errors.append(f"{p}: dimension must be one of {DIMENSIONS}")
    if not isinstance(m.version, int) or isinstance(m.version, bool) or m.version < 1:
        errors.append(f"{p}: version must be an integer >= 1")
    if not m.sql_template.strip():
        errors.append(f"{p}: sql_template is empty")
    if ";" in m.sql_template or "--" in m.sql_template:
        errors.append(f"{p}: sql_template may not contain ';' or '--'")
    names = template_placeholders(m.sql_template)
    if m.scope == "COLUMN" and "col" not in names:
        errors.append(f"{p}: column-scoped template must use {{col}}")
    if m.scope == "TABLE" and "col" in names:
        errors.append(f"{p}: table-scoped template cannot use {{col}}")
    if "filter" in names:
        errors.append(f"{p}: 'filter' is a reserved parameter name")
    for name in sorted(unquoted_placeholders(m.sql_template) - {"col"}):
        errors.append(f"{p}: parameter {{{name}}} must sit inside a single-quoted string literal")
    return errors


def parse_metrics(doc: Any) -> list[Metric]:
    if not isinstance(doc, Mapping) or not isinstance(doc.get("metrics"), list):
        raise ConfigError(["metric catalog must be a mapping with a 'metrics' list"])
    errors: list[str] = []
    metrics: list[Metric] = []
    for i, raw in enumerate(doc["metrics"], start=1):
        if not isinstance(raw, Mapping):
            errors.append(f"metrics[{i}]: expected a mapping")
            continue
        label = raw.get("metric_id", f"#{i}")
        for key in sorted(set(raw) - _METRIC_KEYS):
            errors.append(f"metric '{label}': unknown key '{key}'")
        for key in sorted(_METRIC_REQUIRED - set(raw)):
            errors.append(f"metric '{label}': missing '{key}'")
        if _METRIC_REQUIRED - set(raw):
            continue
        metric = Metric(
            metric_id=str(raw["metric_id"]),
            metric_name=str(raw["metric_name"]),
            dimension=str(raw["dimension"]),
            scope=str(raw["scope"]).upper(),
            direction=str(raw["direction"]).upper(),
            sql_template=str(raw["sql_template"]).strip(),
            description=str(raw.get("description", "")).strip(),
            version=raw.get("version", 1),
            is_active=bool(raw.get("is_active", True)),
        )
        errors.extend(validate_metric(metric))
        metrics.append(metric)
    ids = [m.metric_id for m in metrics]
    errors.extend(f"duplicate metric_id '{i}'" for i in sorted({i for i in ids if ids.count(i) > 1}))
    if errors:
        raise ConfigError(errors)
    return metrics


def load_metrics(ref: str = "metric_catalog.yml") -> list[Metric]:
    return parse_metrics(read_config_yaml(ref))


# --------------------------------------------------------------------------- assignments


def parse_assignments(doc: Any, catalog: Optional[str] = None) -> list[Assignment]:
    """Parse the table-grouped YAML format. ``${catalog}`` in table names is substituted."""
    if doc is None:
        return []
    if not isinstance(doc, Mapping) or not isinstance(doc.get("tables", []), list):
        raise ConfigError(["assignments file must be a mapping with a 'tables' list"])
    defaults = doc.get("defaults") or {}
    errors: list[str] = []
    out: list[Assignment] = []
    for t_index, table in enumerate(doc.get("tables", []), start=1):
        if not isinstance(table, Mapping) or "name" not in table:
            errors.append(f"tables[{t_index}]: expected a mapping with 'name'")
            continue
        name = str(table["name"]).strip()
        if catalog:
            name = name.replace("${catalog}", catalog)
        for key in sorted(set(table) - _TABLE_KEYS):
            errors.append(f"table '{name}': unknown key '{key}'")
        for c_index, check in enumerate(table.get("checks") or [], start=1):
            where = f"table '{name}' check #{c_index}"
            if not isinstance(check, Mapping):
                errors.append(f"{where}: expected a mapping")
                continue
            for key in sorted(set(check) - _CHECK_KEYS):
                errors.append(f"{where}: unknown key '{key}'")
            if "metric" not in check or "threshold" not in check:
                errors.append(f"{where}: 'metric' and 'threshold' are required")
                continue
            owner = check.get("owner") or table.get("owner") or defaults.get("owner")
            if not owner:
                errors.append(f"{where}: no owner (set on the check, the table or defaults)")
                continue
            severity = str(
                check.get("severity") or table.get("severity") or defaults.get("severity") or "MEDIUM"
            ).upper()
            params = check.get("params") or {}
            if not isinstance(params, Mapping):
                errors.append(f"{where}: params must be a mapping")
                continue
            try:
                threshold = float(check["threshold"])
            except (TypeError, ValueError):
                errors.append(f"{where}: threshold must be a number")
                continue
            column = check.get("column")
            out.append(
                Assignment(
                    table_name=name,
                    column_name=str(column).strip() if column else None,
                    metric_id=str(check["metric"]).strip(),
                    threshold=threshold,
                    severity=severity,
                    owner=str(owner),
                    params={str(k): str(v) for k, v in params.items()},
                    data_product=table.get("data_product") or defaults.get("data_product"),
                    is_enabled=bool(check.get("enabled", True)),
                )
            )
    if errors:
        raise ConfigError(errors)
    return out


def load_assignments(ref: str, catalog: Optional[str] = None) -> list[Assignment]:
    try:
        return parse_assignments(read_config_yaml(ref), catalog)
    except ConfigError as exc:
        raise ConfigError([f"{ref}: {e}" for e in exc.errors]) from exc


__all__ = [
    "ConfigError", "load_assignments", "load_metrics", "parse_assignments", "parse_metrics",
    "read_config_text", "read_config_yaml", "validate_metric", "SEVERITIES",
]  # fmt: skip
