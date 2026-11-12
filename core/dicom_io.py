"""DICOM 읽기·표시 변환. 담당: B

다른 모듈과의 약속: 이 모듈이 만든 numpy 배열(uint8, 2D 흑백)을
core/algorithms.py 와 core/quality.py 가 입력으로 받습니다.
"""
from __future__ import annotations

import numpy as np


class InvalidDicomError(ValueError):
    """DICOM이 아니거나 픽셀 데이터가 없는 파일."""


def load_dicom(file) -> tuple[np.ndarray, dict]:
    """DICOM 파일을 읽어 (원시 픽셀 배열, 헤더 정보 dict)를 반환한다.

    - Rescale Slope/Intercept 적용
    - MONOCHROME1 이면 반전
    - 헤더 dict 예: {"modality", "study_date", "body_part", "rows", "cols", ...}
    - 잘못된 파일이면 InvalidDicomError
    """
    raise NotImplementedError  # TODO(B)


def apply_window(pixels: np.ndarray, center: float | None = None, width: float | None = None) -> np.ndarray:
    """윈도우잉 후 화면 표시용 uint8(0~255) 배열을 반환한다. (알고리즘 3종에는 포함하지 않음)"""
    raise NotImplementedError  # TODO(B)
