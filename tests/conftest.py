import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _NoDocs:
    """빈 컬렉션·없는 문서처럼 동작한다. 화면 테스트가 Firestore 없이 열리게 하는 용도."""

    exists = False

    def __getattr__(self, name):  # where, limit, order_by, collection, document ... 모두 자기 자신
        return lambda *args, **kwargs: self

    def stream(self, *args, **kwargs):
        return iter(())

    def get(self, *args, **kwargs):
        return self

    def to_dict(self):
        return None


class _OfflineDB(_NoDocs):
    """테스트 전용 빈 DB. 읽기는 빈 결과, 쓰기는 바로 실패한다."""

    def batch(self):
        raise AssertionError("테스트에서 가짜 DB를 넘기지 않고 저장하려 했습니다.")

    def transaction(self):
        raise AssertionError("테스트에서 가짜 DB를 넘기지 않고 저장하려 했습니다.")


@pytest.fixture(autouse=True)
def offline_firestore(monkeypatch):
    """모든 테스트에서 실제 Firestore 접속을 막는다.

    secrets.toml 이 있는 컴퓨터에서도 실제 DB를 읽지 않고, 없는 컴퓨터에서도 화면 테스트가 실패하지 않는다.
    DB가 필요한 테스트는 지금처럼 가짜 DB를 직접 넘기거나 get_db 를 다시 바꿔 끼우면 된다.
    """
    import services.db

    monkeypatch.setattr(services.db, "get_db", lambda: _OfflineDB())
