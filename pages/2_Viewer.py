"""이미지 뷰어 화면. 담당: B (알고리즘 함수는 C의 core/algorithms.py 사용)"""
import streamlit as st

from services.auth import require_login

user = require_login()  # 로그인하지 않았으면 여기서 멈춤 (FR-03)

st.title("영상 뷰어")
# TODO(B): 윈도우잉, 확대·축소·이동, 초기화
# TODO(B): 알고리즘 선택·파라미터 조절, 원본/결과 나란히 비교
# TODO(B): ROI 선택 후 core.algorithms.roi_stats 결과·히스토그램 표시
st.info("구현 예정")
