"""Decide PASS / FAIL / ERROR for a computed metric value."""

from __future__ import annotations

import math
from typing import Optional

from .models import ERROR, FAIL, PASS

EPSILON = 1e-9


def evaluate(value: Optional[float], threshold: float, direction: str) -> str:
    """MAX fails when value > threshold; MIN fails when value < threshold.

    A NULL / NaN value is ERROR: the metric is undefined (empty table, all-null column), which is
    surfaced to the platform team rather than silently passing.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ERROR
    if direction == "MAX":
        return FAIL if value > threshold + EPSILON else PASS
    if direction == "MIN":
        return FAIL if value < threshold - EPSILON else PASS
    raise ValueError(f"unknown direction {direction!r}")
