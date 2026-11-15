"""교육용 웹 PACS - 진입 화면 (로그인, 홈).

실행: streamlit run app.py
"""
import streamlit as st

from core.workflow import ROLE_LABELS
from services.auth import AuthError, current_user, sign_in, sign_out

st.set_page_config(page_title="교육용 PACS", page_icon="🩻", layout="wide")

st.title("🩻 교육용 웹 PACS")
st.caption("X-ray·CT·MRI DICOM 영상 품질 점검 · 모의 케이스 전용 (실제 진단용 아님)")

user = current_user()

# ---------- 로그인 전 ----------
if user is None:
    left, _ = st.columns([1, 1])
    with left:
        with st.form("login", clear_on_submit=False):
            st.subheader("로그인")
            email = st.text_input("이메일", placeholder="test000@test.com")
            password = st.text_input("비밀번호", type="password")
            submitted = st.form_submit_button("로그인", type="primary", use_container_width=True)
        if submitted:
            try:
                with st.spinner("로그인 중..."):
                    st.session_state.user = sign_in(email, password)
                st.rerun()
            except AuthError as err:
                st.error(str(err))
    st.stop()

# ---------- 로그인 후 ----------
with st.sidebar:
    st.markdown(f"**{user['email']}**")
    st.caption(f"역할: {ROLE_LABELS.get(user['role'], user['role'])}")
    if st.button("로그아웃", use_container_width=True):
        sign_out()
        st.rerun()
    st.caption("교육용 모의 케이스 – 실제 진단용 아님")

st.success(f"{user['email']} 님, {ROLE_LABELS.get(user['role'], user['role'])} 역할로 로그인했습니다.")
st.markdown(
    """
    **메뉴** (왼쪽 사이드바)
    - 케이스 목록: 케이스·영상 등록과 검색
    - 영상 뷰어: 원본/처리 결과 비교, ROI 분석
    - 검토: 상태 변경, 메모·리포트, 처리 이력
    """
)
