"""영상처리 알고리즘과 ROI 분석. 담당: C

공통 약속
- 입력: uint8 2D 흑백 numpy 배열 (core/dicom_io.apply_window 의 결과)
- 출력: 같은 크기의 uint8 2D 배열 (원본은 절대 수정하지 않고 새 배열 반환)
- 잘못된 파라미터는 ValueError 로 이유와 함께 알린다
"""
from __future__ import annotations

import cv2
import numpy as np


# --- 알고리즘 1: 명암·분포 조정 ---
def apply_clahe(img: np.ndarray, clip_limit: float = 2.0, tile_size: int = 8) -> np.ndarray:
    if not isinstance(img, np.ndarray) or img.ndim != 2 or img.size == 0:
        raise ValueError("입력 영상은 비어 있지 않은 2D numpy 배열이어야 합니다.")
    if img.dtype != np.uint8:
        raise ValueError("입력 영상의 dtype은 uint8이어야 합니다.")
    if (
        isinstance(clip_limit, bool)
        or not isinstance(clip_limit, (int, float, np.number))
        or not np.isfinite(clip_limit)
        or clip_limit <= 0
    ):
        raise ValueError("clip_limit은 0보다 큰 유한한 숫자여야 합니다.")
    if (
        isinstance(tile_size, bool)
        or not isinstance(tile_size, (int, np.integer))
        or tile_size < 1
    ):
        raise ValueError("tile_size는 1 이상의 정수여야 합니다.")

    tile_size_int = int(tile_size)
    clahe = cv2.createCLAHE(
        clipLimit=float(clip_limit),
        tileGridSize=(tile_size_int, tile_size_int),
    )
    return clahe.apply(img)

# --- 알고리즘 2: 노이즈 제거·평활화 ---
def apply_denoise(img: np.ndarray, method: str = "gaussian", ksize: int = 5) -> np.ndarray:
    """method: "gaussian" | "median" | "bilateral" """
    raise NotImplementedError  # TODO(C)


# --- 알고리즘 3: 영역·경계 추출 ---
def apply_edge(img: np.ndarray, method: str = "canny", low: int = 50, high: int = 150) -> np.ndarray:
    """method: "canny" | "sobel" | "otsu" """
    raise NotImplementedError  # TODO(C)


# 화면에서 이름으로 알고리즘을 고를 때 사용
ALGORITHMS = {
    "CLAHE": apply_clahe,
    "Denoise": apply_denoise,
    "Edge": apply_edge,
}


def roi_stats(img: np.ndarray, x: int, y: int, w: int, h: int) -> dict:
    """ROI 영역의 특징값을 반환한다.

    반환 예: {"x":..,"y":..,"w":..,"h":.., "mean":.., "std":.., "min":.., "max":.., "hist": [256개]}
    ROI가 영상 밖이거나 크기가 0이면 ValueError
    """
    raise NotImplementedError  # TODO(C)
