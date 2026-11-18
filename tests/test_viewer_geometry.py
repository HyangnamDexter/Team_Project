import math

import pytest

from core.viewer_geometry import measure_distance, viewport


def test_measurement_uses_row_and_column_spacing():
    distance, unit = measure_distance([[0, 0], [3, 4]], [2, 0.5])
    assert unit == "mm"
    assert distance == pytest.approx(math.hypot(1.5, 8))


@pytest.mark.parametrize("spacing", [None, [], [1], [1, 2, 3], [0, 1],
                                    [-1, 1], [float("nan"), 1], [1, float("inf")]])
def test_missing_or_invalid_spacing_uses_pixels(spacing):
    assert measure_distance([[0, 0], [3, 4]], spacing) == (5.0, "px")


def test_viewport_edges_and_full_image():
    assert viewport((100, 200), 1, 100, 0) == (0, 0, 200, 100)
    assert viewport((100, 200), 2, 100, 100) == (100, 50, 100, 50)
    assert viewport((1, 1), 4, 100, 100) == (0, 0, 1, 1)
