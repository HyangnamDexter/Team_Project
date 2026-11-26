"""core/detector.py 테스트. 실제 학습 모델 없이 가짜 모델 출력으로 전처리·좌표 계산·NMS 를 확인한다."""
from types import SimpleNamespace

import numpy as np
import pytest

from core.detector import Detector, DetectorError, postprocess, preprocess

# 원본 세로 2000 × 가로 1000 → 학습 크기(긴 변 1024)로 축소하면 1024 × 512
# → 640 letterbox 배율 0.625 → 안쪽 640 × 320, 좌우에 160 씩 회색 여백
H, W = 2000, 1000
LEFT = 160
SCALE = 0.512 * 0.625  # 원본 → 모델 입력 배율


def to_model_xywh(xmin, ymin, xmax, ymax):
    """원본 픽셀 박스 → 모델이 내놓는 (cx, cy, w, h) 640 칸 좌표"""
    x0, y0, x1, y1 = xmin * SCALE + LEFT, ymin * SCALE, xmax * SCALE + LEFT, ymax * SCALE
    return [(x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0]


def model_output(*candidates):
    """candidates: (원본 박스, 점수) → ultralytics 형식 출력 (1, 5, 후보 수)"""
    cols = [to_model_xywh(*box) + [score] for box, score in candidates]
    return np.array(cols, dtype=np.float32).T[None]


class FakeSession:
    def __init__(self, output, shape=(1, 3, 640, 640)):
        self.output, self.shape, self.seen = output, shape, None

    def get_inputs(self):
        return [SimpleNamespace(name="images", shape=list(self.shape))]

    def run(self, names, feeds):
        self.seen = feeds["images"]
        return [self.output]


def pixels():
    rng = np.random.default_rng(0)
    return rng.integers(0, 4000, (H, W)).astype(np.float32)


# ---------------------------------------------------------------- 전처리
def test_preprocess_letterbox_matches_training_pipeline():
    tensor, meta = preprocess(pixels())
    assert tensor.shape == (1, 3, 640, 640) and tensor.dtype == np.float32
    assert meta["left"] == LEFT and meta["top"] == 0
    assert np.allclose(tensor[0, :, :, :LEFT], 114 / 255)       # 왼쪽 회색 여백
    assert np.allclose(tensor[0, :, :, 640 - LEFT:], 114 / 255)  # 오른쪽 회색 여백
    assert np.array_equal(tensor[0, 0], tensor[0, 2])            # 흑백을 3채널로 복사
    assert 0.0 <= tensor.min() and tensor.max() <= 1.0


def test_preprocess_small_image_not_shrunk_before_letterbox():
    tensor, meta = preprocess(np.random.default_rng(1).integers(0, 4000, (300, 600)).astype(np.float32))
    assert tensor.shape == (1, 3, 640, 640)
    assert meta["top"] == 160 and meta["left"] == 0  # 600 → 640 (확대), 세로 320 이라 위아래 160


# ---------------------------------------------------------------- 후처리
def test_boxes_mapped_back_to_original_pixels():
    implant = (200, 400, 600, 1000)
    det = Detector(session=FakeSession(model_output((implant, 0.9))))
    result = det.detect(pixels())
    assert len(result["boxes"]) == 1
    b = result["boxes"][0]
    assert b["class_name"] == "implant" and b["score"] == pytest.approx(0.9)
    assert [b["xmin"], b["ymin"], b["xmax"], b["ymax"]] == pytest.approx(list(implant), abs=1.0)
    assert result["conf"] == 0.40 and result["elapsed_ms"] >= 0  # C 의 val 기준 확정값


def test_low_score_removed_and_overlaps_merged_by_nms():
    out = model_output(((200, 400, 600, 1000), 0.9),
                       ((210, 410, 610, 1010), 0.8),   # 거의 같은 위치 → NMS 로 제거
                       ((100, 1500, 300, 1800), 0.5),  # 다른 위치 → 남음
                       ((700, 100, 900, 300), 0.1))    # 점수 낮음 → 제거
    boxes = postprocess(out, preprocess(pixels())[1])
    assert [b["score"] for b in boxes] == pytest.approx([0.9, 0.5])


def test_no_detection():
    out = model_output(((200, 400, 600, 1000), 0.05))
    assert postprocess(out, preprocess(pixels())[1]) == []


def test_transposed_output_layout_accepted():
    out = model_output(((200, 400, 600, 1000), 0.9))  # (1, 5, N)
    meta = preprocess(pixels())[1]
    assert postprocess(out.transpose(0, 2, 1), meta) == postprocess(out, meta)  # (1, N, 5) 도 같은 결과


def test_boxes_clipped_to_image():
    out = model_output(((-50, -50, 300, 300), 0.9))
    b = postprocess(out, preprocess(pixels())[1])[0]
    assert b["xmin"] == 0 and b["ymin"] == 0


def test_wrong_output_shape():
    with pytest.raises(DetectorError, match="출력 형식"):
        postprocess(np.zeros((1, 7, 10), dtype=np.float32), preprocess(pixels())[1])


def test_conf_option():
    out = model_output(((200, 400, 600, 1000), 0.5))
    assert Detector(session=FakeSession(out), conf=0.6).detect(pixels())["boxes"] == []


def test_dynamic_input_shape_uses_640():
    det = Detector(session=FakeSession(model_output(), shape=("batch", 3, "height", "width")))
    assert det.input_size == (640, 640)


# ---------------------------------------------------------------- 모델 파일
def test_missing_model_file(tmp_path):
    with pytest.raises(DetectorError, match="모델 파일이 없습니다"):
        Detector(tmp_path / "nope.onnx")


def test_broken_model_file(tmp_path):
    bad = tmp_path / "bad.onnx"
    bad.write_bytes(b"not an onnx model")
    with pytest.raises(DetectorError, match="열 수 없습니다"):
        Detector(bad)


def test_real_onnxruntime_end_to_end(tmp_path):
    """작은 ONNX 모델을 직접 만들어 실제 onnxruntime 으로 실행해 본다 (onnx 패키지가 있을 때만)."""
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper, numpy_helper

    pytest.importorskip("onnxruntime")
    fixed = model_output(((200, 400, 600, 1000), 0.9))  # 입력과 상관없이 이 값을 내놓는 모델
    graph = helper.make_graph(
        [helper.make_node("ReduceMax", ["images"], ["m"], keepdims=0),           # 입력을 한 번 쓰고
         helper.make_node("Mul", ["m", "zero"], ["z"]),                          # 0 을 곱해
         helper.make_node("Add", ["fixed", "z"], ["output0"])],                  # 고정 출력에 더함
        "fake_yolo",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 640, 640])],
        [helper.make_tensor_value_info("output0", TensorProto.FLOAT, list(fixed.shape))],
        initializer=[numpy_helper.from_array(fixed, "fixed"),
                     numpy_helper.from_array(np.array(0, dtype=np.float32), "zero")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    path = tmp_path / "fake.onnx"
    onnx.save(model, path)

    det = Detector(path)
    assert det.input_name == "images" and det.input_size == (640, 640) and det.model_name == "fake.onnx"
    b = det.detect(pixels())["boxes"][0]
    assert [b["xmin"], b["ymin"], b["xmax"], b["ymax"]] == pytest.approx([200, 400, 600, 1000], abs=1.0)
