"""로컬 Firebase 설정과 Firestore 읽기 연결 확인. 비밀 값은 출력하지 않는다."""
from pathlib import Path
import socket
import sys
import tomllib

import firebase_admin
from firebase_admin import credentials, firestore


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        config = tomllib.loads((root / ".streamlit/secrets.toml").read_text(encoding="utf-8"))
        path = Path(config["firebase"]["service_account_path"])
        if not path.is_absolute():
            path = root / path
        certificate = credentials.Certificate(str(path))
    except Exception as exc:
        print(f"설정 또는 인증서 확인 실패: {type(exc).__name__}")
        print("secrets.toml의 경로가 현재 실행 환경에서 접근 가능한지 확인하세요.")
        return 1
    print("설정·서비스 계정 인증서 로딩: 정상")

    for host in ("oauth2.googleapis.com", "firestore.googleapis.com"):
        try:
            socket.getaddrinfo(host, 443)
        except OSError:
            print(f"DNS 조회 실패: {host}")
            print("현재 환경의 네트워크·DNS를 확인하세요. 키·권한은 아직 확인되지 않았습니다.")
            return 1
    print("Google 서버 DNS 조회: 정상")

    app = firebase_admin.initialize_app(certificate, name="connection-check")
    try:
        db = firestore.client(app=app)
        docs = list(db.collection("users").limit(1).stream(timeout=10, retry=None))
        print("Firestore 읽기 연결: 성공")
        print("users 문서: 있음" if docs else "users 문서: 없음 (연결 정상)")
        return 0
    except Exception as exc:
        print(f"Firestore 연결 실패: {type(exc).__name__}")
        print("문서 내용과 비공개 키는 출력하지 않았습니다.")
        return 1
    finally:
        firebase_admin.delete_app(app)


if __name__ == "__main__":
    sys.exit(main())
