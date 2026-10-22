"""The check runner: plan -> one scan per table -> evaluate -> standard results.

Spark is injected as ``run_query(sql) -> dict`` so every behaviour here (batching, error isolation,
parallelism) is unit-testable without a cluster.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Optional

from .evaluation import evaluate
from .models import ERROR, Assignment, CheckResult, Metric
from .rendering import RenderError, build_select, render_expression, render_filter

RunQuery = Callable[[str], Mapping[str, Any]]


class CheckError(Exception):
    """A configuration problem with one assignment (unknown metric etc.)."""


@dataclass
class _Check:
    index: int
    assignment: Assignment
    metric: Metric
    expression: str
    alias: str


def _short_error(exc: Exception) -> str:
    lines = [line for line in str(exc).strip().splitlines() if line.strip()]
    text = lines[0] if lines else type(exc).__name__
    return text[:300]


def _to_float(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def _plan(index: int, a: Assignment, metrics: Mapping[str, Metric]) -> _Check:
    metric = metrics.get(a.metric_id)
    if metric is None:
        raise CheckError(f"unknown metric '{a.metric_id}'")
    if not metric.is_active:
        raise CheckError(f"metric '{a.metric_id}' is inactive")
    return _Check(index, a, metric, render_expression(metric, a), f"m{index}")


def _run_batch(
    key: tuple[str, Optional[str]], checks: list[_Check], run_query: RunQuery
) -> list[tuple[_Check, Optional[float], Optional[str]]]:
    """Run all checks for one (table, filter) in a single scan.

    If the combined query fails, retry each check alone so one broken check (dropped column,
    bad regex) cannot hide the results of the others.
    """
    table, where = key
    try:
        row = run_query(build_select(table, [(c.alias, c.expression) for c in checks], where))
        return [(c, _to_float(row.get(c.alias)), None) for c in checks]
    except Exception as exc:  # noqa: BLE001 - any Spark/analysis error must become an ERROR row
        if len(checks) == 1:
            return [(checks[0], None, _short_error(exc))]
    outcomes = []
    for c in checks:
        try:
            row = run_query(build_select(table, [(c.alias, c.expression)], where))
            outcomes.append((c, _to_float(row.get(c.alias)), None))
        except Exception as exc:  # noqa: BLE001
            outcomes.append((c, None, _short_error(exc)))
    return outcomes


def run_checks(
    assignments: Iterable[Assignment],
    metrics: Iterable[Metric],
    run_query: RunQuery,
    run_id: str,
    run_ts: datetime,
    max_workers: int = 1,
) -> list[CheckResult]:
    assignments = list(assignments)
    metric_map = {m.metric_id: m for m in metrics}

    def result(a: Assignment, metric: Optional[Metric], value, status: str, error) -> CheckResult:
        return CheckResult(
            run_id=run_id,
            run_ts=run_ts,
            assignment_id=a.assignment_id,
            table_name=a.table_name,
            column_name=a.column_name,
            metric_id=a.metric_id,
            metric_version=metric.version if metric else None,
            dimension=metric.dimension if metric else None,
            metric_value=value,
            threshold=a.threshold,
            direction=metric.direction if metric else None,
            status=status,
            severity=a.severity,
            owner=a.owner,
            data_product=a.data_product,
            error_message=error,
        )

    slots: list[Optional[CheckResult]] = [None] * len(assignments)
    batches: dict[tuple[str, Optional[str]], list[_Check]] = {}

    for i, a in enumerate(assignments):
        if not a.is_enabled:
            continue
        try:
            check = _plan(i, a, metric_map)
            key = (a.table_name, render_filter(a))
        except (CheckError, RenderError) as exc:
            slots[i] = result(a, metric_map.get(a.metric_id), None, ERROR, str(exc))
            continue
        batches.setdefault(key, []).append(check)

    items = list(batches.items())
    if max_workers > 1 and len(items) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            outcomes = list(pool.map(lambda kv: _run_batch(kv[0], kv[1], run_query), items))
    else:
        outcomes = [_run_batch(key, checks, run_query) for key, checks in items]

    for batch_outcomes in outcomes:
        for check, value, error in batch_outcomes:
            a, metric = check.assignment, check.metric
            if error is not None:
                slots[check.index] = result(a, metric, None, ERROR, error)
            elif value is None:
                msg = "metric returned NULL (empty table, or no non-null values)"
                slots[check.index] = result(a, metric, None, ERROR, msg)
            else:
                status = evaluate(value, a.threshold, metric.direction)
                slots[check.index] = result(a, metric, value, status, None)

    return [r for r in slots if r is not None]
