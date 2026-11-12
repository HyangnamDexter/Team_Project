"""DICOM 읽기·표시 변환. 담당: B

지원 촬영 종류: X-ray(CR, DX), CT, MRI(MR)
X-ray도 "단면이 1장인 시리즈"로 다룬다. 그래서 등록·저장 코드는 load_series 하나만 쓴다.

다른 모듈과의 약속
- load_series 는 float32 3D 볼륨 (단면 수 N, 세로 H, 가로 W)을 돌려준다. X-ray는 N=1
- 화면 표시와 알고리즘 입력은 볼륨[i] 단면 1장을 apply_window 로 바꾼 uint8 2D 배열이다
  (core/algorithms.py 와 core/quality.py 는 이 uint8 2D 배열만 받는다)
"""
from __future__ import annotations

import numpy as np

SUPPORTED_MODALITIES = {"CR", "DX", "CT", "MR"}

# CT 윈도우 프리셋 (HU 기준, center/width). 화면에서 버튼으로 고른다.
WINDOW_PRESETS = {
    "CT": {
        "폐": (-600, 1500),
        "종격동": (40, 400),
        "뼈": (400, 1800),
        "뇌": (40, 80),
    },
}


class InvalidDicomError(ValueError):
    """DICOM이 아니거나, 픽셀 데이터가 없거나, 지원하지 않는 촬영 종류이거나,
    여러 시리즈가 섞인 파일 묶음."""


def load_dicom(file) -> tuple[np.ndarray, dict]:
    """DICOM 파일 1개를 읽어 (원시 픽셀 2D float32 배열, 헤더 정보 dict)를 반환한다.

    - Rescale Slope/Intercept 적용 (CT는 이 결과가 HU)
    - MONOCHROME1 이면 반전
    - 헤더 dict 예: {"modality", "study_date", "body_part", "rows", "cols",
      "series_uid", "instance_number", "image_position", "image_orientation",
      "pixel_spacing", "slice_thickness", "window_center", "window_width", ...}
    - 잘못된 파일이면 InvalidDicomError
    """
    raise NotImplementedError  # TODO(B)


def load_series(files) -> tuple[np.ndarray, dict]:
    """파일 1개(X-ray) 또는 여러 개(CT·MRI, .zip 포함)를 읽어
    (볼륨 float32 [N, H, W], 시리즈 정보 dict)를 반환한다.

    - 각 파일을 load_dicom 으로 읽는다
    - 단면 순서: ImagePositionPatient 를 ImageOrientationPatient 의 법선 방향으로 투영한 값 기준 정렬.
      위치 태그가 없으면 InstanceNumber 기준
    - 반환 dict 예: {"modality", "series_uid", "num_slices", "positions": [mm...] | None,
      "headers": [단면별 헤더 dict, 정렬된 순서], "rows", "cols", ...}
    - 촬영 종류가 SUPPORTED_MODALITIES 가 아니거나, 단면 크기가 서로 다르면 InvalidDicomError
      (시리즈 혼입·단면 누락 같은 '점검' 항목은 거절하지 않고 core/quality.series_check 가 판정)
    """
    raise NotImplementedError  # TODO(B)


def auto_window(pixels: np.ndarray, low_pct: float = 1.0, high_pct: float = 99.0) -> tuple[float, float]:
    """값 분포의 하위 low_pct%~상위 high_pct% 구간으로 (center, width)를 정한다.
    MRI와 윈도우 태그가 없는 X-ray의 기본 윈도우로 쓴다."""
    raise NotImplementedError  # TODO(B)


def apply_window(pixels: np.ndarray, center: float | None = None, width: float | None = None) -> np.ndarray:
    """2D 단면 1장을 윈도우잉해서 화면 표시용 uint8(0~255) 배열로 반환한다.
    center/width 가 None 이면 auto_window 를 쓴다. (알고리즘 3종에는 포함하지 않음)"""
    raise NotImplementedError  # TODO(B)
