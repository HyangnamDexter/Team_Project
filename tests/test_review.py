"""services/review.py 테스트 (배정·상태 변경 T4~T9, 권한 검사). Firebase 없이 가짜 DB로 확인한다."""
import inspect
from datetime import datetime, timezone
from itertools import count

import pytest

import services.cases
import services.review as review
from core.workflow import (
    ASSIGNED, DONE, IN_REVIEW, MANAGER, QC_FAILED, RADIOGRAPHER, READER, RECEIVED, PermissionDenied,
)
from services.review import (
    ReviewError, assign_reader, available_actions, complete_review, list_readers, request_rereview,
    request_retake, start_review, visible_cases,
)

NOW = datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc)
ADMIN = {"uid": "m1", "email": "m1@test.com", "role": MANAGER}
READER1 = {"uid": "r1", "email": "r1@test.com", "role": READER}
READER2 = {"uid": "r2", "email": "r2@test.com", "role": READER}
TECH = {"uid": "t1", "email": "t1@test.com", "role": RADIOGRAPHER}


# ---------------------------------------------------------------- 가짜 Firestore
class Snap:
    def __init__(self, doc_id, data):
        self.id, self._data = doc_id, data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class Ref:
    def __init__(self, db, col, doc_id):
        self.db, self.col, self.id = db, col, doc_id

    def get(self, transaction=None):
        self.db.reads += 1
        return Snap(self.id, self.db.data.get(self.col, {}).get(self.id))


class Query:
    def __init__(self, db, col, filters=(), n=None):
        self.db, self.col, self.filters, self.n = db, col, list(filters), n

    def document(self, doc_id=None):
        if doc_id is None:
            doc_id = f"auto{next(self.db.ids)}"
        return Ref(self.db, self.col, doc_id)

    def where(self, *, filter):
        return Query(self.db, self.col, self.filters + [(filter.field_path, filter.op_string, filter.value)], self.n)

    def limit(self, n):
        return Query(self.db, self.col, self.filters, n)

    def stream(self, transaction=None):
        self.db.reads += 1
        rows = [Snap(i, d) for i, d in self.db.data.get(self.col, {}).items()
                if all(op == "==" and d.get(f) == v for f, op, v in self.filters)]
        return iter(rows[: self.n] if self.n else rows)


class Tx:
    """쓰기는 모아 두었다가 commit 때 한 번에 반영한다 (예외가 나면 아무것도 저장 안 됨)."""

    def __init__(self, db):
        self.db, self.writes = db, []

    def update(self, ref, fields):
        self.writes.append(("update", ref, fields))

    def set(self, ref, doc):
        self.writes.append(("set", ref, doc))

    def commit(self):
        for kind, ref, doc in self.writes:
            col = self.db.data.setdefault(ref.col, {})
            if kind == "update":
                assert ref.id in col, "update 는 있는 문서에만"
                col[ref.id] = {**col[ref.id], **doc}
            else:
                col[ref.id] = dict(doc)


class FakeDB:
    def __init__(self):
        self.data = {"users": {u["uid"]: {"email": u["email"], "role": u["role"]}
                               for u in (ADMIN, READER1, READER2, TECH)},
                     "cases": {}, "case_history": {}, "notes": {}}
        self.ids, self.reads = count(1), 0

    def collection(self, name):
        return Query(self, name)

    def add_case(self, case_id, status, assignee=None, **extra):
        self.data["cases"][case_id] = {"status": status, "assignee_uid": assignee, "modality": "DX",
                                       "updated_at": NOW, **extra}

    def history(self):
        return list(self.data["case_history"].values())


@pytest.fixture(autouse=True)
def fake_transaction(monkeypatch):
    def run(db, work):
        tx = Tx(db)
        result = work(tx)
        tx.commit()
        return result
    monkeypatch.setattr(review, "_run_transaction", run)


@pytest.fixture
def db():
    return FakeDB()


def case(db, case_id="C1"):
    return db.data["cases"][case_id]


# ---------------------------------------------------------------- T4·T5 배정
def test_assign_received_case(db):
    db.add_case("C1", RECEIVED)
    result = assign_reader(ADMIN, "C1", "r1", db=db, now=NOW)
    assert result == {"case_id": "C1", "from_status": RECEIVED, "status": ASSIGNED}
    assert case(db)["status"] == ASSIGNED and case(db)["assignee_uid"] == "r1" and case(db)["updated_at"] == NOW
    assert db.history() == [{"case_id": "C1", "from_status": RECEIVED, "to_status": ASSIGNED, "reason": None,
                             "extra": {"from_uid": None, "to_uid": "r1"}, "actor_uid": "m1", "at": NOW}]


