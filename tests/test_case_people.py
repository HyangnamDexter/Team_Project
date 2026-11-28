"""케이스 참여자 표시의 조회 범위·권한과 한국 시간 변환을 검증한다."""
from datetime import datetime, timezone

import pytest

from core.workflow import PermissionDenied
from services.case_people import UNKNOWN_USER, case_user_labels
from test_review import FakeDB, ADMIN, READER1, READER2, TECH
from ui.display import format_kst


@pytest.mark.parametrize("user", [ADMIN, READER1, TECH])
def test_only_authorized_case_participants_are_resolved(user):
    db = FakeDB()
    db.add_case("C1", "검토 중", "r1", created_by="t1")
    db.data["notes"] = {"N1": {"case_id": "C1", "author_uid": "m1"},
                        "N2": {"case_id": "C2", "author_uid": "r2"}}
    labels = case_user_labels(user, "C1", db=db)
    assert labels == {"r1": "r1@test.com", "m1": "m1@test.com", "t1": "t1@test.com"}
    assert "r2" not in labels


@pytest.mark.parametrize("user", [None, {}, {"uid": "bad", "role": "admin"}])
def test_invalid_user_cannot_read_profiles(user):
    db = FakeDB()
    with pytest.raises(PermissionDenied):
        case_user_labels(user, "C1", db=db)
    assert db.reads == 0


def test_other_reader_cannot_read_case_participants():
    db = FakeDB()
    db.add_case("C1", "검토 중", "r1")
    with pytest.raises(PermissionDenied):
        case_user_labels(READER2, "C1", db=db)
    assert db.reads == 1  # 케이스 권한을 확인한 뒤 사용자 문서는 읽지 않음


def test_display_name_edits_and_deleted_accounts_and_login_email_fallback():
    db = FakeDB()
    db.add_case("C1", "검토 중", "r1")
    db.data["users"]["m1"]["display_name"] = "관리자 이름"
    del db.data["users"]["r1"]
    db.data["notes"] = {"N1": {"case_id": "C1", "author_uid": "deleted",
                                "edits": [{"by": "m1"}]}}
    labels = case_user_labels(READER1, "C1", db=db)
    assert labels == {"r1": READER1["email"], "m1": "관리자 이름", "deleted": UNKNOWN_USER}


@pytest.mark.parametrize("value,expected", [
    (datetime(2026, 11, 28, 4, 6, 15, 836185, tzinfo=timezone.utc), "2026-11-28 13:06:15"),
    (datetime(2026, 11, 28, 4, 6, 15), "2026-11-28 13:06:15"),
    ("2026-11-28T23:06:15Z", "2026-11-29 08:06:15"),
    ("2026-11-28T13:06:15+09:00", "2026-11-28 13:06:15"),
    (None, "-"), ("broken", "-"), (12, "-"),
])
def test_korean_time_is_second_precision_and_handles_legacy_values(value, expected):
    assert format_kst(value) == expected
