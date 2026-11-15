"""검토 업무 화면. 담당: A(상태·권한), B(메모·이력 화면)"""
import streamlit as st

from services.auth import require_login

user = require_login()  # 로그인하지 않았으면 여기서 멈춤 (FR-03)

st.title("검토")
# TODO(A): core/workflow.py 의 상태 전이 규칙과 권한 검사 연결
# TODO(B): 메모·리포트 작성, 처리 이력 조회
st.info("구현 예정")
