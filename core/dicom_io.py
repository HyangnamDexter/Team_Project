"""DICOM 읽기·표시 변환. 담당: B.

지원 촬영 종류: X-ray(CR, DX), CT, MRI(MR).
load_dicom은 단일 프레임 흑백 float32 배열을 반환한다.
load_series는 향후 float32 [N, H, W] 볼륨을 반환할 인터페이스이며 아직 미구현이다.
X-ray는 단면 수 N=1인 시리즈로 다룬다.
apply_window 결과(uint8, 2D)를 알고리즘·품질 분석에 전달한다.
MONOCHROME1은 이 모듈에서 반전하므로 화면에서 다시 반전하지 않는다.
"""
from __future__ import annotations

import numpy as np
import pydicom
from pydicom.multival import MultiValue

SUPPORTED_MODALITIES = {"CR", "DX", "CT", "MR"}
WINDOW_PRESETS = {"CT": {"폐": (-600, 1500), "종격동": (40, 400),
                         "뼈": (400, 1800), "뇌": (40, 80)}}


class InvalidDicomError(ValueError):
    """잘못된 DICOM 또는 지원하지 않는 영상."""


def _pixels(pixels: np.ndarray) -> np.ndarray:
    arr = np.asarray(pixels)
    if arr.ndim != 2 or arr.size == 0 or arr.dtype.kind not in "iuf":
        raise ValueError("비어 있지 않은 2차원 실수 픽셀 배열이 필요합니다.")
    arr = arr.astype(np.float64)
    if not np.isfinite(arr).all():
        raise ValueError("픽셀에 NaN 또는 무한대가 있습니다.")
    return arr


def _number(ds, name, default=None):
    value = getattr(ds, name, default)
    if value is None or value == "":
        return default
    if isinstance(value, MultiValue):
        value = value[0]
    value = float(value)
    if not np.isfinite(value):
        raise InvalidDicomError(f"{name} 값이 유효하지 않습니다.")
    return value


