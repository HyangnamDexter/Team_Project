from __future__ import annotations

import cv2
import numpy as np

# 촬영 종류별 기준값. 숫자는 C가 실험으로 정한다 (docs/experiments/ 에 근거 기록).
# 키 예: {"brightness_min", "brightness_max", "contrast_min", "sharpness_min"}
QC_THRESHOLDS: dict[str, dict[str, float]] = {
    "DX": {
        "brightness_min": 35.0,
        "brightness_max": 220.0,
        "contrast_min": 25.0,
        "sharpness_min": 50.0,
    },
    "CT": {
        "brightness_min": 25.0,
        "brightness_max": 210.0,
        "contrast_min": 30.0,
        "sharpness_min": 80.0,
    },
    "MR": {
        "brightness_min": 30.0,
        "brightness_max": 215.0,
        "contrast_min": 22.0,
        "sharpness_min": 40.0,
    },
}
QC_THRESHOLDS_VERSION = "v1"


def thresholds_for(modality: str) -> dict[str, float]:
    """CR은 DX 기준을 쓴다. 모르는 촬영 종류는 ValueError."""
    key = "DX" if modality == "CR" else modality
    if key not in QC_THRESHOLDS:
        raise ValueError(f"품질 기준이 없는 촬영 종류입니다: {modality}")
    return QC_THRESHOLDS[key] # TODO(C)


def sample_slices(num_slices: int, k: int = 5) -> list[int]:
    """품질 지표를 계산할 대표 단면 번호. 가운데 단면을 포함해 최대 k장을 고르게 고른다."""
    if isinstance(num_slices, bool) or not isinstance(num_slices, (int, np.integer)) or num_slices <= 0:
        raise ValueError(f"num_slices는 1 이상의 정수여야 합니다. (입력값: {num_slices!r})")
    if isinstance(k, bool) or not isinstance(k, (int, np.integer)) or k <= 0:
        raise ValueError(f"k는 1 이상의 정수여야 합니다. (입력값: {k!r})")

    n = int(num_slices)
    k_i = int(k)
    mid = n // 2

    if n <= k_i:
        return list(range(n))
    if k_i == 1:
        return [mid]

    indices = [int(round(v)) for v in np.linspace(0, n - 1, k_i)]
    if mid not in indices:
        closest_idx = min(range(len(indices)), key=lambda i: abs(indices[i] - mid))
        indices[closest_idx] = mid

    return sorted(indices) # TODO(C)


def image_quality(img: np.ndarray, modality: str = "DX") -> dict:
    """uint8 2D 단면 1장의 밝기·대비·선명도 지표와, 촬영 종류별 기준 미달 사유를 반환한다.

    반환 예: {"brightness":.., "contrast":.., "sharpness":.., "issues": ["너무 어두움", ...]}
    """
    if not isinstance(img, np.ndarray) or img.ndim != 2 or img.size == 0 or img.dtype != np.uint8:
        raise ValueError("입력 영상은 비어 있지 않은 2D uint8 numpy.ndarray여야 합니다.")

    rules = thresholds_for(modality)  # CR -> DX 자동 매핑 및 미지원 모달리티 ValueError

    brightness = float(np.mean(img))
    contrast = float(np.std(img))
    sharpness = float(cv2.Laplacian(img, cv2.CV_64F).var())

    issues: list[str] = []
    if brightness < rules["brightness_min"]:
        issues.append("너무 어두움")
    if brightness > rules["brightness_max"]:
        issues.append("너무 밝음")
    if contrast < rules["contrast_min"]:
        issues.append("대비 부족")
    if sharpness < rules["sharpness_min"]:
        issues.append("선명도 부족")

    return {
        "brightness": round(brightness, 2),
        "contrast": round(contrast, 2),
        "sharpness": round(sharpness, 2),
        "issues": issues,
    }   # TODO(C)


def tag_check(header: dict, modality: str = "DX") -> dict:
    """촬영 종류별 필수 태그 누락, 개인정보 태그 잔존 여부를 반환한다.
    CT·MRI는 PixelSpacing, SliceThickness, ImagePositionPatient, ImageOrientationPatient 도 필수.

    반환 예: {"missing": ["BodyPartExamined"], "phi_found": ["PatientName"], "issues": [...]}
    """
    raise NotImplementedError  # TODO(B)


def series_check(headers: list[dict], positions: list[float] | None, min_slices: int = 10) -> dict:
    """CT·MRI 시리즈 구성을 점검한다. (X-ray는 호출하지 않음)

    - 다른 SeriesInstanceUID 혼입
    - 단면 위치 중복
    - 단면 누락: 이웃 단면 간격이 간격 중앙값의 1.5배를 넘는 곳
    - 단면 수가 min_slices 미만

    반환 예: {"mixed_series": False, "duplicates": 0,
             "gaps": [{"after_index": 89, "position_mm": -6.25}], "issues": ["단면 누락 (위치 -6.25mm 부근)"]}
    """
    raise NotImplementedError  # TODO(A)
