"""A의 탐지 데이터 형식과 모델 로딩·결과 조회를 재사용한다.

- 뷰어의 명시적 탐지 실행·저장은 services.box_review.run_boxes가 담당한다.
- A의 등록 연동에서 사용할 수 있는 run_detection은 모델 부재·실패를 status로 구분한다.
    "done": 탐지함 (박스 0개도 done)  /  "no_model": 모델 없음  /  "failed": 탐지 중 오류
- 결과는 원본 영상 픽셀 좌표라 정답(ground_truth) 박스와 바로 비교할 수 있다

화면(B)에서 쓰는 예:
    from services.detection import get_detections
    det = get_detections(user, image_id, db=get_db())    # 조회. None 이면 탐지 기록 없음
    if det and det["status"] == "done":
        for b in det["boxes"]:
            draw_box(b["xmin"], b["ymin"], b["xmax"], b["ymax"], label=f'{b["score"]:.2f}')
"""
from __future__ import annotations

import logging
from pathlib import Path

import streamlit as st

from services.access import visible_image

COLLECTION = "detections"
DEFAULT_MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "implant.onnx"
STATUS_DONE, STATUS_NO_MODEL, STATUS_FAILED = "done", "no_model", "failed"

log = logging.getLogger(__name__)


def run_detection(detector, pixels) -> dict:
    """탐지를 실행하고 저장할 결과를 만든다. 실패해도 예외를 내지 않는다 (등록을 막지 않기 위해)."""
    empty = {"boxes": [], "n_boxes": 0, "model": None, "conf": None, "elapsed_ms": None, "error": None}
    if detector is None:
        return {**empty, "status": STATUS_NO_MODEL}
    try:
        result = detector.detect(pixels)
    except Exception as exc:  # 모델 오류로 등록이 실패하면 안 된다
        from core.detector import DetectorError

        log.warning("임플란트 탐지 실패: %s", exc)
        message = str(exc) if isinstance(exc, DetectorError) else "탐지 중 오류가 발생했습니다."
        return {**empty, "status": STATUS_FAILED, "model": getattr(detector, "model_name", None), "error": message}
    return {
        "status": STATUS_DONE,
        "boxes": result["boxes"],
        "n_boxes": len(result["boxes"]),
        "model": result["model"],
        "conf": result["conf"],
        "elapsed_ms": result["elapsed_ms"],
        "error": None,
    }


def load_detector(model_path: str | Path | None = None):
    """화면용: 모델을 한 번만 불러와 재사용한다 (Streamlit cache). 모델 파일이 없거나 열 수 없으면 None.

    경로: 인자 > secrets.toml [model] path > models/implant.onnx 또는 models/best.onnx
    프로젝트 밖의 모델은 인자나 비공개 설정으로 명시한다.
    """
    if model_path is None:
        try:
            model_path = st.secrets.get("model", {}).get("path")
        except Exception:  # secrets.toml 이 없어도 기본 경로로 시도
            model_path = None
    if not model_path:
        candidates = (DEFAULT_MODEL_PATH, DEFAULT_MODEL_PATH.parent / "best.onnx")
        model_path = next((p for p in candidates if p.is_file()), DEFAULT_MODEL_PATH)
    return _open_detector(str(model_path))


@st.cache_resource(show_spinner="탐지 모델 불러오는 중...")
def _open_detector(path: str):
    from core.detector import Detector, DetectorError

    if not Path(path).is_file():
        log.info("탐지 모델 파일 없음: %s (탐지 없이 등록)", path)
        return None
    try:
        return Detector(path)
    except DetectorError as exc:
        log.warning("탐지 모델을 열 수 없음: %s", exc)
        return None


def get_detections(user: dict | None, image_id: str, *, db) -> dict | None:
    """등록된 영상의 자동 탐지 결과. 기록이 없으면 None.

    권한: 로그인 필수, 판독의는 본인에게 배정된 케이스의 영상만 (services/access.py).
    없는 영상이면 services.access.ImageNotFound.
    """
    visible_image(user, image_id, db=db)
    snap = db.collection(COLLECTION).document(image_id).get()
    if not snap.exists:
        return None
    doc = dict(snap.to_dict() or {})
    doc.pop("write_token", None)  # 내부용 표시
    return doc
