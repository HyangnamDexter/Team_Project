import numpy as np
import pytest
from core.algorithms import apply_clahe


def test_clahe_output_shape_and_dtype():
    dummy_image = np.zeros((100, 100), dtype=np.uint8)
    result = apply_clahe(dummy_image)

    assert result.shape == (100, 100)
    assert result.dtype == np.uint8


def test_clahe_does_not_modify_original():
    original = np.random.randint(50, 150, size=(64, 64), dtype=np.uint8)
    backup = original.copy()

    result = apply_clahe(original, clip_limit=2.0, tile_size=8)

    assert np.array_equal(original, backup)
    assert result is not original


def test_clahe_invalid_params():
    dummy = np.zeros((50, 50), dtype=np.uint8)

    with pytest.raises(ValueError):
        apply_clahe(dummy, clip_limit=0)

    with pytest.raises(ValueError):
        apply_clahe(dummy, tile_size=0)

    with pytest.raises(ValueError):
        apply_clahe(np.zeros((50, 50, 3), dtype=np.uint8))

    with pytest.raises(ValueError):
        apply_clahe(np.zeros((50, 50), dtype=np.float32))