"""X-ray 케이스 목록·등록·검색 화면. 담당: B."""
import streamlit as st

from core.workflow import PermissionDenied, RADIOGRAPHER, STATUSES
from services.auth import require_login
from services.db import get_db
from services.review import visible_cases
from ui.case_registration import render_registration

user = require_login()
st.title("케이스 목록")
st.caption("교육용 모의 케이스 – 실제 진단용 아님")

if user["role"] == RADIOGRAPHER:
    render_registration(user)
else:
    st.caption("새 케이스 등록은 방사선사 계정에서 할 수 있습니다.")

search, status_column, modality_column = st.columns([2, 1, 1])
with search:
    query = st.text_input("검색", placeholder="케이스 ID, 검사일, 촬영 부위, 담당자 UID", key="cases_search")
with status_column:
    status = st.selectbox("상태", ["전체", *STATUSES], key="cases_status")
with modality_column:
    modality = st.selectbox("촬영 종류 필터", ["전체", "DX", "CR"], key="cases_modality_filter")
st.button("목록 새로고침", key="cases_refresh")

try:
    # A의 조회 함수가 판독의에게 본인 배정 건만 반환한다.
    limit = st.session_state.get("cases_list_limit", 200)
    cases = visible_cases(user, db=get_db(), limit=limit)
except PermissionDenied as exc:
    st.error(str(exc))
    st.stop()
except Exception:
    st.error("목록을 불러오지 못했습니다. 연결을 확인한 후 새로고침하세요.")
    st.stop()

query = query.strip().casefold()
filtered = [case for case in cases
            if (status == "전체" or case.get("status") == status)
            and (modality == "전체" or case.get("modality") == modality)
            and (not query or query in " ".join(str(case.get(key) or "") for key in
                                                ("case_id", "study_date", "body_part", "assignee_uid")).casefold())]
st.caption(f"조회 {len(cases)}건 중 {len(filtered)}건")
if len(cases) == limit:
    st.info(f"현재 불러온 최대 {limit}건에서 검색합니다. 더 많은 케이스를 찾으려면 추가로 불러오세요.")
    if st.button("200건 더 불러오기", key="cases_load_more"):
        st.session_state.cases_list_limit = limit + 200
        st.rerun()
if not filtered:
    st.info("조건에 맞는 케이스가 없습니다.")
else:
    st.dataframe([{
        "케이스 ID": row["case_id"], "상태": row.get("status", ""),
        "촬영 종류": row.get("modality", ""), "촬영 부위": row.get("body_part") or "미기재",
        "검사일": row.get("study_date") or "미기재",
        "담당자 UID": row.get("assignee_uid") or "미배정",
    } for row in filtered], hide_index=True, use_container_width=True)
    selected = st.selectbox("열 케이스", [row["case_id"] for row in filtered])
    if st.button("영상 뷰어에서 열기"):
        st.session_state.viewer_case_id = selected
        st.session_state.viewer_source = "등록된 케이스"
        st.switch_page("pages/2_Viewer.py")
    if st.button("케이스 검토하기"):
        st.session_state.review_case_id = selected
        st.switch_page("pages/3_Review.py")