def test_reassign_records_previous_reader(db):
    db.add_case("C1", ASSIGNED, "r1")
    assign_reader(ADMIN, "C1", "r2", db=db, now=NOW)
    assert case(db)["assignee_uid"] == "r2"
    assert db.history()[0]["extra"] == {"from_uid": "r1", "to_uid": "r2"}


def test_reassign_to_same_reader_rejected(db):
    db.add_case("C1", ASSIGNED, "r1")
    with pytest.raises(ReviewError, match="이미"):
        assign_reader(ADMIN, "C1", "r1", db=db)
    assert db.history() == []


def test_assign_after_retake_can_pick_previous_reader(db):
    # 재촬영 → 재등록으로 다시 접수된 케이스는 이전 담당자가 남아 있어도 다시 배정할 수 있다
    db.add_case("C1", RECEIVED, "r1")
    assign_reader(ADMIN, "C1", "r1", db=db)
    assert case(db)["status"] == ASSIGNED


@pytest.mark.parametrize("target", ["m1", "t1", "nobody", "", None])
def test_assign_only_to_reader_accounts(db, target):
    db.add_case("C1", RECEIVED)
    with pytest.raises(ReviewError):
        assign_reader(ADMIN, "C1", target, db=db)
    assert case(db)["status"] == RECEIVED and db.history() == []


@pytest.mark.parametrize("status", [QC_FAILED, IN_REVIEW, DONE])
def test_assign_wrong_status_rejected(db, status):
    db.add_case("C1", status, "r1")
    with pytest.raises(PermissionDenied, match=f"현재 상태\\({status}\\)"):
        assign_reader(ADMIN, "C1", "r2", db=db)
    assert case(db)["assignee_uid"] == "r1"


# ---------------------------------------------------------------- T6 검토 시작
def test_assigned_reader_starts_review(db):
    db.add_case("C1", ASSIGNED, "r1")
    start_review(READER1, "C1", db=db, now=NOW)
    assert case(db)["status"] == IN_REVIEW
    assert db.history()[0]["actor_uid"] == "r1"


def test_other_reader_cannot_start(db):
    db.add_case("C1", ASSIGNED, "r1")
    with pytest.raises(PermissionDenied, match="본인에게 배정된"):
        start_review(READER2, "C1", db=db)
    assert case(db)["status"] == ASSIGNED and db.history() == []


def test_double_click_second_request_rejected(db):
    # 같은 요청이 두 번 와도 두 번째는 현재 상태를 다시 읽고 거절한다 (NFR-05)
    db.add_case("C1", ASSIGNED, "r1")
    start_review(READER1, "C1", db=db)
    with pytest.raises(PermissionDenied, match="현재 상태\\(검토 중\\)"):
        start_review(READER1, "C1", db=db)
    assert len(db.history()) == 1


# ---------------------------------------------------------------- T7 재촬영 요청
def test_retake_requires_reason(db):
    db.add_case("C1", IN_REVIEW, "r1")
    for bad in ("", "   ", None, "가" * (review.REASON_MAX + 1)):
        with pytest.raises(ReviewError):
            request_retake(READER1, "C1", bad, db=db)
    assert case(db)["status"] == IN_REVIEW and db.history() == []


def test_retake_saves_reason(db):
    db.add_case("C1", IN_REVIEW, "r1")
    request_retake(READER1, "C1", "  흔들림이 심함 ", db=db)
    assert case(db)["status"] == QC_FAILED
    assert db.history()[0]["reason"] == "흔들림이 심함"


def test_retake_by_other_reader_rejected(db):
    db.add_case("C1", IN_REVIEW, "r1")
    with pytest.raises(PermissionDenied):
        request_retake(READER2, "C1", "사유", db=db)


# ---------------------------------------------------------------- T8 검토 완료
def test_complete_requires_report(db):
    db.add_case("C1", IN_REVIEW, "r1")
    db.data["notes"]["N0"] = {"case_id": "C1", "type": "memo"}      # 일반 메모는 리포트가 아님
    db.data["notes"]["N1"] = {"case_id": "C2", "type": "report"}    # 다른 케이스의 리포트
    with pytest.raises(ReviewError, match="리포트"):
        complete_review(READER1, "C1", db=db)
    assert case(db)["status"] == IN_REVIEW and db.history() == []


def test_complete_with_report_records_report_id(db):
    db.add_case("C1", IN_REVIEW, "r1")
    db.data["notes"]["N9"] = {"case_id": "C1", "type": "report"}
    complete_review(READER1, "C1", db=db)
    assert case(db)["status"] == DONE
    assert db.history()[0]["extra"] == {"report_id": "N9"}


