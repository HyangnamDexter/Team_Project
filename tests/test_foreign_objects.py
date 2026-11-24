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
