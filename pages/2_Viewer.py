"""단일 DICOM 영상 뷰어. 담당: B."""
from hashlib import sha256
from pathlib import Path

import numpy as np
import streamlit as st

from core.dicom_io import InvalidDicomError, WINDOW_PRESETS, apply_window, auto_window, load_dicom
from core.viewer_geometry import measure_distance, viewport
from ui.dicom_canvas import image_canvas
from services.auth import require_login


def set_window(center, width):
    st.session_state.viewer_center = float(center)
    st.session_state.viewer_width = float(width)


def reset_view(center, width):
    set_window(center, width)
    st.session_state.viewer_zoom = 1.0
    st.session_state.viewer_x = 50
    st.session_state.viewer_y = 50
    clear_measurement()


def clear_measurement():
    st.session_state.viewer_points = []
    invalidate_canvas()


def invalidate_canvas():
    st.session_state.viewer_epoch = st.session_state.get("viewer_epoch", 0) + 1


def accept_canvas_event(token, shape):
    event = st.session_state.get("viewer_canvas")
    if not isinstance(event, dict) or event.get("token") != token:
        return
    if event.get("epoch") != st.session_state.viewer_epoch:
        return
    if event.get("id") == st.session_state.get("viewer_event_id"):
        return
    st.session_state.viewer_event_id = event.get("id")
    if event.get("kind") == "navigate":
        values = [event.get(key) for key in ("zoom", "x", "y")]
        if all(isinstance(v, (int, float)) and np.isfinite(v) for v in values):
            st.session_state.viewer_zoom = round(float(np.clip(values[0], 1, 4)), 1)
            st.session_state.viewer_x = int(round(np.clip(values[1], 0, 100)))
            st.session_state.viewer_y = int(round(np.clip(values[2], 0, 100)))
    elif event.get("kind") == "measure":
        point = event.get("point")
        if (isinstance(point, list) and len(point) == 2
                and all(isinstance(v, (int, float)) and np.isfinite(v) for v in point)
                and 0 <= point[0] <= shape[1] - 1 and 0 <= point[1] <= shape[0] - 1):
            points = st.session_state.get("viewer_points", [])
            st.session_state.viewer_points = points + [point] if len(points) == 1 else [point]


st.set_page_config(page_title="영상 뷰어", layout="wide")

user = require_login()
st.session_state.setdefault("viewer_epoch", 0)
st.session_state.setdefault("viewer_points", [])
st.title("영상 뷰어")

with st.sidebar:
    source = st.radio("영상 선택 방법", ["샘플 영상", "파일 업로드"], horizontal=True)
    if source == "샘플 영상":
        sample_dir = Path(__file__).resolve().parents[1] / "data" / "sample"
        samples = sorted(p for p in sample_dir.glob("*")
                         if p.is_file() and p.suffix.lower() in {".dcm", ".dicom"})
        if not samples:
            st.info("data/sample/에 DICOM 파일을 넣거나 파일 업로드를 선택하세요.")
            st.stop()
        selected = st.selectbox("샘플 파일", samples, format_func=lambda p: p.name)
        file = selected
        file_id = (str(selected), selected.stat().st_mtime_ns, selected.stat().st_size)
        filename = selected.name
    else:
        file = st.file_uploader("DICOM 파일", type=["dcm", "dicom"])
        if file is None:
            st.info("표시할 DICOM 파일을 선택하세요.")
            st.stop()
        if file.size > 500 * 1024 * 1024:
            st.error("파일은 500MB 이하로 선택하세요.")
            st.stop()
        file_id = (file.name, sha256(file.getbuffer()).hexdigest())
        filename = file.name

# 세션 안에서 선택한 영상 한 장만 보관하여 설정 조절 시 재해석을 피한다.
if st.session_state.get("viewer_file_id") != file_id:
    try:
        with st.spinner("영상을 읽는 중입니다."):
            pixels, header = load_dicom(file)
        auto_center, auto_width = auto_window(pixels)
        center, width = header["window_center"], header["window_width"]
        if center is None or width is None:
            center, width = auto_center, auto_width
    except (InvalidDicomError, ValueError) as exc:
        st.error(str(exc))
        st.stop()
    st.session_state.viewer_data = (pixels, header, center, width, auto_center, auto_width)
    st.session_state.viewer_file_id = file_id
    reset_view(center, width)