def test_complete_by_other_reader_rejected_even_with_report(db):
    db.add_case("C1", IN_REVIEW, "r1")
    db.data["notes"]["N9"] = {"case_id": "C1", "type": "report"}
    with pytest.raises(PermissionDenied, match="본인에게 배정된"):
        complete_review(READER2, "C1", db=db)


# ---------------------------------------------------------------- T9 재검토 지시
def test_rereview_by_manager_with_reason(db):
    db.add_case("C1", DONE, "r1")
    request_rereview(ADMIN, "C1", "측정값 확인 필요", db=db)
    assert case(db)["status"] == IN_REVIEW and case(db)["assignee_uid"] == "r1"
    assert db.history()[0]["reason"] == "측정값 확인 필요"


def test_rereview_requires_reason(db):
    db.add_case("C1", DONE, "r1")
    with pytest.raises(ReviewError):
        request_rereview(ADMIN, "C1", " ", db=db)


# ---------------------------------------------------------------- 공통
def test_missing_case(db):
    with pytest.raises(ReviewError, match="찾을 수 없습니다"):
        start_review(READER1, "NOPE", db=db)


def test_full_workflow(db):
    db.add_case("C1", RECEIVED)
    assign_reader(ADMIN, "C1", "r1", db=db)
    assign_reader(ADMIN, "C1", "r2", db=db)
    start_review(READER2, "C1", db=db)
    db.data["notes"]["N1"] = {"case_id": "C1", "type": "report"}
    complete_review(READER2, "C1", db=db)
    request_rereview(ADMIN, "C1", "다시 확인", db=db)
    request_retake(READER2, "C1", "재촬영 필요", db=db)
    assert [(h["from_status"], h["to_status"]) for h in db.history()] == [
        (RECEIVED, ASSIGNED), (ASSIGNED, ASSIGNED), (ASSIGNED, IN_REVIEW),
        (IN_REVIEW, DONE), (DONE, IN_REVIEW), (IN_REVIEW, QC_FAILED)]


# ---------------------------------------------------------------- 역할 검사 (DB를 건드리기 전에 거절)
CALLS = {
    "assign_reader": lambda u, db: assign_reader(u, "C1", "r1", db=db),
    "start_review": lambda u, db: start_review(u, "C1", db=db),
    "request_retake": lambda u, db: request_retake(u, "C1", "사유", db=db),
    "complete_review": lambda u, db: complete_review(u, "C1", db=db),
    "request_rereview": lambda u, db: request_rereview(u, "C1", "사유", db=db),
    "list_readers": lambda u, db: list_readers(u, db=db),
}
ALLOWED = {"assign_reader": MANAGER, "start_review": READER, "request_retake": READER,
           "complete_review": READER, "request_rereview": MANAGER, "list_readers": MANAGER}


@pytest.mark.parametrize("name", CALLS)
@pytest.mark.parametrize("user", [None, {}, {"uid": "x", "role": "admin"}, ADMIN, READER1, TECH])
def test_wrong_role_rejected_before_db_access(db, name, user):
    if user and user.get("role") == ALLOWED[name]:
        pytest.skip("허용된 역할")
    db.add_case("C1", RECEIVED)
    with pytest.raises(PermissionDenied):
        CALLS[name](user, db)
    assert db.reads == 0 and db.history() == []


# 새 저장 함수를 만들고 권한 검사 테스트를 빠뜨리면 여기서 실패한다
# visible_cases: 아래 test_visible_cases_requires_login, register_case: test_cases.py 의 역할 테스트,
# available_actions: DB를 건드리지 않는 버튼 표시용 (로그인 안 하면 빈 목록, test_available_actions)
CHECKED = set(CALLS) | {"visible_cases", "register_case", "available_actions"}


@pytest.mark.parametrize("module", [services.review, services.cases])
def test_every_user_function_is_permission_tested(module):
    public = {name for name, fn in inspect.getmembers(module, inspect.isfunction)
              if fn.__module__ == module.__name__ and not name.startswith("_")
              and next(iter(inspect.signature(fn).parameters), None) == "user"}
    assert public - CHECKED == set(), "권한 검사 테스트가 없는 함수"


@pytest.mark.parametrize("user", [None, {}, {"uid": "x", "role": "admin"}])
def test_visible_cases_requires_login(db, user):
    with pytest.raises(PermissionDenied):
        visible_cases(user, db=db)


