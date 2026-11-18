import numpy as np
import pytest
from core.quality import QC_THRESHOLDS, image_quality, sample_slices, thresholds_for


def test_thresholds_for_modalities():
    assert thresholds_for("DX") == QC_THRESHOLDS["DX"]
    assert thresholds_for("CR") == QC_THRESHOLDS["DX"]
    assert thresholds_for("CT") == QC_THRESHOLDS["CT"]
    assert thresholds_for("MR") == QC_THRESHOLDS["MR"]
    with pytest.raises(ValueError):
        thresholds_for("US")


@pytest.mark.parametrize("num_slices,k", [(1, 5), (3, 5), (5, 5), (10, 5), (11, 5), (20, 4), (15, 1)])
def test_sample_slices_includes_middle_and_bounds(num_slices, k):
    res = sample_slices(num_slices, k)
    assert len(res) == min(num_slices, k)
    assert (num_slices // 2) in res
    assert res == sorted(set(res))
    assert all(0 <= idx < num_slices for idx in res)


@pytest.mark.parametrize("bad_n,bad_k", [(0, 5), (-3, 5), (10, 0), (10, -1), (10.5, 5), (True, 5)])
def test_sample_slices_invalid(bad_n, bad_k):
    with pytest.raises(ValueError):
        sample_slices(bad_n, bad_k)


def test_image_quality_normal_and_issues():
    # 정상 대비·선명도를 가진 합성 영상
    rng = np.random.default_rng(7)
    normal = np.tile(np.linspace(30, 210, 128, dtype=np.uint8), (128, 1))
    normal[::2, ::2] = np.clip(normal[::2, ::2].astype(np.int16) + 35, 0, 255).astype(np.uint8)
    res_ok = image_quality(normal, modality="DX")
    assert res_ok["issues"] == []

    # 어둡고 대비/선명도가 낮은 영상
    dark_flat = np.full((64, 64), 10, dtype=np.uint8)
    res_dark = image_quality(dark_flat, modality="CR")
    assert "너무 어두움" in res_dark["issues"]
    assert "대비 부족" in res_dark["issues"]
    assert "선명도 부족" in res_dark["issues"]

    # 너무 밝은 영상
    bright_flat = np.full((64, 64), 245, dtype=np.uint8)
    res_bright = image_quality(bright_flat, modality="CT")
    assert "너무 밝음" in res_bright["issues"]