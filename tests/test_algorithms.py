import numpy as np
import pytest

from core.algorithms import (
    ALGORITHMS,
    apply_clahe,
    apply_denoise,
    apply_edge,
    roi_stats,
)


@pytest.fixture
def sample_img() -> np.ndarray:
    img = np.full((64, 64), 40, dtype=np.uint8)
    img[16:48, 16:48] = 200
    return img


@pytest.fixture
def dark_low_contrast_img() -> np.ndarray:
    rng = np.random.default_rng(42)
    return rng.normal(loc=40, scale=8, size=(64, 64)).clip(0, 255).astype(np.uint8)


def test_algorithms_registry():
    assert set(ALGORITHMS.keys()) == {"CLAHE", "Denoise", "Edge"}


# --- 1. apply_clahe ---
@pytest.mark.parametrize("tile_size", [4, 8, np.int64(8)])
def test_apply_clahe_valid_and_contrast_increase(dark_low_contrast_img, tile_size):
    before = dark_low_contrast_img.copy()
    out = apply_clahe(dark_low_contrast_img, clip_limit=2.0, tile_size=tile_size)

    assert isinstance(out, np.ndarray)
    assert out.shape == dark_low_contrast_img.shape
    assert out.dtype == np.uint8
    assert out is not dark_low_contrast_img
    assert np.array_equal(dark_low_contrast_img, before)
    assert out.std() > dark_low_contrast_img.std()


@pytest.mark.parametrize(
    "bad_clip",
    [0, -1.5, np.nan, np.inf, -np.inf, None, "2.0", True, False],
)
def test_apply_clahe_invalid_clip_limit(sample_img, bad_clip):
    with pytest.raises(ValueError):
        apply_clahe(sample_img, clip_limit=bad_clip, tile_size=8)


@pytest.mark.parametrize(
    "bad_tile",
    [0, -4, 4.5, np.nan, None, "8", True, False],
)
def test_apply_clahe_invalid_tile_size(sample_img, bad_tile):
    with pytest.raises(ValueError):
        apply_clahe(sample_img, clip_limit=2.0, tile_size=bad_tile)


# --- 2. apply_denoise ---
@pytest.mark.parametrize("method", ["gaussian", "median", "bilateral"])
@pytest.mark.parametrize("ksize", [3, np.int64(5)])
def test_apply_denoise_valid_and_reduces_noise(sample_img, method, ksize):
    rng = np.random.default_rng(42)
    noisy = np.clip(
        sample_img.astype(np.int16) + rng.integers(-25, 26, sample_img.shape), 0, 255
    ).astype(np.uint8)
    before = noisy.copy()

    out = apply_denoise(noisy, method=method, ksize=ksize)

    assert out.shape == noisy.shape
    assert out.dtype == np.uint8
    assert out is not noisy
    assert np.array_equal(noisy, before)

    mse_before = np.mean((noisy.astype(np.float32) - sample_img.astype(np.float32)) ** 2)
    mse_after = np.mean((out.astype(np.float32) - sample_img.astype(np.float32)) ** 2)
    assert mse_after < mse_before


@pytest.mark.parametrize("bad_method", ["mean", "GAUSSIAN", "", None, 123])
def test_apply_denoise_invalid_method(sample_img, bad_method):
    with pytest.raises(ValueError):
        apply_denoise(sample_img, method=bad_method, ksize=5)


@pytest.mark.parametrize(
    "bad_ksize",
    [0, -3, 1, 2, 4, 3.5, np.nan, np.inf, None, "5", True, False],
)
def test_apply_denoise_invalid_ksize(sample_img, bad_ksize):
    with pytest.raises(ValueError):
        apply_denoise(sample_img, method="gaussian", ksize=bad_ksize)


# --- 3. apply_edge ---
@pytest.mark.parametrize("method", ["canny", "sobel", "otsu"])
def test_apply_edge_valid(sample_img, method):
    before = sample_img.copy()
    out = apply_edge(sample_img, method=method, low=50, high=150)

    assert out.shape == sample_img.shape
    assert out.dtype == np.uint8
    assert out is not sample_img
    assert np.array_equal(sample_img, before)
    assert out.max() > 0


@pytest.mark.parametrize("bad_method", ["laplacian", "CANNY", "", None, 1])
def test_apply_edge_invalid_method(sample_img, bad_method):
    with pytest.raises(ValueError):
        apply_edge(sample_img, method=bad_method)


@pytest.mark.parametrize(
    "low,high",
    [(-1, 100), (150, 50), (100, 100), (np.nan, 100), (50, np.inf), (None, 100), (True, 100)],
)
def test_apply_edge_invalid_thresholds(sample_img, low, high):
    with pytest.raises(ValueError):
        apply_edge(sample_img, method="canny", low=low, high=high)


# --- 4. roi_stats ---
def test_roi_stats_valid(sample_img):
    stats = roi_stats(sample_img, x=16, y=16, w=32, h=32)

    assert stats["x"] == 16
    assert stats["y"] == 16
    assert stats["w"] == 32
    assert stats["h"] == 32
    assert stats["mean"] == pytest.approx(200.0)
    assert stats["std"] == pytest.approx(0.0)
    assert stats["min"] == 200
    assert stats["max"] == 200
    assert len(stats["hist"]) == 256
    assert sum(stats["hist"]) == 32 * 32
    assert stats["hist"][200] == 1024


@pytest.mark.parametrize(
    "x,y,w,h",
    [
        (-1, 0, 10, 10),
        (0, -1, 10, 10),
        (0, 0, 0, 10),
        (0, 0, 10, 0),
        (0, 0, -5, 10),
        (50, 0, 20, 10),
        (0, 50, 10, 20),
        (1.5, 0, 10, 10),
        (True, 0, 10, 10),
    ],
)
def test_roi_stats_invalid(sample_img, x, y, w, h):
    with pytest.raises(ValueError):
        roi_stats(sample_img, x=x, y=y, w=w, h=h)


# --- 5. 공통 잘못된 입력 영상 검증 ---
@pytest.mark.parametrize(
    "bad_img",
    [
        None,
        [[1, 2], [3, 4]],
        np.zeros((10, 10, 3), dtype=np.uint8),
        np.zeros((10, 10), dtype=np.float32),
        np.zeros((10, 10), dtype=np.uint16),
        np.empty((0, 0), dtype=np.uint8),
    ],
)
def test_all_algorithms_reject_invalid_image(bad_img):
    with pytest.raises(ValueError):
        apply_clahe(bad_img)
    with pytest.raises(ValueError):
        apply_denoise(bad_img)
    with pytest.raises(ValueError):
        apply_edge(bad_img)
    with pytest.raises(ValueError):
        roi_stats(bad_img, 0, 0, 5, 5)