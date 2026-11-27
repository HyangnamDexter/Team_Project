"""X-ray 단일 DICOM 영상 뷰어. 담당: B."""
from hashlib import sha256
from pathlib import Path

import numpy as np
import streamlit as st

from core.dicom_io import InvalidDicomError, apply_window, auto_window, load_dicom
from core.viewer_geometry import measure_distance, roi_from_points, viewport
from core.workflow import PermissionDenied, READER
from services.access import case_images, original_path
from services.db import get_db
from services.review import visible_cases
from ui.viewer_tools import (render_history, render_processing, render_overlays,
                             processed_image, render_histogram)
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
    st.session_state.viewer_roi = None
    st.session_state.viewer_roi_enabled = False
    st.session_state.pop("viewer_processed", None)


def clear_measurement():
    st.session_state.viewer_points = []
    invalidate_canvas()


def invalidate_canvas():
    st.session_state.viewer_epoch = st.session_state.get("viewer_epoch", 0) + 1


def pen_command(action):
    sequence = st.session_state.get("viewer_pen_command_seq", 0) + 1
    st.session_state.viewer_pen_command_seq = sequence
    st.session_state.viewer_pen_command = {"id": sequence, "action": action}


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
    elif event.get("kind") == "roi":
        try:
            st.session_state.viewer_roi = roi_from_points(event["start"], event["end"], shape)
            st.session_state.viewer_roi_enabled = True
        except (KeyError, TypeError, ValueError, OverflowError):
            pass


st.set_page_config(page_title="영상 뷰어", layout="wide")

user = require_login()
st.session_state.setdefault("viewer_epoch", 0)
st.session_state.setdefault("viewer_points", [])
st.title("영상 뷰어")
st.caption("X-ray 단일 DICOM 영상")
registered_image_id = None
registered_case = None
registered_image = None
db = None
storage_root = None

with st.sidebar:
    source = st.radio("영상 선택 방법", ["샘플 영상", "파일 업로드", "등록된 케이스"],
                      key="viewer_source")
    if source == "등록된 케이스":
        try:
            db = get_db()
            choices = visible_cases(user, db=db, limit=1000)
            by_case = {c["case_id"]: c for c in choices}
            if not choices:
                st.info("조회 가능한 등록 케이스가 없습니다.")
                st.stop()
            ids = list(by_case)
            wanted = st.session_state.get("viewer_case_id")
            case_id = st.selectbox("등록 케이스", ids, index=ids.index(wanted) if wanted in by_case else 0)
            registered_case = by_case[case_id]
            images = case_images(user, case_id, db=db)
            if not images:
                st.info("케이스에 등록된 영상이 없습니다.")
                st.stop()
            by_image = {r["image_id"]: r for r in images}
            wanted_image = st.session_state.get("viewer_image_id")
            image_ids = list(by_image)
            registered_image_id = st.selectbox("등록 영상", image_ids,
                index=image_ids.index(wanted_image) if wanted_image in by_image else 0)
            registered_image = by_image[registered_image_id]
            storage_root = Path(st.secrets["storage"]["image_dir"])
            file = original_path(registered_image, storage_root)
            file_id = (user["uid"], registered_image_id, file.stat().st_mtime_ns)
            filename = f"{case_id} / {registered_image_id}"
        except Exception as exc:
            st.error(str(exc) if isinstance(exc, (PermissionDenied, ValueError)) else "등록 영상을 불러오지 못했습니다.")
            st.stop()
    elif source == "샘플 영상":
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
        file = st.file_uploader("X-ray DICOM 파일 1개", type=["dcm", "dicom"],
                                accept_multiple_files=False)
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
        if registered_image:
            header = dict(header, modality=registered_image["modality"])
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
if header["modality"] and header["modality"] not in {"CR", "DX"}:
    st.error("X-ray(CR·DX) 단일 영상만 선택하세요.")
    st.stop()
# 다른 업무 화면을 방문하면 Streamlit이 뷰어 위젯 상태를 정리할 수 있다.
if any(key not in st.session_state for key in
       ("viewer_center", "viewer_width", "viewer_zoom", "viewer_x", "viewer_y")):
    reset_view(default_center, default_width)
token = sha256(repr(file_id).encode()).hexdigest()
render_history(user, registered_image_id, registered_case, db, storage_root, token)
accept_canvas_event(token, pixels.shape)
st.caption(f"{filename} · {header['cols']} × {header['rows']} px · "
           f"촬영 종류 {header['modality'] or '미기재'}")
if registered_image:
    with st.sidebar.expander("등록 품질·태그 점검"):
        qc = registered_image.get("qc", {})
        st.write({k: qc.get(k) for k in ("result", "thresholds_version", "brightness", "contrast", "sharpness")})
        st.write("필수 태그 누락:", qc.get("missing_tags", []))
        st.write("저장 전 제거한 개인정보 태그:", qc.get("phi_removed", []))
        for issue in qc.get("issues", []):
            st.warning(issue)
        for warning in qc.get("warnings", []):
            st.warning(warning)
