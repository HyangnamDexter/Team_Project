"""임플란트 탐지 모델 추론 (YOLO → ONNX). 담당: A(추론·연결), C(학습)

학습(C): Colab 에서 ultralytics YOLO 학습 → model.export(format="onnx", imgsz=640) 로 best.onnx 를 만든다
서버(A): PyTorch 없이 onnxruntime 으로 실행한다 (설치 +72MB. PyTorch 는 +1.3GB 라 JCloud micro 에 부담)
모델 파일은 VinDr 로 학습한 결과라 공개 저장소에 올리지 않는다 (models/ 와 *.onnx 는 .gitignore)

전처리는 학습 데이터를 만들 때(scripts/prepare_spinexr.dicom_to_png)와 같아야 결과가 맞다
1. apply_window(pixels): 자동 윈도우. 헤더 WL 이 아니다 (학습 PNG 가 자동 윈도우로 만들어졌기 때문)
   ※ 품질 점검(services/cases.run_checks)은 판독의가 보는 화면 기준이라 헤더 WL 을 쓴다. 목적이 달라서 다르다
2. 긴 변을 1024 로 축소 (INTER_AREA, 학습 PNG 와 같음)
3. ultralytics 와 같은 letterbox: 비율을 지켜 640 칸에 맞추고 남는 곳은 회색(114)으로 채움
   흑백을 3채널로 복사, 0~1 로 나눔, (1, 3, 640, 640) float32

후처리
- 모델 출력 (1, 4 + 클래스 수, 후보 수): 후보마다 cx, cy, w, h (640 칸 기준) + 클래스 점수
- 점수 conf 이상만 남기고 NMS(IoU 0.45, 전달받은 ONNX 명세)로 겹친 박스 정리
- letterbox·축소를 되돌려 원본 영상 픽셀 좌표로 바꾼다 (ground_truth 박스와 같은 기준)

사용 예:
    from core.detector import Detector
    detector = Detector("models/implant.onnx")      # 한 번만 만들어 재사용 (화면에서는 st.cache_resource)
    result = detector.detect(pixels)                 # pixels: load_dicom 결과 (원본 픽셀 2D)
    for b in result["boxes"]:                        # 원본 픽셀 좌표, 점수 높은 순
        print(b["xmin"], b["ymin"], b["xmax"], b["ymax"], b["score"])
"""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

from core.dicom_io import apply_window

DEFAULT_CONF = 0.40       # C의 val 기준 선정값
DEFAULT_IOU = 0.45        # 전달받은 ONNX 후처리 명세
TRAIN_MAX_SIZE = 1024     # 학습 PNG 의 긴 변 (prepare_spinexr convert --max-size 기본값)
DEFAULT_IMGSZ = 640       # 모델 입력 크기를 모델에서 읽지 못할 때만 쓴다
PAD_VALUE = 114           # ultralytics letterbox 의 회색 채움 값
MAX_DET = 300             # 한 장에서 돌려줄 최대 박스 수
CLASS_NAMES = ("implant",)


class DetectorError(Exception):
    """모델을 불러오거나 실행할 수 없음. 메시지는 화면에 그대로 보여 줄 수 있는 문장이다."""


class Detector:
    """ONNX 탐지 모델. 만들 때 한 번 불러오고 detect 를 여러 번 부른다.

    session: 테스트용. onnxruntime.InferenceSession 처럼 get_inputs() 와 run() 이 있는 객체
    """

    def __init__(self, model_path: str | Path | None = None, *, session=None, conf: float = DEFAULT_CONF,
                 iou: float = DEFAULT_IOU, class_names: tuple[str, ...] = CLASS_NAMES):
        if session is None:
            session = _open_session(model_path)
        self.session = session
        self.conf, self.iou, self.class_names = conf, iou, tuple(class_names)
        self.model_name = Path(model_path).name if model_path else "custom"
        model_input = session.get_inputs()[0]
        self.input_name = model_input.name
        shape = list(model_input.shape)
        height, width = (shape[2], shape[3]) if len(shape) == 4 else (None, None)
        # 입력 크기가 고정이 아니면(문자열·None) 기본값 640
        self.input_size = (height if isinstance(height, int) and height > 0 else DEFAULT_IMGSZ,
                           width if isinstance(width, int) and width > 0 else DEFAULT_IMGSZ)

    def detect(self, pixels: np.ndarray) -> dict:
        """원본 픽셀 2D(load_dicom 결과) → {"boxes", "model", "conf", "elapsed_ms"}

        boxes: [{"xmin", "ymin", "xmax", "ymax", "score", "class_name"}] 원본 픽셀 좌표, 점수 높은 순
        """
        start = time.perf_counter()
        tensor, meta = preprocess(pixels, self.input_size)
        try:
            outputs = self.session.run(None, {self.input_name: tensor})
        except Exception as exc:
            raise DetectorError("탐지 모델을 실행하지 못했습니다. 모델 파일을 확인하세요.") from exc
        boxes = postprocess(outputs[0], meta, self.conf, self.iou, self.class_names)
        return {"boxes": boxes, "model": self.model_name, "conf": self.conf,
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 1)}


