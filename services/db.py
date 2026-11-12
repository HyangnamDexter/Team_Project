"""Cloud Firestore 접근. 담당: A(설계·초기화), B(케이스·영상·메모 함수)

firebase-admin 은 Firestore 보안 규칙을 무시하므로 권한 검사는 core/workflow.py 에서 한다.
컬렉션 설계는 docs/firestore.md 참고.
"""


def get_db():
    """firebase_admin 초기화 후 Firestore 클라이언트 반환 (secrets.toml 의 키 경로 사용)."""
    raise NotImplementedError  # TODO(A)
