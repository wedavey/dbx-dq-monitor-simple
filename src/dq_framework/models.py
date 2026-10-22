"""Plain data types shared across the framework. No Spark imports in this module."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
DIRECTIONS = ("MAX", "MIN")  # MAX: fail if value > threshold. MIN: fail if value < threshold.
SCOPES = ("COLUMN", "TABLE")
DIMENSIONS = ("completeness", "uniqueness", "validity", "freshness", "volume", "consistency")

PASS, FAIL, ERROR = "PASS", "FAIL", "ERROR"


@dataclass(frozen=True)
class Metric:
    """A governance-owned metric definition (one row of metric_catalog)."""

    metric_id: str
    metric_name: str
    dimension: str
    scope: str
    sql_template: str
    direction: str
    description: str = ""
    version: int = 1
    is_active: bool = True


@dataclass(frozen=True)
class Assignment:
    """Apply one metric to one table (and column) with a threshold, severity and owner."""

    table_name: str
    metric_id: str
    threshold: float
    severity: str
    owner: str
    column_name: Optional[str] = None
    params: dict = field(default_factory=dict)
    data_product: Optional[str] = None
    is_enabled: bool = True

    @property
    def assignment_id(self) -> str:
        # Lower-cased so case variants collide (UC names are case-insensitive).
        column = (self.column_name or "").lower()
        return f"{self.table_name.lower()}|{column}|{self.metric_id.lower()}"


@dataclass(frozen=True)
class Issue:
    """A problem found while building assignments, shown back to whoever owns the YAML."""

    severity: str  # ERROR (assignment dropped)
    message: str
    table_name: Optional[str] = None
    column_name: Optional[str] = None


@dataclass(frozen=True)
class CheckResult:
    """One row of the results table. Threshold/severity/owner are snapshotted at run time."""

    run_id: str
    run_ts: datetime
    assignment_id: str
    table_name: str
    column_name: Optional[str]
    metric_id: str
    metric_version: Optional[int]
    dimension: Optional[str]
    metric_value: Optional[float]
    threshold: float
    direction: Optional[str]
    status: str
    severity: str
    owner: str
    data_product: Optional[str]
    error_message: Optional[str]
