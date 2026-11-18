"""교육용 웹 PACS - 진입점. 사이드바 메뉴(한글)와 로그인 상태를 여기서 정한다.

실행: streamlit run app.py
- 로그인 전: 로그인 페이지만 보인다 (FR-03)
- 로그인 후: 홈·업무 메뉴와 사용자 정보·로그아웃 버튼이 보인다
새 페이지를 만들면 pages/ 에 파일을 두고 아래 MENU 에 한 줄 추가한다.
"""
import streamlit as st

from core.workflow import ROLE_LABELS
from services.auth import current_user, sign_out

st.set_page_config(page_title="교육용 PACS", page_icon="🩻", layout="wide")

MENU = {
    "": [
        st.Page("pages/0_Home.py", title="홈", icon=":material/home:", default=True),
    ],
    "업무": [
        st.Page("pages/1_Cases.py", title="케이스 목록", icon=":material/list_alt:"),
        st.Page("pages/2_Viewer.py", title="영상 뷰어", icon=":material/image_search:"),
        st.Page("pages/3_Review.py", title="검토", icon=":material/rate_review:"),
    ],
}

user = current_user()

if user is None:
    page = st.navigation([st.Page("pages/0_Login.py", title="로그인", icon=":material/login:")])
else:
    page = st.navigation(MENU)
    with st.sidebar:
        st.markdown(f"**{user['email']}**")
        st.caption(f"역할: {ROLE_LABELS.get(user['role'], user['role'])}")
        if st.button("로그아웃", use_container_width=True):
            sign_out()
            st.rerun()
        st.caption("교육용 모의 케이스 – 실제 진단용 아님")

page.run()
