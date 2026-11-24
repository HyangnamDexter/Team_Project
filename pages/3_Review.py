"""검토 업무 화면. 담당: A(상태·권한), B(메모·이력 화면)"""
import streamlit as st

from core.workflow import PermissionDenied
from services.auth import require_login
from services.db import get_db
from services.review import (
    ACTION_LABELS, ReviewError, assign_reader, available_actions, complete_review, list_readers,
    request_rereview, request_retake, start_review, visible_cases,
)

user = require_login()  # 로그인하지 않았으면 여기서 멈춤 (FR-03)

st.title("검토")
db = get_db()

# 지난 실행에서 저장한 결과 메시지 (저장 후 화면을 새로 그리면서 보여 준다)
if msg := st.session_state.pop("review_msg", None):
    st.success(msg)

try:
    cases = visible_cases(user, db=db)
except PermissionDenied as e:
    st.error(str(e))
    st.stop()
if not cases:
    st.info("볼 수 있는 케이스가 없습니다." if user["role"] != "reader" else "배정된 케이스가 없습니다.")
    st.stop()

by_id = {c["case_id"]: c for c in cases}
ids = list(by_id)
picked = st.session_state.get("review_case_id")
# 저장할 때마다 선택 상자를 새로 만든다 (같은 key 로 두면 닫힌 상자에 예전 상태 글자가 남는다). 고른 케이스는 유지
case_id = st.selectbox("케이스", ids, index=ids.index(picked) if picked in by_id else 0,
                       key=f"review_case_{st.session_state.get('review_ver', 0)}",
                       format_func=lambda i: f"{i} · {by_id[i].get('status', '?')}")
st.session_state.review_case_id = case_id
case = by_id[case_id]
readers = list_readers(user, db=db) if user["role"] == "manager" else []
email_of = {r["uid"]: r["email"] for r in readers}


def assignee_text(uid):
    if not uid:
        return "없음"
    if uid == user["uid"]:
        return "나"
    return email_of.get(uid, uid)


c1, c2, c3 = st.columns(3)
c1.metric("상태", case.get("status", "?"))
c2.metric("촬영 종류", case.get("modality") or "-")
c3.metric("담당 판독의", assignee_text(case.get("assignee_uid")))


def run(action_label, fn, *args):
    """저장 함수를 실행하고, 성공하면 결과 메시지를 남긴 뒤 화면을 새로 그린다."""
    try:
        result = fn(user, case_id, *args, db=db)
    except (PermissionDenied, ReviewError) as e:
        st.error(str(e))
        return
    st.session_state.review_msg = f"{action_label} 완료: {result['from_status']} → {result['status']}"
    st.session_state.review_ver = st.session_state.get("review_ver", 0) + 1
    st.rerun()


actions = available_actions(user, case)
if not actions:
    st.caption("지금 이 케이스에서 할 수 있는 상태 변경이 없습니다.")

for action in actions:
    label = ACTION_LABELS[action]
    if action in ("assign", "reassign"):
        choices = [r["uid"] for r in readers if r["uid"] != case.get("assignee_uid") or action == "assign"]
        if not choices:
            st.warning("배정할 수 있는 판독의 계정이 없습니다.")
            continue
        with st.form(f"form_{action}"):
            reader_uid = st.selectbox("판독의", choices, format_func=lambda u: email_of.get(u, u))
            if st.form_submit_button(label):
                run(label, assign_reader, reader_uid)
    elif action in ("retake", "rereview"):
        with st.form(f"form_{action}"):
            reason = st.text_area(f"{label} 사유 (필수)", max_chars=500)
            if st.form_submit_button(label):
                run(label, request_retake if action == "retake" else request_rereview, reason)
    elif action == "start":
        if st.button(label, type="primary"):
            run(label, start_review)
    elif action == "complete":
        if st.button(label, type="primary"):
            run(label, complete_review)

# TODO(B): 메모·리포트 작성, 처리 이력 조회 (검토 완료에는 type == "report" 메모가 필요)
