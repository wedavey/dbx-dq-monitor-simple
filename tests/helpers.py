"""Shared test builders. The shipped metric catalog is used so tests track the real definitions."""

from __future__ import annotations

from datetime import datetime, timezone

from dq_framework.config_io import load_metrics
from dq_framework.models import Assignment

RUN_TS = datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc)
METRICS = {m.metric_id: m for m in load_metrics()}


def assignment(
    metric: str = "null_pct",
    column: str | None = "email",
    table: str = "cat.sch.customers",
    threshold: float = 0.02,
    severity: str = "HIGH",
    owner: str = "owner@example.com",
    **kwargs,
) -> Assignment:
    if metric == "row_count":
        column = None
    return Assignment(
        table_name=table,
        metric_id=metric,
        threshold=threshold,
        severity=severity,
        owner=owner,
        column_name=column,
        **kwargs,
    )
