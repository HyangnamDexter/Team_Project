import numpy as np
import pytest

from core.algorithms import apply_clahe


def test_clahe_output_shape_and_dtype():
    dummy_image = np.zeros((100, 100), dtype=np.uint8)
    result = apply_clahe(dummy_image)

    assert result.shape == (100, 100)
    assert result.dtype == np.uint8


def test_clahe_does_not_modify_original():
    rng = np.random.default_rng(42)
    original = rng.integers(50, 150, size=(64, 64), dtype=np.uint8)
    backup = original.copy()

    result = apply_clahe(original, clip_limit=2.0, tile_size=np.int64(8))

    assert np.array_equal(original, backup)
    assert result is not original


def test_clahe_increases_contrast_on_dark_image():
    rng = np.random.default_rng(42)
    dark_img = np.clip(rng.normal(loc=40, scale=8, size=(128, 128)), 0, 255).astype(np.uint8)

    result = apply_clahe(dark_img)

    assert result.std() > dark_img.std()


def test_clahe_invalid_params():
    dummy = np.zeros((50, 50), dtype=np.uint8)

    for invalid_clip in [0, -1.5, float("nan"), float("inf"), None, "2", True]:
        with pytest.raises(ValueError):
            apply_clahe(dummy, clip_limit=invalid_clip)

    for invalid_tile in [0, -4, 3.5, None, "8", True]:
        with pytest.raises(ValueError):
            apply_clahe(dummy, tile_size=invalid_tile)

    with pytest.raises(ValueError):
        apply_clahe(np.zeros((50, 50, 3), dtype=np.uint8))

    with pytest.raises(ValueError):
        apply_clahe(np.zeros((50, 50), dtype=np.float32))