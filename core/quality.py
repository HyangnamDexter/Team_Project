"""특화 기능: 영상 품질·DICOM 태그·시리즈 구성 자동 점검.
담당: C(지표 계산, 기준값), B(태그 점검), A(시리즈 점검)

점검 기준은 촬영 종류(modality)마다 다르다. CR·DX는 같은 X-ray 기준을 쓴다.
"""
from __future__ import annotations

import numpy as np

# 촬영 종류별 기준값. 숫자는 C가 실험으로 정한다 (docs/experiments/ 에 근거 기록).
# 키 예: {"brightness_min", "brightness_max", "contrast_min", "sharpness_min"}
QC_THRESHOLDS: dict[str, dict[str, float]] = {
    "DX": {},  # TODO(C) X-ray
    "CT": {},  # TODO(C)
    "MR": {},  # TODO(C)
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
    raise NotImplementedError  # TODO(C)


def image_quality(img: np.ndarray, modality: str = "DX") -> dict:
    """uint8 2D 단면 1장의 밝기·대비·선명도 지표와, 촬영 종류별 기준 미달 사유를 반환한다.

    반환 예: {"brightness":.., "contrast":.., "sharpness":.., "issues": ["너무 어두움", ...]}
    """
    raise NotImplementedError  # TODO(C)


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
