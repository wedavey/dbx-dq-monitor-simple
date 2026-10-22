import pytest

from dq_framework.evaluation import evaluate


@pytest.mark.parametrize(
    "value, threshold, direction, expected",
    [
        (0.01, 0.02, "MAX", "PASS"),
        (0.02, 0.02, "MAX", "PASS"),  # equal to the threshold passes
        (0.0201, 0.02, "MAX", "FAIL"),
        (0.0, 0.0, "MAX", "PASS"),
        (1e-12, 0.0, "MAX", "PASS"),  # float noise is tolerated
        (1000, 500, "MIN", "PASS"),
        (500, 500, "MIN", "PASS"),
        (499, 500, "MIN", "FAIL"),
        (None, 0.0, "MAX", "ERROR"),
        (float("nan"), 0.0, "MIN", "ERROR"),
    ],
)
def test_evaluate(value, threshold, direction, expected):
    assert evaluate(value, threshold, direction) == expected


def test_unknown_direction_raises():
    with pytest.raises(ValueError):
        evaluate(1.0, 1.0, "SIDEWAYS")