def _open_session(model_path):
    if not model_path or not Path(model_path).is_file():
        raise DetectorError(f"탐지 모델 파일이 없습니다: {model_path}")
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise DetectorError("onnxruntime 이 설치되어 있지 않습니다. pip install -r requirements.txt") from exc
    try:
        return ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    except Exception as exc:
        raise DetectorError("탐지 모델 파일을 열 수 없습니다. ONNX 파일이 맞는지 확인하세요.") from exc


def preprocess(pixels: np.ndarray, input_size: tuple[int, int] = (DEFAULT_IMGSZ, DEFAULT_IMGSZ),
               train_max_size: int | None = TRAIN_MAX_SIZE) -> tuple[np.ndarray, dict]:
    """원본 픽셀 → 모델 입력 (1, 3, H, W) float32 와, 좌표를 되돌릴 정보(meta)."""
    img = apply_window(pixels)  # 자동 윈도우 → uint8 (학습 PNG 와 같음)
    orig_h, orig_w = img.shape
    if train_max_size and max(orig_h, orig_w) > train_max_size:
        s = train_max_size / max(orig_h, orig_w)
        img = cv2.resize(img, (round(orig_w * s), round(orig_h * s)), interpolation=cv2.INTER_AREA)
    small_h, small_w = img.shape

    # ultralytics LetterBox(auto=False) 와 같은 계산
    in_h, in_w = input_size
    r = min(in_h / small_h, in_w / small_w)
    new_w, new_h = int(round(small_w * r)), int(round(small_h * r))
    dw, dh = (in_w - new_w) / 2, (in_h - new_h) / 2
    if (small_w, small_h) != (new_w, new_h):
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=PAD_VALUE)

    tensor = np.repeat(img[None, None, :, :], 3, axis=1).astype(np.float32) / 255.0
    meta = {"orig_w": orig_w, "orig_h": orig_h, "left": left, "top": top,
            "scale_x": (new_w / orig_w), "scale_y": (new_h / orig_h)}  # 원본 → letterbox 안쪽 배율
    return tensor, meta


def postprocess(output: np.ndarray, meta: dict, conf: float = DEFAULT_CONF, iou: float = DEFAULT_IOU,
                class_names: tuple[str, ...] = CLASS_NAMES) -> list[dict]:
    """모델 출력 → 원본 픽셀 좌표 박스 목록 (점수 높은 순)."""
    out = np.asarray(output, dtype=np.float32)
    if out.ndim == 3:
        out = out[0]
    if out.ndim != 2:
        raise DetectorError("탐지 모델 출력 형식이 예상과 다릅니다.")
    n_cls = len(class_names)
    if out.shape[0] != 4 + n_cls and out.shape[1] == 4 + n_cls:
        out = out.T  # (후보 수, 4 + 클래스 수) 로 나오는 모델도 받는다
    if out.shape[0] != 4 + n_cls:
        raise DetectorError(f"탐지 모델 출력 형식이 예상과 다릅니다. (클래스 {n_cls}개 기준, 출력 {out.shape})")

    cls_scores = out[4:]                    # (클래스 수, 후보 수)
    cls_ids = cls_scores.argmax(axis=0)
    scores = cls_scores.max(axis=0)
    keep = scores >= conf
    if not keep.any():
        return []
    cx, cy, w, h = out[0][keep], out[1][keep], out[2][keep], out[3][keep]
    scores, cls_ids = scores[keep], cls_ids[keep]

    # 클래스가 다르면 겹쳐도 지우지 않도록 클래스마다 멀리 떨어뜨린 좌표로 NMS
    offset = cls_ids.astype(np.float32) * 10000.0
    rects = np.stack([cx - w / 2 + offset, cy - h / 2, w, h], axis=1)
    idx = cv2.dnn.NMSBoxes(rects.tolist(), scores.tolist(), conf, iou)
    idx = np.array(idx).reshape(-1)[:MAX_DET]

    boxes = []
    for i in idx:
        x0, y0 = (cx[i] - w[i] / 2 - meta["left"]) / meta["scale_x"], (cy[i] - h[i] / 2 - meta["top"]) / meta["scale_y"]
        x1, y1 = (cx[i] + w[i] / 2 - meta["left"]) / meta["scale_x"], (cy[i] + h[i] / 2 - meta["top"]) / meta["scale_y"]
        x0, x1 = float(np.clip(x0, 0, meta["orig_w"])), float(np.clip(x1, 0, meta["orig_w"]))
        y0, y1 = float(np.clip(y0, 0, meta["orig_h"])), float(np.clip(y1, 0, meta["orig_h"]))
        if x1 - x0 <= 0 or y1 - y0 <= 0:
            continue
        boxes.append({"xmin": round(x0, 1), "ymin": round(y0, 1), "xmax": round(x1, 1), "ymax": round(y1, 1),
                      "score": round(float(scores[i]), 4), "class_name": class_names[int(cls_ids[i])]})
    return sorted(boxes, key=lambda b: b["score"], reverse=True)
