"""교육용 웹 PACS - 진입점. 사이드바 메뉴(한글)와 로그인 상태를 여기서 정한다.

실행: streamlit run app.py
- 로그인 전: 메뉴를 숨기고 로그인 화면을 보여 준다. 다른 페이지 주소로 들어오면
  각 페이지의 require_login() 이 "로그인이 필요합니다" 안내를 보여 준다 (FR-03)
- 로그인 후: 홈·업무 메뉴와 사용자 정보·로그아웃 버튼이 보인다
새 페이지를 만들면 pages/ 에 파일을 두고 아래 menu() 에 한 줄 추가한다.
"""
import streamlit as st

from core.workflow import ROLE_LABELS
from services.auth import current_user, sign_out

st.set_page_config(page_title="교육용 PACS", page_icon="🩻", layout="wide")


def menu(logged_in: bool) -> dict:
    """사이드바 메뉴. 로그인 전에는 로그인 화면이 첫 페이지, 로그인 후에는 홈이 첫 페이지."""
    return {
        "": [
            st.Page("pages/0_Login.py", title="로그인", icon=":material/login:", default=not logged_in),
            st.Page("pages/0_Home.py", title="홈", icon=":material/home:", default=logged_in),
        ],
        "업무": [
            st.Page("pages/1_Cases.py", title="케이스 목록", icon=":material/list_alt:"),
            st.Page("pages/2_Viewer.py", title="영상 뷰어", icon=":material/image_search:"),
            st.Page("pages/3_Review.py", title="검토", icon=":material/rate_review:"),
        ],
    }


user = current_user()

if user is None:
    page = st.navigation(menu(False), position="hidden")
else:
    pages = menu(True)
    pages[""] = pages[""][1:]  # 로그인 후에는 메뉴에서 '로그인' 숨김
    page = st.navigation(pages)
    with st.sidebar:
        st.markdown(f"**{user['email']}**")
        st.caption(f"역할: {ROLE_LABELS.get(user['role'], user['role'])}")
        if st.button("로그아웃", use_container_width=True):
            sign_out()
            st.rerun()
        st.caption("교육용 모의 케이스 – 실제 진단용 아님")

page.run()
