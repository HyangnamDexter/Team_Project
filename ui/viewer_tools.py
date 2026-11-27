"""뷰어의 영상처리·히스토그램·박스·처리 이력 패널."""
import numpy as np
import streamlit as st

from core.algorithms import roi_stats
from core.workflow import IN_REVIEW, READER, ROLE_LABELS
from services import box_review, processing
from services.ground_truth import get_ground_truth

COLORS = {"gt": "#30e878", "model": "#ffe34d", "opencv": "#459dff"}


def can_edit(user, case):
    return (user["role"] == READER and case and case.get("assignee_uid") == user["uid"]
            and case.get("status") == IN_REVIEW)


def editing_block_reason(user, case):
    if not case:
        return "등록된 케이스를 선택하세요."
    if user["role"] != READER:
        return (f"현재 {ROLE_LABELS.get(user['role'], user['role'])} 계정입니다. "
                "관리자가 판독의를 배정한 뒤, 담당 판독의 계정으로 로그인해 검토를 시작하세요.")
    if case.get("assignee_uid") != user["uid"]:
        return "본인에게 배정된 케이스에서만 탐지·영상처리를 실행할 수 있습니다."
    status = case.get("status")
    if status == "배정":
        return "현재 상태가 ‘배정’입니다. 검토 화면에서 ‘검토 시작’을 누르면 실행할 수 있습니다."
    if status == "접수":
        return "현재 상태가 ‘접수’입니다. 관리자 배정 후 담당 판독의가 검토를 시작해야 합니다."
    if status == "재촬영 요청":
        return "현재 ‘재촬영 요청’ 상태입니다. 품질 점검 통과 후 배정·검토를 시작해야 실행할 수 있습니다."
    if status == "완료":
        return "완료된 케이스입니다. 관리자가 재검토를 지시하면 담당 판독의가 다시 실행할 수 있습니다."
    return "" if can_edit(user, case) else "검토 중인 본인 배정 케이스에서만 실행할 수 있습니다."


def _set_result(result, image, token):
    st.session_state.viewer_processed = {"token": token, "image": image, "log": result}


def render_history(user, image_id, case, db, root, token):
    """윈도우 위젯이 만들어지기 전에 복원하여 이력의 실행 당시 WL을 표시한다."""
    if not image_id:
        return
    with st.sidebar.expander("처리 이력"):
        logs = processing.list_processing(user, image_id, db=db)
        if not logs:
            st.caption("저장된 처리 이력이 없습니다.")
            return
        by_id = {r["log_id"]: r for r in logs}
        picked = st.selectbox("저장된 처리", list(by_id), key="history_" + image_id,
                              format_func=lambda i: f"{by_id[i]['algorithm']} · {by_id[i]['run_at']}")
        load = st.button("결과 불러오기", key="load_processing")
        replay = st.button("같은 조건으로 재실행", disabled=not can_edit(user, case), key="replay_processing")
        if load or replay:
            try:
                if replay:
                    saved = processing.replay_processing(user, picked, db=db, storage_root=root)
                    picked = saved["log_id"]
                log, image = processing.load_processing(user, picked, db=db, storage_root=root)
                _set_result(log, image, token)
                st.session_state.viewer_center = float(log["window"]["center"])
                st.session_state.viewer_width = float(log["window"]["width"])
                st.session_state.viewer_roi = log["roi"]
                st.session_state.viewer_roi_enabled = log["roi"] is not None
            except Exception as exc:
                st.error(str(exc) if isinstance(exc, (ValueError, PermissionError)) else "이력을 불러오지 못했습니다.")


def render_processing(user, pixels, window, image_id, case, db, root, token):
    with st.sidebar.expander("영상처리·ROI", expanded=False):
        roi = st.session_state.get("viewer_roi")
        use_roi = st.checkbox("ROI 영역만 처리", value=roi is not None, key="viewer_roi_enabled")
        if use_roi:
            h, w = pixels.shape
            roi = roi or {"x": 0, "y": 0, "w": w, "h": h}
            st.caption(f"ROI: x={roi['x']}, y={roi['y']}, 너비={roi['w']}, 높이={roi['h']}")
            st.caption("마우스 도구에서 ROI 선택 후 영상 위를 드래그하세요.")
        else:
            roi = None
        algorithm = st.selectbox("알고리즘", ["CLAHE", "Denoise", "Edge"], key="viewer_algorithm")
        if algorithm == "CLAHE":
            params = {"clip_limit": st.slider("대비 제한", 0.5, 8.0, 2.0, 0.5),
                      "tile_size": st.select_slider("타일 수", [4, 8, 16], value=8)}
        elif algorithm == "Denoise":
            params = {"method": st.selectbox("평활화 방식", ["bilateral", "median", "gaussian"]),
                      "ksize": st.select_slider("커널 크기", [3, 5, 7], value=3)}
        else:
            method = st.selectbox("경계 추출 방식", ["canny", "sobel", "otsu"])
            low, high = st.slider("Canny 임계값", 0, 255, (50, 150), disabled=method != "canny")
            params = {"method": method, "low": low, "high": high}
        allowed = can_edit(user, case) if image_id else user["role"] == READER
        if st.button("알고리즘 적용", disabled=not allowed, key="apply_algorithm"):
            try:
                with st.spinner("영상을 처리하는 중입니다."):
                    if image_id:
                        log = processing.run_processing(user, image_id, algorithm, params, window, roi,
                                                        db=db, storage_root=root)
                        log, image = processing.load_processing(user, log["log_id"], db=db, storage_root=root)
                    else:
                        image, _ = processing.apply_processing(pixels, algorithm, params, window, roi)
                        log = {"algorithm": algorithm, "window": dict(window), "roi": roi}
                    _set_result(log, image, token)
                st.success("처리 완료" + (" · 이력 저장됨" if image_id else " · 저장하지 않은 미리보기"))
            except Exception as exc:
                from core.workflow import PermissionDenied
                st.error(str(exc) if isinstance(exc, (ValueError, PermissionDenied)) else "영상처리에 실패했습니다.")
        if not allowed:
            st.caption(editing_block_reason(user, case))
        if st.button("처리 결과 지우기", key="clear_processed"):
            st.session_state.pop("viewer_processed", None)
        return roi


