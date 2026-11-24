import cv2
import numpy as np
import pytest

from core.quality import detect_foreign_objects


def test_detect_bright_rectangle():
    img = np.full((200, 200), 1000, dtype=np.int32)
    img[80:100, 90:110] = 4000

    res = detect_foreign_objects(img)
    objs = res["objects"]

    assert len(objs) == 1
    obj = objs[0]
    assert abs(obj["x"] - 90) <= 2
    assert abs(obj["y"] - 80) <= 2
    assert abs(obj["w"] - 20) <= 2
    assert abs(obj["h"] - 20) <= 2
    assert obj["area"] == pytest.approx(400, abs=20)
    assert obj["mean"] == pytest.approx(4000.0, abs=1.0)
    assert isinstance(obj["x"], int)
    assert isinstance(obj["mean"], float)
    assert res["steps"]["threshold"].dtype == np.uint8
    assert res["steps"]["cleaned"].dtype == np.uint8


def test_detect_long_bar():
    img = np.full((200, 200), 1000.0, dtype=np.float32)
    img[60:110, 95:101] = 4000.0

    res = detect_foreign_objects(img)
    objs = res["objects"]

    assert len(objs) == 1
    assert objs[0]["shape"] == "long"


def test_detect_round_circle():
    img = np.full((200, 200), 1000, dtype=np.int32)
    cv2.circle(img, (100, 100), 12, 4000, -1)

    res = detect_foreign_objects(img)
    objs = res["objects"]

    assert len(objs) == 1
    assert objs[0]["shape"] == "round"


def test_small_dot_removed_as_noise():
    rng = np.random.default_rng(42)
    img = rng.normal(1000.0, 10.0, size=(200, 200)).astype(np.float32)
    img[100, 100] = 4000.0

    res = detect_foreign_objects(img)
    assert res["objects"] == []


def test_uniform_empty_image():
    img = np.full((200, 200), 1000, dtype=np.int32)

    res = detect_foreign_objects(img)
    assert res["objects"] == []


@pytest.mark.parametrize(
    "bad_input",
    [
        np.zeros((10, 10, 3), dtype=np.int32),
        np.zeros((0, 0), dtype=np.int32),
        np.zeros((10,), dtype=np.int32),
    ],
)
def test_invalid_array_raises_value_error(bad_input):
    with pytest.raises(ValueError):
        detect_foreign_objects(bad_input)


@pytest.mark.parametrize("bad_pct", [-1.0, 100.1, float("nan"), float("inf"), True])
def test_invalid_percentile_raises_value_error(bad_pct):
    img = np.full((50, 50), 1000, dtype=np.int32)
    with pytest.raises(ValueError):
        detect_foreign_objects(img, percentile=bad_pct)


def test_input_array_not_mutated():
    img = np.full((200, 200), 1000, dtype=np.int32)
    img[80:100, 90:110] = 4000
    snapshot = img.copy()

    detect_foreign_objects(img)
    assert np.array_equal(img, snapshot)


def test_bg_sigma_detects_small_metal_on_bright_gradient_and_preserves_raw_mean():
    # 왼쪽(500) -> 오른쪽(3500)으로 밝아지는 강한 배경 기울기 위에
    # 배경보다 +1500 밝은 작은 금속을 상대적으로 어두운 왼쪽 영역에 배치
    grad = np.linspace(500.0, 3500.0, 200, dtype=np.float32)
    img = np.tile(grad, (200, 1))
    metal_slice = (slice(90, 106), slice(40, 56))  # 16x16 크기 (256px)
    img[metal_slice] += 1500.0
    expected_raw_mean = float(img[metal_slice].mean())
    snapshot = img.copy()

    res = detect_foreign_objects(img, percentile=99.0, bg_sigma=15.0)
    objs = res["objects"]

    assert len(objs) == 1
    obj = objs[0]
    assert abs(obj["x"] - 40) <= 2
    assert abs(obj["y"] - 90) <= 2
    assert abs(obj["w"] - 16) <= 2
    assert abs(obj["h"] - 16) <= 2
    # bg_sigma 배경 제거 후에도 mean은 배경 뺀 값이 아닌 원본 원시 픽셀 평균을 유지해야 함
    assert obj["mean"] == pytest.approx(expected_raw_mean, abs=50.0)
    assert obj["mean"] > 2000.0
    assert np.array_equal(img, snapshot)


@pytest.mark.parametrize(
    "bad_sigma",
    [0, 0.0, -1.5, float("nan"), float("inf"), float("-inf"), True, False, "10"],
)
def test_invalid_bg_sigma_raises_value_error(bad_sigma):
    img = np.full((50, 50), 1000, dtype=np.int32)
    with pytest.raises(ValueError):
        detect_foreign_objects(img, bg_sigma=bad_sigma)
