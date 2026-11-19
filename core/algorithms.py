"""영상처리 알고리즘과 ROI 분석. 담당: C

공통 약속
- 입력: uint8 2D 흑백 numpy 배열 (core/dicom_io.apply_window 의 결과)
- 출력: 같은 크기의 uint8 2D 배열 (원본은 절대 수정하지 않고 새 배열 반환)
- 잘못된 파라미터는 ValueError 로 이유와 함께 알린다
"""
from __future__ import annotations

import cv2
import numpy as np

_VALID_DENOISE_METHODS = ("gaussian", "median", "bilateral")
_VALID_EDGE_METHODS = ("canny", "sobel", "otsu")


def _validate_uint8_2d(img: np.ndarray) -> None:
    if not isinstance(img, np.ndarray):
        raise ValueError("입력 영상은 numpy.ndarray여야 합니다.")
    if img.ndim != 2:
        raise ValueError(f"입력 영상은 2D 흑백 배열이어야 합니다. (현재 ndim={img.ndim})")
    if img.size == 0:
        raise ValueError("빈 영상 배열은 처리할 수 없습니다.")
    if img.dtype != np.uint8:
        raise ValueError(f"입력 영상의 dtype은 uint8이어야 합니다. (현재 dtype={img.dtype})")


def _validate_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name}는 정수여야 합니다. (입력값: {value!r})")
    return int(value)


def _validate_odd_ksize(ksize: int) -> int:
    k = _validate_int(ksize, "ksize")
    if k < 3 or k % 2 == 0:
        raise ValueError(f"ksize는 3 이상의 홀수여야 합니다. (입력값: {k})")
    return k


# --- 알고리즘 1: 명암·분포 조정 ---
def apply_clahe(img: np.ndarray, clip_limit: float = 2.0, tile_size: int = 8) -> np.ndarray:
    _validate_uint8_2d(img)

    if (
        isinstance(clip_limit, bool)
        or not isinstance(clip_limit, (int, float, np.number))
        or not np.isfinite(clip_limit)
        or float(clip_limit) <= 0.0
    ):
        raise ValueError(f"clip_limit은 0보다 큰 유한한 숫자여야 합니다. (입력값: {clip_limit!r})")

    t = _validate_int(tile_size, "tile_size")
    if t <= 0:
        raise ValueError(f"tile_size는 1 이상의 정수여야 합니다. (입력값: {t})")

    src = np.ascontiguousarray(img)
    clahe = cv2.createCLAHE(clipLimit=float(clip_limit), tileGridSize=(t, t))
    out = clahe.apply(src)
    return np.asarray(out, dtype=np.uint8)


# --- 알고리즘 2: 노이즈 제거·평활화 ---
def apply_denoise(img: np.ndarray, method: str = "gaussian", ksize: int = 5) -> np.ndarray:
    """method: "gaussian" | "median" | "bilateral" """
    _validate_uint8_2d(img)
    if not isinstance(method, str) or method not in _VALID_DENOISE_METHODS:
        raise ValueError(f"지원하지 않는 method입니다: {method!r} (허용: {_VALID_DENOISE_METHODS})")

    k = _validate_odd_ksize(ksize)
    src = np.ascontiguousarray(img)

    if method == "gaussian":
        out = cv2.GaussianBlur(src, (k, k), 0)
    elif method == "median":
        out = cv2.medianBlur(src, k)
    else:
        out = cv2.bilateralFilter(src, d=k, sigmaColor=75, sigmaSpace=75)

    return np.asarray(out, dtype=np.uint8)


# --- 알고리즘 3: 영역·경계 추출 ---
def apply_edge(img: np.ndarray, method: str = "canny", low: int = 50, high: int = 150) -> np.ndarray:
    """method: "canny" | "sobel" | "otsu" """
    _validate_uint8_2d(img)
    if not isinstance(method, str) or method not in _VALID_EDGE_METHODS:
        raise ValueError(f"지원하지 않는 method입니다: {method!r} (허용: {_VALID_EDGE_METHODS})")

    for name, val in (("low", low), ("high", high)):
        if isinstance(val, bool) or not isinstance(val, (int, float, np.number)) or not np.isfinite(val):
            raise ValueError(f"{name}는 유한한 숫자여야 합니다. (입력값: {val!r})")
    if low < 0 or high <= low:
        raise ValueError(f"임계값은 0 <= low < high 조건을 만족해야 합니다. (low={low}, high={high})")

    src = np.ascontiguousarray(img)

    if method == "canny":
        out = cv2.Canny(src, float(low), float(high))
    elif method == "sobel":
        gx = cv2.Sobel(src, cv2.CV_64F, 1, 0, ksize=3)
        gy = cv2.Sobel(src, cv2.CV_64F, 0, 1, ksize=3)
        mag = np.hypot(gx, gy)
        max_val = float(mag.max())
        if max_val > 0:
            out = np.clip((mag / max_val) * 255.0, 0, 255).astype(np.uint8)
        else:
            out = np.zeros_like(src, dtype=np.uint8)
    else:  # "otsu"
        _, out = cv2.threshold(src, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    return np.asarray(out, dtype=np.uint8)


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
    _validate_uint8_2d(img)
    x_i = _validate_int(x, "x")
    y_i = _validate_int(y, "y")
    w_i = _validate_int(w, "w")
    h_i = _validate_int(h, "h")

    if x_i < 0 or y_i < 0:
        raise ValueError(f"ROI 시작 좌표는 0 이상이어야 합니다. (x={x_i}, y={y_i})")
    if w_i <= 0 or h_i <= 0:
        raise ValueError(f"ROI 너비와 높이는 1 이상이어야 합니다. (w={w_i}, h={h_i})")

    img_h, img_w = img.shape
    if x_i + w_i > img_w or y_i + h_i > img_h:
        raise ValueError(
            f"ROI 영역이 영상 범위({img_w}x{img_h})를 벗어났습니다. "
            f"(x+w={x_i + w_i}, y+h={y_i + h_i})"
        )

    patch = img[y_i : y_i + h_i, x_i : x_i + w_i]
    hist = np.bincount(patch.ravel(), minlength=256).tolist()

    return {
        "x": x_i,
        "y": y_i,
        "w": w_i,
        "h": h_i,
        "mean": float(patch.mean()),
        "std": float(patch.std()),
        "min": int(patch.min()),
        "max": int(patch.max()),
        "hist": hist,
    }