def processed_image(current, window, token):
    saved = st.session_state.get("viewer_processed")
    if saved and saved["token"] == token and saved["log"]["window"] == window:
        return saved["image"], saved["log"]["algorithm"]
    return current, "조절 결과"


def render_overlays(user, image_id, case, db, root):
    boxes = []
    with st.sidebar.expander("탐지 박스", expanded=True):
        gt_on = st.checkbox("정답 (초록)", value=True, key="show_gt")
        model_on = st.checkbox("모델 탐지 (노랑)", value=True, key="show_model")
        cv_on = st.checkbox("영상처리 검출 (파랑)", value=True, key="show_opencv")
        if not image_id:
            st.caption("등록된 케이스를 선택하면 정답·탐지를 조회할 수 있습니다.")
            return boxes
        gt = get_ground_truth(user, image_id, db=db, storage_root=root)
        if gt is None:
            st.caption("정답 데이터 없음")
        else:
            st.caption(f"정답 {len(gt['boxes'])}개")
            if gt_on:
                boxes.extend(dict(b, color=COLORS["gt"], label=f"GT {i + 1}") for i, b in enumerate(gt["boxes"]))
        allowed = can_edit(user, case)
        reason = editing_block_reason(user, case)
        st.caption(f"계정: {ROLE_LABELS.get(user['role'], user['role'])} · 케이스 상태: {case.get('status', '미기재')}")
        if reason:
            st.info(reason)
            if st.button("검토 화면으로 이동", key="detection_open_review"):
                st.session_state.review_case_id = case["case_id"]
                st.switch_page("pages/3_Review.py")
        for source, enabled, label in (("model", model_on, "모델"), ("opencv", cv_on, "OpenCV")):
            if st.button(f"{label} 탐지 실행", disabled=not allowed, help=reason or None, key="detect_" + source):
                try:
                    with st.spinner(f"{label} 탐지 중입니다."):
                        box_review.run_boxes(user, image_id, source, db=db, storage_root=root)
                except Exception as exc:
                    from core.workflow import PermissionDenied
                    from core.detector import DetectorError
                    st.error(str(exc) if isinstance(exc, (ValueError, PermissionDenied, DetectorError)) else "탐지에 실패했습니다.")
            detection = box_review.get_boxes(user, image_id, source, db=db)
            if not detection or detection.get("status") != "done":
                st.caption(f"{label} 탐지 결과 없음")
                if detection and detection.get("status") == "no_model":
                    st.caption("이전 실행에서 모델 파일을 찾지 못했습니다. 모델 경로를 확인하고 다시 실행하세요.")
                elif detection and detection.get("status") == "failed":
                    st.caption("이전 탐지 실행이 실패했습니다. 다시 실행하면 오류 안내를 확인할 수 있습니다.")
            else:
                st.caption(f"{label} 탐지 {len(detection['boxes'])}개")
                if enabled:
                    boxes.extend(dict(b, color=COLORS[source], label=f"{label} {i + 1}" +
                                      (f" {b['score']:.2f}" if b.get("score") is not None else ""))
                                 for i, b in enumerate(detection["boxes"]))
        st.caption("파랑은 규칙 기반 후보이며 정답이나 재촬영 판단을 뜻하지 않습니다.")
    return boxes


def render_histogram(original, result, roi):
    with st.expander("ROI 통계·히스토그램"):
        h, w = result.shape
        region = roi or {"x": 0, "y": 0, "w": w, "h": h}
        before, after = roi_stats(original, **region), roi_stats(result, **region)
        st.dataframe([{"영상": name, **{k: round(stats[k], 2) for k in ("mean", "std", "min", "max")}}
                      for name, stats in (("처리 전", before), ("결과", after))], hide_index=True)
        st.bar_chart({"처리 전": np.array(before["hist"]), "결과": np.array(after["hist"])})