# ---------------------------------------------------------------- 조회
def test_reader_sees_only_assigned_cases(db):
    db.add_case("C1", ASSIGNED, "r1")
    db.add_case("C2", ASSIGNED, "r2")
    db.add_case("C3", RECEIVED)
    assert [c["case_id"] for c in visible_cases(READER1, db=db)] == ["C1"]
    assert {c["case_id"] for c in visible_cases(ADMIN, db=db)} == {"C1", "C2", "C3"}
    assert {c["case_id"] for c in visible_cases(TECH, db=db)} == {"C1", "C2", "C3"}


def test_visible_cases_newest_first(db):
    db.add_case("OLD", RECEIVED, updated_at=datetime(2026, 11, 1, tzinfo=timezone.utc))
    db.add_case("NEW", RECEIVED, updated_at=datetime(2026, 11, 20, tzinfo=timezone.utc))
    db.add_case("NODATE", RECEIVED, updated_at=None)
    assert [c["case_id"] for c in visible_cases(ADMIN, db=db)] == ["NEW", "OLD", "NODATE"]


def test_list_readers(db):
    assert list_readers(ADMIN, db=db) == [{"uid": "r1", "email": "r1@test.com"},
                                          {"uid": "r2", "email": "r2@test.com"}]


@pytest.mark.parametrize("user,status,assignee,expected", [
    (ADMIN, RECEIVED, None, ["assign"]),
    (ADMIN, ASSIGNED, "r1", ["reassign"]),
    (ADMIN, DONE, "r1", ["rereview"]),
    (ADMIN, IN_REVIEW, "r1", []),
    (READER1, ASSIGNED, "r1", ["start"]),
    (READER1, IN_REVIEW, "r1", ["retake", "complete"]),
    (READER2, IN_REVIEW, "r1", []),       # 담당자 아님
    (READER1, DONE, "r1", []),
    (TECH, RECEIVED, None, []),
    (TECH, QC_FAILED, None, []),          # T3 재등록은 등록 화면에서
    (None, RECEIVED, None, []),
])
def test_available_actions(user, status, assignee, expected):
    assert available_actions(user, {"status": status, "assignee_uid": assignee}) == expected


# ---------------------------------------------------------------- 검토 화면 (Streamlit AppTest)
@pytest.fixture
def page(db, monkeypatch):
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    import services.db
    monkeypatch.setattr(services.db, "get_db", lambda: db)
    app = str(__import__("pathlib").Path(__file__).resolve().parent.parent / "app.py")

    def open_as(user):
        at = AppTest.from_file(app, default_timeout=30)
        at.session_state["user"] = user
        at.run()
        return at.switch_page("pages/3_Review.py").run()
    return open_as


def button(at, label):
    return next(b for b in at.button if b.label == label)


def test_page_manager_assigns_reader(db, page):
    db.add_case("C1", RECEIVED)
    at = page(ADMIN)
    assert not at.exception
    assert [m.value for m in at.metric][:1] == [RECEIVED]
    reader_box = next(s for s in at.selectbox if s.label == "판독의")
    assert reader_box.options == ["r1@test.com", "r2@test.com"]
    reader_box.set_value("r2")
    button(at, "배정").click().run()
    assert not at.exception
    assert case(db)["status"] == ASSIGNED and case(db)["assignee_uid"] == "r2"
    assert any("배정 완료" in s.value for s in at.success)


def test_page_reader_sees_only_own_case_and_starts(db, page):
    db.add_case("MINE", ASSIGNED, "r1")
    db.add_case("OTHER", ASSIGNED, "r2")
    at = page(READER1)
    assert not at.exception
    assert next(s for s in at.selectbox if s.label == "케이스").options == ["MINE · 배정"]
    button(at, "검토 시작").click().run()
    assert case(db, "MINE")["status"] == IN_REVIEW
    assert {b.label for b in at.button} >= {"재촬영 요청", "검토 완료"}


def test_page_complete_without_report_shows_error(db, page):
    db.add_case("C1", IN_REVIEW, "r1")
    at = page(READER1)
    button(at, "검토 완료").click().run()
    assert any("리포트" in e.value for e in at.error)
    assert case(db)["status"] == IN_REVIEW


def test_page_retake_without_reason_shows_error(db, page):
    db.add_case("C1", IN_REVIEW, "r1")
    at = page(READER1)
    button(at, "재촬영 요청").click().run()
    assert any("사유" in e.value for e in at.error)
    assert case(db)["status"] == IN_REVIEW


def test_page_reader_without_cases(db, page):
    at = page(READER1)
    assert not at.exception
    assert any("배정된 케이스가 없습니다" in i.value for i in at.info)


def test_page_radiographer_has_no_buttons(db, page):
    db.add_case("C1", RECEIVED)
    at = page(TECH)
    assert not at.exception
    assert not at.button
    assert any("할 수 있는 상태 변경이 없습니다" in c.value for c in at.caption)