if "Modality" in header.get("missing_tags", []):
    with st.sidebar:
        st.warning("촬영 종류(Modality) 태그가 없습니다. 등록 정보를 확인하세요.")

controls = st.sidebar
display = st.container()
pen_color, pen_width = "#ff4d4f", 4
with controls:
    st.subheader("표시 설정")
    st.button("화면 초기화", on_click=reset_view, args=(default_center, default_width))
    st.button("자동 윈도우", on_click=set_window, args=(auto_center, auto_width))
    center = st.number_input("윈도우 중심 (밝기)", key="viewer_center", step=1.0)
    width = st.number_input("윈도우 폭 (대비)", min_value=1.0, key="viewer_width", step=1.0)
    tools = ["확대·이동", "길이 측정", "ROI 선택"]
    if user["role"] == READER:
        tools += ["펜", "지우개"]
    if st.session_state.get("viewer_tool") not in tools:
        st.session_state.viewer_tool = tools[0]
    tool = st.radio("마우스 도구", tools, key="viewer_tool",
                    on_change=invalidate_canvas)
    if tool == "확대·이동":
        st.caption("좌클릭 확대 · 우클릭 축소 · 드래그 이동")
    elif tool == "길이 측정":
        st.caption("뼈의 양 끝을 차례로 클릭하세요. 세 번째 클릭은 새 측정을 시작합니다.")
    elif tool == "ROI 선택":
        st.caption("영상 위를 드래그해서 처리할 사각형 영역을 선택하세요.")
    elif tool == "펜":
        st.caption("오른쪽 영상 위에 마우스·손가락·펜으로 그리세요.")
    else:
        st.caption("그림을 문지르면 닿은 선 전체가 지워집니다. 박스·측정선은 지우지 않습니다.")
    if user["role"] == READER:
        with st.expander("펜 설정", expanded=tool in {"펜", "지우개"}):
            color = st.selectbox("펜 색", ["빨강", "노랑", "초록"], key="viewer_pen_color")
            pen_color = {"빨강": "#ff4d4f", "노랑": "#ffe34d", "초록": "#30e878"}[color]
            pen_width = st.select_slider("펜 굵기", [2, 4, 8], value=4, key="viewer_pen_width",
                                        format_func=lambda n: {2: "가늘게", 4: "보통", 8: "굵게"}[n])
            st.button("펜 되돌리기", key="pen_undo", on_click=pen_command, args=("undo",))
            st.button("그림 전체 지우기", key="pen_clear", on_click=pen_command, args=("clear",))
            st.caption("임시 그림입니다. 원본에는 저장하지 않으며, 다른 영상으로 바꾸면 지워집니다.")
    st.button("측정 지우기", on_click=clear_measurement)
    zoom = st.slider("확대 배율", 1.0, 4.0, step=0.1, key="viewer_zoom")
    x = st.slider("가로 위치", 0, 100, key="viewer_x", disabled=zoom == 1.0)
    y = st.slider("세로 위치", 0, 100, key="viewer_y", disabled=zoom == 1.0)

if not np.isfinite(center) or not np.isfinite(width):
    st.error("윈도우 중심과 폭에 유한한 숫자를 입력하세요.")
    st.stop()
window = {"center": center, "width": width}
input_image = apply_window(pixels, center, width)
roi = render_processing(user, pixels, window, registered_image_id, registered_case, db, storage_root, token)
image, result_label = processed_image(input_image, window, token)
try:
    boxes = render_overlays(user, registered_image_id, registered_case, db, storage_root)
except Exception:
    st.warning("탐지·정답 데이터를 조회하지 못했습니다. 연결을 확인하세요.")
    boxes = []
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
        image_canvas(input_image,
                     rows=image.shape[0], cols=image.shape[1],
                     crop=[0, 0, image.shape[1], image.shape[0]], zoom=1.0,
                     mode="view", points=[], label="", token=token,
                     epoch=st.session_state.viewer_epoch, read_only=True,
                     max_height=420, boxes=boxes, roi=roi, key="viewer_original", default=None)
    with adjusted:
        st.subheader(result_label)
        image_canvas(image, rows=image.shape[0], cols=image.shape[1],
                     crop=[left, top, crop_width, crop_height], zoom=zoom,
                     mode={"길이 측정": "measure", "ROI 선택": "roi", "펜": "pen", "지우개": "eraser"}.get(tool, "navigate"),
                     points=points, label=label, token=token,
                     epoch=st.session_state.viewer_epoch, read_only=False, max_height=420,
                     boxes=boxes, roi=roi, key="viewer_canvas", default=None,
                     drawing_token=f"{user['uid']}:{token}", pen_color=pen_color, pen_width=pen_width,
                     pen_command=st.session_state.get("viewer_pen_command"))
        st.caption(f"{zoom:.1f}배 · 표시 영역 x={left}, y={top}, "
                   f"너비={crop_width}, 높이={crop_height}")
render_histogram(input_image, image, roi)
if registered_image_id and st.button("이 케이스 검토하기"):
    st.session_state.review_case_id = registered_image["case_id"]
    st.switch_page("pages/3_Review.py")