def load_dicom(file) -> tuple[np.ndarray, dict]:
    """경로 또는 바이너리 파일을 읽어 (2D float32 픽셀, 헤더)를 반환한다.

    Rescale 적용 후 MONOCHROME1을 관측 범위의 min+max를 기준으로 반전한다.
    이 경우 window_center도 반전 배열의 LINEAR 좌표에 맞춰 변환한다.
    반환 헤더에는 환자 식별 값 대신 비어 있지 않은 태그 이름만 기록한다.
    Modality 누락·빈 값은 modality="", missing_tags=["Modality"]로 기록한다.
    촬영 종류가 명시된 경우 지원 목록을 검사한다.
    압축 영상은 pydicom 디코더가 설치된 경우에만 읽을 수 있다.
    """
    try:
        if hasattr(file, "seek"):
            file.seek(0)
        ds = pydicom.dcmread(file)  # force=True로 가짜 파일을 허용하지 않는다.
        modality = str(getattr(ds, "Modality", "") or "").strip()
        if modality and modality not in SUPPORTED_MODALITIES:
            raise InvalidDicomError("지원하는 촬영 종류는 CR, DX, CT, MR입니다.")
        photo = str(getattr(ds, "PhotometricInterpretation", ""))
        if photo not in {"MONOCHROME1", "MONOCHROME2"}:
            raise InvalidDicomError("단일 프레임 흑백 영상만 지원합니다.")
        if int(getattr(ds, "NumberOfFrames", 1)) != 1:
            raise InvalidDicomError("다중 프레임 영상은 아직 지원하지 않습니다.")
        arr = _pixels(ds.pixel_array)
        arr = arr * _number(ds, "RescaleSlope", 1.0) + _number(ds, "RescaleIntercept", 0.0)
        center = _number(ds, "WindowCenter")
        width = _number(ds, "WindowWidth")
        if width is not None and width < 1:
            raise InvalidDicomError("WindowWidth는 1 이상이어야 합니다.")
        if photo == "MONOCHROME1":
            total = float(arr.min()) + float(arr.max())
            arr = total - arr
            if center is not None:
                center = total - center + 1.0
        with np.errstate(over="ignore", invalid="ignore"):
            arr = arr.astype(np.float32)
        if not np.isfinite(arr).all():
            raise InvalidDicomError("픽셀 변환 결과가 유효하지 않습니다.")
        instance_number = _number(ds, "InstanceNumber")
        header = {
            "modality": modality,
            "missing_tags": ["Modality"] if not modality else [],
            "study_date": str(getattr(ds, "StudyDate", "")),
            "body_part": str(getattr(ds, "BodyPartExamined", "")),
            "rows": int(ds.Rows), "cols": int(ds.Columns),
            "series_uid": str(getattr(ds, "SeriesInstanceUID", "")),
            "instance_number": int(instance_number) if instance_number is not None else None,
            "slice_thickness": _number(ds, "SliceThickness"),
            "window_center": center, "window_width": width,
            "photometric_interpretation": photo,
            "present_tags": [e.keyword for e in ds if e.keyword and not e.is_empty],
        }
        for key, tag in (("image_position", "ImagePositionPatient"),
                         ("image_orientation", "ImageOrientationPatient"),
                         ("pixel_spacing", "PixelSpacing")):
            value = getattr(ds, tag, None)
            header[key] = [float(v) for v in value] if value is not None else None
        return arr, header
    except InvalidDicomError:
        raise
    except Exception as exc:
        # 파서 예외에는 경로·태그 값 등이 포함될 수 있어 사용자 메시지는 고정한다.
        raise InvalidDicomError(
            "DICOM을 읽을 수 없습니다. 파일 형식·픽셀·촬영 종류와 압축 디코더를 확인하세요."
        ) from exc


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
    - 구현 시 A3 등록 서비스와 데이터셋 전달 방식을 맞춘다.
      개인정보 태그를 제거한 뒤 DICOM을 저장할 수 있도록 단면별 pydicom Dataset이 필요하다.
    """
    raise NotImplementedError


def auto_window(pixels: np.ndarray, low_pct: float = 1.0,
                high_pct: float = 99.0) -> tuple[float, float]:
    """백분위 범위를 DICOM LINEAR의 (center, width)로 변환한다.

    center=(low+high+1)/2, width=max(high-low+1, 1).
    상수 영상은 width=2로 표시 중간값을 유지한다.
    """
    arr = _pixels(pixels)
    if not (np.isfinite(low_pct) and np.isfinite(high_pct)
            and 0 <= low_pct < high_pct <= 100):
        raise ValueError("백분위는 0 <= low < high <= 100이어야 합니다.")
    low, high = np.percentile(arr, [low_pct, high_pct])
    if low == high:
        return float(low + 0.5), 2.0
    return float((low + high + 1) / 2), float(high - low + 1)


def apply_window(pixels: np.ndarray, center: float | None = None,
                 width: float | None = None) -> np.ndarray:
    """DICOM LINEAR 윈도우잉. 둘 다 생략하면 자동 계산, 일부 생략은 오류.

    원본을 변경하지 않으며 반올림한 uint8 2D 배열을 반환한다.
    LINEAR 이외 VOI LUT·SIGMOID 및 presentation LUT는 적용하지 않는다.
    """
    arr = _pixels(pixels)
    if center is None and width is None:
        center, width = auto_window(arr)
    elif center is None or width is None:
        raise ValueError("center와 width를 함께 지정하세요.")
    if not (np.isfinite(center) and np.isfinite(width)) or width < 1:
        raise ValueError("유한한 center와 1 이상의 width가 필요합니다.")
    if width == 1:
        return np.where(arr <= center - 0.5, 0, 255).astype(np.uint8)
    result = ((arr - (center - 0.5)) / (width - 1) + 0.5) * 255
    return np.rint(np.clip(result, 0, 255)).astype(np.uint8)
