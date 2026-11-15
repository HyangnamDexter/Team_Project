"""케이스 목록·등록·검색 화면. 담당: B"""
import streamlit as st

from services.auth import require_login

user = require_login()  # 로그인하지 않았으면 여기서 멈춤 (FR-03)

st.title("케이스 목록")
# TODO(B): DICOM 업로드 → 헤더 추출 → 케이스 자동 등록
# TODO(B): 검색·필터 (케이스 ID, 검사일, 상태)
st.info("구현 예정")
