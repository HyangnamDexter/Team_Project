"""홈 화면 (로그인 후 첫 화면)."""
import streamlit as st

from core.workflow import ROLE_LABELS
from services.auth import require_login

user = require_login()

st.title("🩻 교육용 웹 PACS")
st.caption("X-ray·CT·MRI DICOM 영상 품질 점검 · 모의 케이스 전용 (실제 진단용 아님)")

st.success(f"{user['email']} 님, {ROLE_LABELS.get(user['role'], user['role'])} 역할로 로그인했습니다.")
st.markdown(
    """
    **메뉴** (왼쪽 사이드바)
    - 케이스 목록: 케이스·영상 등록과 검색
    - 영상 뷰어: 원본/처리 결과 비교, ROI 분석
    - 검토: 상태 변경, 메모·리포트, 처리 이력
    """
)
