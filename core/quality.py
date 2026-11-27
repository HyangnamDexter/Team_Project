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
        "sharpness_min": 30.0,
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
QC_THRESHOLDS_VERSION = "v2"


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
    from core.dicom_privacy import IDENTIFIERS

    thresholds_for(modality)
    present = set(header.get("present_tags", []))
    required = {"Modality", "BodyPartExamined", "Rows", "Columns"}
    if modality in {"CT", "MR"}:
        required |= {"PixelSpacing", "SliceThickness", "ImagePositionPatient", "ImageOrientationPatient"}
    # 등록 시 사용자가 지정한 촬영 종류도 유효한 메타데이터로 취급한다.
    if header.get("modality"):
        present.add("Modality")
    missing = sorted(required - present)
    phi = sorted(set(header.get("phi_tags", [])) | (present & IDENTIFIERS))
    # VinDr 샘플의 촬영 부위 누락은 경고로 남긴다. 픽셀 품질 실패와 구분한다.
    warnings = ["촬영 부위 태그 누락: BodyPartExamined"] if "BodyPartExamined" in missing else []
    issues = [f"필수 태그 누락: {tag}" for tag in missing if tag != "BodyPartExamined"]
    if phi:
        issues.append("개인정보 태그 잔존")
    if header.get("burned_in_annotation", "").upper() == "YES":
        issues.append("픽셀 내 문자 포함 표시: 수동 익명화 확인 필요")
    return {"missing": missing, "phi_found": phi, "issues": issues, "warnings": warnings}


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
    bg_sigma: float | None = None,
) -> dict:
    """척추 X-ray 원시 픽셀 2D 배열에서 고밀도 이물질(금속) 의심 영역을 검출한다.

    입력 배열은 복사해서 사용하므로 원본을 수정하지 않는다.

    Args:
        pixels: 윈도우잉 전 원시 픽셀 2D 숫자형 ``np.ndarray`` (비어 있지 않아야 함).
        percentile: 고밀도 후보 추출에 사용할 백분위 기준값 (``0 <= percentile <= 100``).
        min_area: 잡음으로 제외할 최소 연결 요소 면적(픽셀 수, 0 이상 정수).
        max_area_ratio: 뼈 등 큰 구조물로 제외할 최대 면적 비율 (``0 < max_area_ratio <= 1``).
        bg_sigma: 선택적 가우시안 배경 제거용 표준편차(``None`` 또는 0보다 큰 유한한 숫자).
            ``None``이면 기존 베이스라인과 동일하게 원시 픽셀에 직접 백분위 임계값을 적용한다.
            양수 값을 주면 ``float32``로 변환한 영상에서 ``cv2.GaussianBlur(..., sigmaX=bg_sigma)``로
            추정한 저주파 배경을 뺀 영상(``work_f - background``)에 임계값을 적용한다.
            단, 각 객체의 ``mean`` 값은 ``bg_sigma`` 사용 여부와 무관하게 원본 ``pixels`` 평균을 반환한다.

    Returns:
        다음 키를 포함하는 ``dict``:
        - ``objects`` (``list[dict]``): 검출된 의심 영역 목록. 각 항목은 다음 필드를 가진다.
            - ``x``, ``y``, ``w``, ``h`` (``int``): **입력 영상 픽셀 좌표 기준** 바운딩 박스
              좌상단 좌표(``x``, ``y``) 및 너비·높이(``w``, ``h``).
            - ``area`` (``int``): 연결 요소의 픽셀 수.
            - ``mean`` (``float``): 해당 연결 요소 위치의 **원본 ``pixels`` 원시값 평균**.
            - ``shape`` (``str``): 형태 분류 (``"long"`` | ``"round"`` | ``"other"``).
        - ``steps`` (``dict[str, np.ndarray]``): 단계별 중간 이진 마스크.
            - ``threshold``: 모폴로지 연산 전 임계값 적용 마스크 (``uint8``, 값 ``0`` 또는 ``255``).
            - ``cleaned``: 모폴로지 열기(3x3)·닫기(5x5) 후 정제 마스크 (``uint8``, 값 ``0`` 또는 ``255``).

    Raises:
        ValueError: ``pixels``가 2D 숫자형 비어 있지 않은 배열이 아니거나,
            ``percentile``, ``min_area``, ``max_area_ratio``, ``bg_sigma``가 유효 범위를 벗어난 경우.
    """
    if not isinstance(pixels, np.ndarray) or pixels.ndim != 2 or pixels.size == 0:
        raise ValueError("pixels는 비어 있지 않은 2차원 numpy 배열이어야 합니다.")
    if not np.issubdtype(pixels.dtype, np.number):
        raise ValueError("pixels는 숫자형 dtype이어야 합니다.")

    if isinstance(percentile, bool) or not isinstance(percentile, (int, float, np.number)):
        raise ValueError("percentile은 0 이상 100 이하의 숫자여야 합니다.")
    percentile_val = float(percentile)
    if not np.isfinite(percentile_val) or not (0.0 <= percentile_val <= 100.0):
        raise ValueError("percentile은 0 이상 100 이하의 유한한 값이어야 합니다.")

    if isinstance(min_area, bool) or not isinstance(min_area, (int, np.integer)) or int(min_area) < 0:
        raise ValueError("min_area는 0 이상의 정수여야 합니다.")
    min_area_val = int(min_area)

    if isinstance(max_area_ratio, bool) or not isinstance(max_area_ratio, (int, float, np.number)):
        raise ValueError("max_area_ratio는 0 초과 1 이하의 숫자여야 합니다.")
    max_area_ratio_val = float(max_area_ratio)
    if not np.isfinite(max_area_ratio_val) or not (0.0 < max_area_ratio_val <= 1.0):
        raise ValueError("max_area_ratio는 0 초과 1 이하의 유한한 값이어야 합니다.")

    bg_sigma_val: float | None = None
    if bg_sigma is not None:
        if isinstance(bg_sigma, bool) or not isinstance(bg_sigma, (int, float, np.number)):
            raise ValueError("bg_sigma는 None이거나 0보다 큰 숫자여야 합니다.")
        bg_sigma_val = float(bg_sigma)
        if not np.isfinite(bg_sigma_val) or bg_sigma_val <= 0.0:
            raise ValueError("bg_sigma는 0보다 큰 유한한 값이어야 합니다.")

    work = np.array(pixels, copy=True)

    if bg_sigma_val is not None:
        work_f = work.astype(np.float32)
        background = cv2.GaussianBlur(work_f, (0, 0), sigmaX=bg_sigma_val)
        target = work_f - background
    else:
        target = work

    thresh = float(np.percentile(target, percentile_val))
    mask_threshold = np.where(target >= thresh, 255, 0).astype(np.uint8)

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
