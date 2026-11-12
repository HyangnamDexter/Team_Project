"""특화 기능: 영상 품질·DICOM 태그 자동 점검. 담당: C(지표 계산), B(태그 점검)"""
from __future__ import annotations

import numpy as np


def image_quality(img: np.ndarray) -> dict:
    """밝기·대비·선명도 지표와 기준 미달 사유를 반환한다.

    반환 예: {"brightness":.., "contrast":.., "sharpness":.., "issues": ["너무 어두움", ...]}
    """
    raise NotImplementedError  # TODO(C)


def tag_check(header: dict) -> dict:
    """필수 태그 누락, 개인정보 태그 잔존 여부를 반환한다.

    반환 예: {"missing": ["BodyPartExamined"], "phi_found": ["PatientName"], "issues": [...]}
    """
    raise NotImplementedError  # TODO(B)
