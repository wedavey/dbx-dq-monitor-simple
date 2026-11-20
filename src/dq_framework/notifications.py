"""Group failed checks by owner and render one message per owner. No Spark or network imports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from .models import SEVERITIES

_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


@dataclass(frozen=True)
class Failure:
    """One failed assignment from a run (a row of ``results`` with status FAIL)."""

    table_name: str
    column_name: Optional[str]
    metric_id: str
    metric_value: Optional[float]
    threshold: Optional[float]
    direction: Optional[str]
    severity: str
    owner: Optional[str]
    data_product: Optional[str] = None


@dataclass(frozen=True)
class Message:
    owner: str
    subject: str
    body: str
    failures: int


def is_deliverable(owner: Optional[str]) -> bool:
    """True when the owner is a single email address (or group address) we can send to."""
    return bool(owner) and _EMAIL.match(owner.strip()) is not None


def _rank(severity: str) -> int:
    return -SEVERITIES.index(severity) if severity in SEVERITIES else 1  # CRITICAL first


def group_by_owner(failures: Iterable[Failure]) -> dict[str, list[Failure]]:
    """{owner: failures}. Owners are matched case-insensitively; blank owners group under ''."""
    grouped: dict[str, list[Failure]] = {}
    for f in failures:
        grouped.setdefault((f.owner or "").strip().lower(), []).append(f)
    for items in grouped.values():
        items.sort(key=lambda f: (_rank(f.severity), f.table_name, f.column_name or "", f.metric_id))
    return grouped


def _fmt(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.4g}"


def _line(f: Failure) -> str:
    target = f"{f.table_name}.{f.column_name}" if f.column_name else f.table_name
    op = {"MAX": ">", "MIN": "<"}.get(f.direction or "", "vs")
    return f"[{f.severity}] {target} | {f.metric_id} = {_fmt(f.metric_value)} ({op} limit {_fmt(f.threshold)})"


def build_message(owner: str, failures: list[Failure], run_ts: datetime, environment: str) -> Message:
    n = len(failures)
    subject = f"[DQ {environment}] {n} high severity check{'s' if n != 1 else ''} failed - {run_ts:%Y-%m-%d}"
    body = "\n".join(
        [
            f"The daily data quality run at {run_ts:%Y-%m-%d %H:%M} UTC found {n} failed "
            f"high severity check{'s' if n != 1 else ''} assigned to {owner}.",
            "",
            *(_line(f) for f in failures),
            "",
            "Thresholds, severity and owner are set in the assignment YAML (see docs/assignments.md).",
        ]
    )
    return Message(owner, subject, body, n)


def build_messages(
    failures: Iterable[Failure], run_ts: datetime, environment: str
) -> tuple[list[Message], dict[str, int]]:
    """(messages for deliverable owners, {undeliverable owner: failure count})."""
    messages: list[Message] = []
    skipped: dict[str, int] = {}
    for owner, items in sorted(group_by_owner(failures).items()):
        if is_deliverable(owner):
            messages.append(build_message(owner, items, run_ts, environment))
        else:
            skipped[owner or "(blank)"] = len(items)
    return messages, skipped
