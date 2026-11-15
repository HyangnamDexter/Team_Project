"""Cloud Firestore 접근. 담당: A(설계·초기화), B(케이스·영상·메모 함수)

firebase-admin 은 Firestore 보안 규칙을 무시하므로 권한 검사는 core/workflow.py 에서 한다.
컬렉션 설계는 docs/firestore.md 참고.
"""
from __future__ import annotations

import firebase_admin
import streamlit as st
from firebase_admin import credentials, firestore


@st.cache_resource(show_spinner=False)
def get_db():
    """firebase_admin 초기화 후 Firestore 클라이언트 반환 (secrets.toml 의 키 경로 사용).

    Streamlit은 화면을 다시 그릴 때마다 스크립트를 처음부터 실행하므로,
    cache_resource 로 앱이 켜져 있는 동안 한 번만 초기화한다.
    """
    if not firebase_admin._apps:
        cred = credentials.Certificate(st.secrets["firebase"]["service_account_path"])
        firebase_admin.initialize_app(cred)
    return firestore.client()
