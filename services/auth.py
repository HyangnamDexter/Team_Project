"""Firebase Authentication 로그인. 담당: A

Streamlit(서버)에서 Firebase Auth REST API(signInWithPassword)로 로그인하고,
Firestore users 컬렉션에서 역할을 읽어 st.session_state.user 에 저장한다.
요구사항: FR-01(로그인·로그아웃), FR-02(역할 불러오기), FR-03(미로그인 차단)
"""
from __future__ import annotations

import requests
import streamlit as st

from core.workflow import ROLES

SIGN_IN_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
TIMEOUT_SEC = 10

# 화면에 보여 줄 문구. 이메일이 없는 경우와 비밀번호가 틀린 경우를 구분하지 않는다
# (구분하면 어떤 계정이 존재하는지 드러나기 때문).
MSG_EMPTY = "이메일과 비밀번호를 입력하세요."
MSG_BAD_CREDENTIALS = "이메일 또는 비밀번호가 올바르지 않습니다."
MSG_TOO_MANY = "로그인 시도가 너무 많습니다. 잠시 후 다시 시도하세요."
MSG_DISABLED = "사용이 중지된 계정입니다. 관리자에게 문의하세요."
MSG_NETWORK = "인증 서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요."
MSG_NO_ROLE = "역할이 지정되지 않은 계정입니다. 관리자에게 문의하세요."


class AuthError(Exception):
    """로그인 실패. 메시지는 화면에 그대로 보여 줄 수 있는 문장이다."""


def _api_key() -> str:
    return st.secrets["firebase"]["web_api_key"]


def _fetch_role(uid: str) -> str | None:
    """Firestore users/{uid} 문서의 role 값. 문서가 없으면 None."""
    from services.db import get_db  # 테스트에서 Firebase 없이 import 할 수 있도록 여기서 가져온다

    doc = get_db().collection("users").document(uid).get()
    return (doc.to_dict() or {}).get("role") if doc.exists else None


def sign_in(email: str, password: str) -> dict:
    """성공 시 {"uid", "email", "role", "id_token"} 반환, 실패 시 AuthError."""
    email = (email or "").strip()
    if not email or not password:
        raise AuthError(MSG_EMPTY)

    try:
        resp = requests.post(
            SIGN_IN_URL,
            params={"key": _api_key()},
            json={"email": email, "password": password, "returnSecureToken": True},
            timeout=TIMEOUT_SEC,
        )
    except requests.RequestException as exc:
        raise AuthError(MSG_NETWORK) from exc

    if resp.status_code != 200:
        try:
            code = resp.json().get("error", {}).get("message", "")
        except ValueError:
            code = ""
        if code.startswith("TOO_MANY_ATTEMPTS"):
            raise AuthError(MSG_TOO_MANY)
        if code == "USER_DISABLED":
            raise AuthError(MSG_DISABLED)
        raise AuthError(MSG_BAD_CREDENTIALS)

    data = resp.json()
    role = _fetch_role(data["localId"])
    if role not in ROLES:  # 문서 없음, 역할 오타 모두 거절
        raise AuthError(MSG_NO_ROLE)

    return {"uid": data["localId"], "email": data["email"], "role": role, "id_token": data["idToken"]}


def sign_out() -> None:
    """세션에서 로그인 정보를 지운다."""
    st.session_state.pop("user", None)


def current_user() -> dict | None:
    return st.session_state.get("user")


def require_login() -> dict:
    """모든 페이지 맨 위에서 호출한다. 로그인하지 않았으면 안내만 보여 주고 페이지 실행을 멈춘다 (FR-03)."""
    user = current_user()
    if not user:
        st.warning("로그인이 필요합니다.")
        st.page_link("pages/0_Login.py", label="로그인 화면으로 이동", icon="🔐")
        st.stop()
    return user
