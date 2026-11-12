"""교육용 웹 PACS - 진입 화면.

실행: streamlit run app.py
"""
import streamlit as st

st.set_page_config(page_title="교육용 PACS", page_icon="🩻", layout="wide")

st.title("🩻 교육용 웹 PACS")
st.caption("X-ray·CT·MRI DICOM 영상 품질 점검 · 모의 케이스 전용 (실제 진단용 아님)")

# TODO(A): Firebase Authentication 로그인 구현 후 아래 임시 상태를 교체
if "user" not in st.session_state:
    st.session_state.user = None

if st.session_state.user is None:
    st.info("로그인 기능은 아직 구현 전입니다. (services/auth.py)")
else:
    st.success(f"{st.session_state.user['email']} ({st.session_state.user['role']})")

st.markdown(
    """
    **메뉴** (왼쪽 사이드바)
    - 케이스 목록: 케이스·영상 등록과 검색
    - 영상 뷰어: 원본/처리 결과 비교, ROI 분석
    - 검토: 상태 변경, 메모·리포트, 처리 이력
    """
)
