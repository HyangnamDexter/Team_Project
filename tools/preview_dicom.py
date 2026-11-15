"""data/sample/의 실제 DICOM을 읽고 표시하는 개발 확인 화면."""
from pathlib import Path
import sys

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.dicom_io import InvalidDicomError, apply_window, load_dicom


st.set_page_config(page_title="DICOM 샘플 확인", layout="wide")
st.title("DICOM 샘플 읽기·표시 확인")
st.caption("교육용 모의 데이터 확인 · 진단용 아님")

files = sorted(p for p in (ROOT / "data/sample").rglob("*")
               if p.is_file() and p.suffix.lower() == ".dcm")
st.write(f"샘플 DICOM: {len(files)}개 / 준비 목표: 10개")
if not files:
    st.info("RSNA에서 받은 .dcm 파일 10개를 data/sample/에 넣어주세요.")
    st.stop()

selected = st.selectbox("확인할 파일", files,
                        format_func=lambda p: str(p.relative_to(ROOT / "data/sample")))
try:
    pixels, header = load_dicom(selected)
    mode = st.radio("윈도우 설정", ["자동", "DICOM 헤더"], horizontal=True)
    center, width = header["window_center"], header["window_width"]
    if mode == "DICOM 헤더" and center is not None and width is not None:
        image = apply_window(pixels, center, width)
    else:
        if mode == "DICOM 헤더":
            st.info("헤더의 윈도우 값이 없어 자동 윈도우를 적용했습니다.")
        image = apply_window(pixels)
    st.write(f"촬영 종류: {header['modality']} · 크기: {pixels.shape} · "
             f"원본 배열: {pixels.dtype} · 표시 배열: {image.dtype}")
    st.image(image, clamp=True)
except (InvalidDicomError, ValueError) as exc:
    st.error(str(exc))
