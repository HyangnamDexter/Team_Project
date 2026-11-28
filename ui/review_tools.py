"""검토 화면의 메모·리포트·탐지 오검출 검토·처리 이력 패널."""
from pathlib import Path
from hashlib import sha256
import json

import streamlit as st

from core.dicom_io import apply_window, load_dicom
from core.workflow import DONE, IN_REVIEW, MANAGER, READER, PermissionDenied
from services import box_review, notes, processing
from services.access import case_images, original_path
from ui.dicom_canvas import image_canvas
from ui.viewer_tools import COLORS, can_edit
from ui.display import format_kst
from services.case_people import UNKNOWN_USER


def _message(exc):
    return str(exc) if isinstance(exc, (ValueError, PermissionDenied)) else "저장·조회에 실패했습니다. 연결을 확인하세요."


def render_notes(user, case_id, case, db, user_labels=None):
    user_labels = user_labels or {}
    st.subheader("메모·리포트")
    rows = notes.list_notes(user, case_id, db=db)
    writable = can_edit(user, case) or user["role"] == MANAGER
    if writable:
        with st.form("new_note_" + case_id, clear_on_submit=True):
            kinds = ["memo", "report"] if user["role"] == READER else ["memo"]
            kind = st.selectbox("작성 종류", kinds,
                                format_func=lambda k: "리포트" if k == "report" else "메모")
            text = st.text_area("내용", max_chars=notes.MAX_CONTENT)
            if st.form_submit_button("메모·리포트 저장"):
                try:
                    notes.save_note(user, case_id, text, kind, db=db)
                    st.session_state.review_msg = "메모·리포트 저장 완료"
                    st.rerun()
                except Exception as exc:
                    st.error(_message(exc))
    else:
        st.caption("담당 판독의는 검토 중에 메모·리포트를 작성할 수 있습니다.")
    if not rows:
        st.info("작성된 메모·리포트가 없습니다.")
    for row in rows:
        title = "리포트" if row.get("type") == "report" else "메모"
        author = user_labels.get(row.get("author_uid"), UNKNOWN_USER)
        with st.expander(f"{title} · {author} · {format_kst(row.get('created_at'))}"):
            st.text(row.get("content", ""))
            editable = (row.get("author_uid") == user["uid"] and
                        (user["role"] == MANAGER or (user["role"] == READER and case["status"] != DONE)))
            if editable:
                with st.form("edit_note_" + row["note_id"]):
                    content = st.text_area("수정 내용", value=row["content"], max_chars=notes.MAX_CONTENT)
                    if st.form_submit_button("수정 저장"):
                        try:
                            notes.save_note(user, case_id, content, row["type"], db=db,
                                            note_id=row["note_id"], expected_content=row["content"])
                            st.session_state.review_msg = f"{title} 수정 완료"
                            st.rerun()
                        except Exception as exc:
                            st.error(_message(exc))
            for edit in row.get("edits", []):
                st.caption(f"이전 내용 · {format_kst(edit['at'])} · {user_labels.get(edit['by'], UNKNOWN_USER)}")
                st.text(edit["before"])


