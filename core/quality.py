"""특화 기능: 영상 품질·DICOM 태그·시리즈 구성 자동 점검.
담당: C(지표 계산, 기준값), B(태그 점검), A(시리즈 점검)

점검 기준은 촬영 종류(modality)마다 다르다. CR·DX는 같은 X-ray 기준을 쓴다.
"""
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
    return QC_THRESHOLDS[key] 


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

    return sorted(indices) 


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
    }   


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

def detect_foreign_objects(
    pixels: np.ndarray,
    percentile: float = 99.5,
    min_area: int = 30,
    max_area_ratio: float = 0.02,
) -> dict:
    """척추 X-ray 원시 픽셀 2D 배열에서 고밀도 이물질(금속) 의심 영역을 검출한다."""
    if not isinstance(pixels, np.ndarray) or pixels.ndim != 2 or pixels.size == 0:
        raise ValueError("pixels must be a non-empty 2D numpy array")
    if not np.issubdtype(pixels.dtype, np.number):
        raise ValueError("pixels must have a numeric dtype")

    if isinstance(percentile, bool) or not isinstance(percentile, (int, float, np.number)):
        raise ValueError("percentile must be a number between 0 and 100")
    percentile_val = float(percentile)
    if not np.isfinite(percentile_val) or not (0.0 <= percentile_val <= 100.0):
        raise ValueError("percentile must be between 0 and 100")

    if isinstance(min_area, bool) or not isinstance(min_area, (int, np.integer)) or int(min_area) < 0:
        raise ValueError("min_area must be a non-negative integer")
    min_area_val = int(min_area)

    if isinstance(max_area_ratio, bool) or not isinstance(max_area_ratio, (int, float, np.number)):
        raise ValueError("max_area_ratio must be a number in (0, 1]")
    max_area_ratio_val = float(max_area_ratio)
    if not np.isfinite(max_area_ratio_val) or not (0.0 < max_area_ratio_val <= 1.0):
        raise ValueError("max_area_ratio must be in (0, 1]")

    work = np.array(pixels, copy=True)

    thresh = float(np.percentile(work, percentile_val))
    mask_threshold = np.where(work >= thresh, 255, 0).astype(np.uint8)

    kernel_open = np.ones((3, 3), dtype=np.uint8)
    opened = cv2.morphologyEx(mask_threshold, cv2.MORPH_OPEN, kernel_open)

    kernel_close = np.ones((5, 5), dtype=np.uint8)
    mask_cleaned = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel_close)

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask_cleaned)

    total_pixels = work.shape[0] * work.shape[1]
    max_area = total_pixels * max_area_ratio_val

    objects = []
    for label in range(1, num_labels):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_area_val or area > max_area:
            continue

        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        w = int(stats[label, cv2.CC_STAT_WIDTH])
        h = int(stats[label, cv2.CC_STAT_HEIGHT])
        mean_val = float(work[labels == label].mean())

        comp_mask = np.where(labels == label, 255, 0).astype(np.uint8)
        contours, _ = cv2.findContours(comp_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        shape = "other"
        if contours:
            cnt = max(contours, key=cv2.contourArea)
            (_, _), (rw, rh), _ = cv2.minAreaRect(cnt)
            long_side = max(rw, rh)
            short_side = min(rw, rh)
            perimeter = float(cv2.arcLength(cnt, True))

            if (short_side == 0.0 and long_side > 0.0) or (
                short_side > 0.0 and (long_side / short_side) >= 3.0
            ):
                shape = "long"
            elif perimeter > 0.0 and (4.0 * np.pi * area / (perimeter ** 2)) >= 0.7:
                shape = "round"

        objects.append(
            {
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "area": area,
                "mean": mean_val,
                "shape": shape,
            }
        )

    return {
        "objects": objects,
        "steps": {
            "threshold": mask_threshold,
            "cleaned": mask_cleaned,
        },
    }