pixels, header, default_center, default_width, auto_center, auto_width = st.session_state.viewer_data
token = sha256(repr(file_id).encode()).hexdigest()
accept_canvas_event(token, pixels.shape)
st.caption(f"{filename} · {header['cols']} × {header['rows']} px · "
           f"촬영 종류 {header['modality'] or '미기재'}")
if "Modality" in header.get("missing_tags", []):
    with st.sidebar:
        st.warning("촬영 종류(Modality) 태그가 없습니다. 등록 정보를 확인하세요.")

controls = st.sidebar
display = st.container()
with controls:
    st.subheader("표시 설정")
    st.button("화면 초기화", on_click=reset_view, args=(default_center, default_width))
    st.button("자동 윈도우", on_click=set_window, args=(auto_center, auto_width))
    if header["modality"] == "CT":
        presets = WINDOW_PRESETS["CT"]
        preset = st.selectbox("CT 프리셋", list(presets))
        st.button("프리셋 적용", on_click=set_window, args=presets[preset])
    center = st.number_input("윈도우 중심 (밝기)", key="viewer_center", step=1.0)
    width = st.number_input("윈도우 폭 (대비)", min_value=1.0, key="viewer_width", step=1.0)
    tool = st.radio("마우스 도구", ["확대·이동", "길이 측정"], key="viewer_tool",
                    on_change=invalidate_canvas)
    if tool == "확대·이동":
        st.caption("좌클릭 확대 · 우클릭 축소 · 드래그 이동")
    else:
        st.caption("뼈의 양 끝을 차례로 클릭하세요. 세 번째 클릭은 새 측정을 시작합니다.")
    st.button("측정 지우기", on_click=clear_measurement)
    zoom = st.slider("확대 배율", 1.0, 4.0, step=0.1, key="viewer_zoom")
    x = st.slider("가로 위치", 0, 100, key="viewer_x", disabled=zoom == 1.0)
    y = st.slider("세로 위치", 0, 100, key="viewer_y", disabled=zoom == 1.0)

if not np.isfinite(center) or not np.isfinite(width):
    st.error("윈도우 중심과 폭에 유한한 숫자를 입력하세요.")
    st.stop()
image = apply_window(pixels, center, width)
left, top, crop_width, crop_height = viewport(image.shape, zoom, x, y)
points = st.session_state.get("viewer_points", [])
label = ""
if len(points) == 2:
    distance, unit = measure_distance(points, header.get("pixel_spacing"))
    label = f"{distance:.2f} {unit}"
    with controls:
        st.metric("두 점 사이 거리", label)
        if unit == "mm":
            st.caption("PixelSpacing 기준 참고 길이입니다. X-ray 촬영 확대와 보정 상태에 따라 실제 길이와 차이가 날 수 있습니다.")
        else:
            st.caption("유효한 PixelSpacing이 없어 픽셀 거리로 표시합니다.")
with display:
    original, adjusted = st.columns(2)
    with original:
        st.subheader("원본")
        image_canvas(apply_window(pixels, default_center, default_width),
                     rows=image.shape[0], cols=image.shape[1],
                     crop=[0, 0, image.shape[1], image.shape[0]], zoom=1.0,
                     mode="view", points=[], label="", token=token,
                     epoch=st.session_state.viewer_epoch, read_only=True,
                     max_height=420, key="viewer_original", default=None)
    with adjusted:
        st.subheader("조절 결과")
        image_canvas(image, rows=image.shape[0], cols=image.shape[1],
                     crop=[left, top, crop_width, crop_height], zoom=zoom,
                     mode="measure" if tool == "길이 측정" else "navigate",
                     points=points, label=label, token=token,
                     epoch=st.session_state.viewer_epoch, read_only=False, max_height=420,
                     key="viewer_canvas", default=None)
        st.caption(f"{zoom:.1f}배 · 표시 영역 x={left}, y={top}, "
                   f"너비={crop_width}, 높이={crop_height}")
