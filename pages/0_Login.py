"""로그인 화면 (FR-01). 로그인하지 않은 사용자에게는 이 페이지만 보인다 (app.py)."""
import streamlit as st

from services.auth import AuthError, sign_in
from core.settings import APP_DESCRIPTION

st.title("🩻 교육용 웹 PACS")
st.caption(f"{APP_DESCRIPTION} · 모의 케이스 전용 (실제 진단용 아님)")

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