def render_box_reviews(user, image_id, case, db, root, user_labels=None):
    user_labels = user_labels or {}
    st.subheader("탐지 박스 검토")
    source = st.selectbox("검토할 탐지", ["model", "opencv"],
                          format_func=lambda s: "모델 (노랑)" if s == "model" else "영상처리 (파랑)")
    doc = box_review.get_boxes(user, image_id, source, db=db)
    if not doc or doc.get("status") != "done" or not doc.get("boxes"):
        st.info("검토할 탐지 박스가 없습니다. 뷰어에서 탐지를 실행하세요.")
        return
    signature = box_review.fingerprint(doc)
    rows = box_review.list_box_reviews(user, image_id, db=db)
    current = {r["box_index"]: r for r in rows if r["source"] == source and r["fingerprint"] == signature}
    st.caption(f"전체 {len(doc['boxes'])}개 · 검토 완료 {len(current)}개")
    selection_context = (user["uid"], image_id, source, signature)
    previous = st.session_state.get("review_box_choice", {})
    picked = previous.get("index", 0) if previous.get("context") == selection_context else 0
    if picked not in range(len(doc["boxes"])):
        picked = 0
    verdicts = [current.get(i, {}).get("verdict", "미검토") for i in range(len(doc["boxes"]))]
    revision = sha256(json.dumps(verdicts, ensure_ascii=False).encode()).hexdigest()[:12]
    # 표시 문구가 바뀌면 위젯을 새로 만들되, 별도로 보관한 박스 번호를 유지한다.
    index = st.selectbox("탐지 박스", list(range(len(doc["boxes"]))),
                         index=picked,
                         format_func=lambda i: f"박스 {i + 1} · {current.get(i, {}).get('verdict', '미검토')}",
                         key=f"review_box_{image_id}_{source}_{signature[:12]}_{revision}")
    st.session_state.review_box_choice = {"context": selection_context, "index": index}
    # 선택한 박스만 보여 주어 밀집된 OpenCV 후보도 한 개씩 확인할 수 있다.
    from services.access import visible_image
    image_doc = visible_image(user, image_id, db=db)
    try:
        pixels, header = load_dicom(original_path(image_doc, root))
        wc, ww = header.get("window_center"), header.get("window_width")
        image = apply_window(pixels, wc, ww) if wc is not None and ww is not None else apply_window(pixels)
        box = dict(doc["boxes"][index], color=COLORS[source], label=f"박스 {index + 1}")
        image_canvas(image, rows=image.shape[0], cols=image.shape[1], crop=[0, 0, image.shape[1], image.shape[0]],
                     zoom=1, mode="view", points=[], boxes=[box], label="", token=signature,
                     epoch=0, read_only=True, max_height=480, key="review_box_canvas", default=None)
    except (OSError, ValueError):
        st.warning("검토용 영상을 읽을 수 없습니다. 저장 경로를 확인하세요.")
    st.write({k: doc["boxes"][index][k] for k in ("xmin", "ymin", "xmax", "ymax")})
    if index in current:
        st.caption(f"저장된 판정: {current[index]['verdict']} · "
                   f"{user_labels.get(current[index]['reviewed_by'], UNKNOWN_USER)}")
    if can_edit(user, case):
        with st.form(f"box_verdict_{image_id}_{source}_{signature[:12]}_{revision}_{index}"):
            verdict = st.radio("판정", ["맞음", "오검출"],
                               index=1 if current.get(index, {}).get("verdict") == "오검출" else 0,
                               horizontal=True)
            if st.form_submit_button("박스 검토 저장"):
                try:
                    box_review.save_box_review(user, image_id, source, index, verdict, signature, db=db)
                    st.session_state.review_msg = f"박스 {index + 1}: {verdict} 저장 완료"
                    st.rerun()
                except Exception as exc:
                    st.error(_message(exc))
    else:
        st.caption("검토 중인 케이스의 담당 판독의만 박스 판정을 저장할 수 있습니다.")


def render_review_tools(user, case_id, case, db, user_labels=None):
    user_labels = user_labels or {}
    st.caption("시각은 한국 시간(KST)으로 표시합니다.")
    render_notes(user, case_id, case, db, user_labels)
    with st.expander("케이스 이력"):
        history = notes.list_case_history(user, case_id, db=db)
        for row in history:
            st.text(f"{format_kst(row['at'])} · {row.get('from_status') or '-'} → {row['to_status']} · "
                    f"{row.get('reason') or '-'} · {user_labels.get(row['actor_uid'], UNKNOWN_USER)}")
    images = case_images(user, case_id, db=db)
    if not images:
        return
    st.subheader("케이스 영상")
    image_id = st.selectbox("검토 영상", [r["image_id"] for r in images])
    if st.button("뷰어에서 영상·박스 열기"):
        st.session_state.viewer_source = "등록된 케이스"
        st.session_state.viewer_case_id = case_id
        st.session_state.viewer_image_id = image_id
        st.switch_page("pages/2_Viewer.py")
    root = Path(st.secrets["storage"]["image_dir"])
    render_box_reviews(user, image_id, case, db, root, user_labels)
    with st.expander("처리 이력"):
        logs = processing.list_processing(user, image_id, db=db)
        if not logs:
            st.caption("저장된 처리 이력이 없습니다.")
        for row in logs:
            st.write({**{k: row[k] for k in ("log_id", "algorithm", "window", "params", "roi")},
                      "실행 시각": format_kst(row.get("run_at")),
                      "실행자": user_labels.get(row.get("run_by"), UNKNOWN_USER)})
        st.caption("뷰어에서 결과 불러오기·같은 조건 재실행을 할 수 있습니다.")
