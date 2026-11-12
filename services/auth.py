"""Firebase Authentication 로그인. 담당: A

Streamlit(서버)에서 Firebase Auth REST API(signInWithPassword)로 로그인하고,
Firestore users 컬렉션에서 역할을 읽어 st.session_state.user 에 저장한다.
"""


def sign_in(email: str, password: str) -> dict:
    """성공 시 {"uid", "email", "role", "id_token"} 반환, 실패 시 예외."""
    raise NotImplementedError  # TODO(A)


def sign_out() -> None:
    raise NotImplementedError  # TODO(A)
