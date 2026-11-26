"""판독의 배정과 검토 상태 변경 (T4~T9). 담당: A — docs/workflow.md 2·4장

모든 저장 함수는 같은 순서로 검사한다.
1. 맨 앞에서 로그인·역할 확인 (require_role) → 틀리면 DB를 건드리지 않고 PermissionDenied
2. Firestore 트랜잭션 안에서 케이스를 다시 읽고 상태 규칙 확인 (require_transition)
   → 같은 버튼을 두 번 눌러도 두 번째 요청은 "현재 상태에서는 바꿀 수 없습니다"로 거절된다 (NFR-05)
3. 담당자 본인 여부, 사유·리포트 필수 같은 조건 확인 → 틀리면 PermissionDenied 또는 ReviewError
4. 케이스 상태 변경과 case_history 기록을 한 트랜잭션으로 저장 (둘 중 하나만 저장되는 일이 없다)

화면에서 쓰는 예:
    from services.review import start_review, ReviewError
    from core.workflow import PermissionDenied
    try:
        start_review(user, case_id, db=get_db())
    except (PermissionDenied, ReviewError) as e:
        st.error(str(e))  # 메시지는 화면에 그대로 보여 줄 수 있는 문장

T3(재촬영 요청 → 접수)은 영상을 다시 등록하는 일이라 등록 쪽(services/cases.py)에서 다룬다.
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.workflow import (
    ASSIGNED, DONE, IN_REVIEW, MANAGER, QC_FAILED, READER, RECEIVED,
    PermissionDenied, next_statuses, require_role, require_transition,
)

REASON_MAX = 500  # 사유 최대 글자 수

# (현재 상태, 다음 상태) → 화면 버튼 이름. available_actions 가 쓴다.
ACTIONS = {
    (RECEIVED, ASSIGNED): "assign",      # T4 배정
    (ASSIGNED, ASSIGNED): "reassign",    # T5 재배정
    (ASSIGNED, IN_REVIEW): "start",      # T6 검토 시작
    (IN_REVIEW, QC_FAILED): "retake",    # T7 재촬영 요청
    (IN_REVIEW, DONE): "complete",       # T8 검토 완료
    (DONE, IN_REVIEW): "rereview",       # T9 재검토 지시
}
ACTION_LABELS = {"assign": "배정", "reassign": "재배정", "start": "검토 시작", "retake": "재촬영 요청",
                 "complete": "검토 완료", "rereview": "재검토 지시"}


class ReviewError(Exception):
    """조건이 맞지 않아 상태를 바꿀 수 없음 (사유 없음, 리포트 없음 등). 메시지는 화면에 그대로 보여 준다."""


# ---------------------------------------------------------------- 상태 변경 (저장 함수)
def assign_reader(user: dict | None, case_id: str, reader_uid: str, *, db, now: datetime | None = None) -> dict:
    """T4 배정 / T5 재배정. 관리자만. 대상은 역할이 판독의인 계정이어야 한다."""
    require_role(user, MANAGER)
    reader_uid = (reader_uid or "").strip()
    if not reader_uid:
        raise ReviewError("배정할 판독의를 선택하세요.")
    reader_ref = db.collection("users").document(reader_uid)

    def check(tx, case):
        reader = reader_ref.get(transaction=tx)
        if not reader.exists or (reader.to_dict() or {}).get("role") != READER:
            raise ReviewError("판독의 계정에만 배정할 수 있습니다.")
        # 재배정(T5)에서만 막는다. 재촬영 후 다시 접수된 케이스는 이전 담당자에게 다시 배정할 수 있다
        if case.get("status") == ASSIGNED and case.get("assignee_uid") == reader_uid:
            raise ReviewError("이미 이 판독의에게 배정되어 있습니다.")
        return {"assignee_uid": reader_uid}, {"from_uid": case.get("assignee_uid"), "to_uid": reader_uid}

    return _change(db, user, case_id, ASSIGNED, now=now, check=check)


def start_review(user: dict | None, case_id: str, *, db, now: datetime | None = None) -> dict:
    """T6 검토 시작. 담당 판독의만."""
    require_role(user, READER)
    return _change(db, user, case_id, IN_REVIEW, now=now, check=_assignee_only(user))


def request_retake(user: dict | None, case_id: str, reason: str, *, db, now: datetime | None = None) -> dict:
    """T7 재촬영 요청. 담당 판독의만, 사유 필수."""
    require_role(user, READER)
    reason = _reason(reason)
    return _change(db, user, case_id, QC_FAILED, now=now, reason=reason, check=_assignee_only(user))


def complete_review(user: dict | None, case_id: str, *, db, now: datetime | None = None) -> dict:
    """T8 검토 완료. 담당 판독의만, 이 케이스의 요약 리포트(notes.type == "report")가 있어야 한다."""
    require_role(user, READER)
    from google.cloud.firestore_v1.base_query import FieldFilter

    report_query = (db.collection("notes")
                    .where(filter=FieldFilter("case_id", "==", case_id))
                    .where(filter=FieldFilter("type", "==", "report"))
                    .limit(1))
    assignee_check = _assignee_only(user)

    def check(tx, case):
        assignee_check(tx, case)
        reports = list(report_query.stream(transaction=tx))
        if not reports:
            raise ReviewError("요약 리포트를 먼저 작성하세요.")
        return {}, {"report_id": reports[0].id}

    return _change(db, user, case_id, DONE, now=now, check=check)


def request_rereview(user: dict | None, case_id: str, reason: str, *, db, now: datetime | None = None) -> dict:
    """T9 재검토 지시. 관리자만, 사유 필수. 담당 판독의는 그대로 둔다."""
    require_role(user, MANAGER)
    reason = _reason(reason)
    return _change(db, user, case_id, IN_REVIEW, now=now, reason=reason)


# ---------------------------------------------------------------- 조회 (화면용)
def visible_cases(user: dict | None, *, db, limit: int = 200) -> list[dict]:
    """로그인 사용자가 볼 수 있는 케이스 목록 (workflow.md 3장). 판독의는 본인 배정 건만.

    최근 변경 순으로 정렬한다. 각 항목은 케이스 문서에 "case_id" 를 더한 dict.
    """
    require_role(user)
    from google.cloud.firestore_v1.base_query import FieldFilter

    query = db.collection("cases")
    if user["role"] == READER:
        query = query.where(filter=FieldFilter("assignee_uid", "==", user["uid"]))
    cases = [dict(snap.to_dict() or {}, case_id=snap.id) for snap in query.limit(limit).stream()]
    # 정렬은 여기서 한다 (Firestore 에서 정렬하면 assignee_uid 와 묶인 복합 인덱스가 필요하다)
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(cases, key=lambda c: c.get("updated_at") or epoch, reverse=True)


def list_readers(user: dict | None, *, db) -> list[dict]:
    """배정 화면의 판독의 목록 [{"uid", "email"}]. 관리자만."""
    require_role(user, MANAGER)
    from google.cloud.firestore_v1.base_query import FieldFilter

    query = db.collection("users").where(filter=FieldFilter("role", "==", READER))
    readers = [{"uid": s.id, "email": (s.to_dict() or {}).get("email", s.id)} for s in query.stream()]
    return sorted(readers, key=lambda r: r["email"])


def available_actions(user: dict | None, case: dict) -> list[str]:
    """이 사용자가 이 케이스에서 누를 수 있는 버튼 (ACTIONS 의 값). 화면에서 버튼을 숨기는 용도.

    버튼을 숨겨도 저장 함수가 다시 검사하므로, 여기는 보기 좋게 하는 역할만 한다.
    """
    if not user or not user.get("role"):
        return []
    status = case.get("status")
    actions = []
    for nxt in next_statuses(status, user["role"]):
        action = ACTIONS.get((status, nxt))
        if action is None:
            continue
        if user["role"] == READER and case.get("assignee_uid") != user.get("uid"):
            continue  # 판독의는 본인에게 배정된 건만
        actions.append(action)
    return actions


# ---------------------------------------------------------------- 내부
def _reason(reason: str | None) -> str:
    reason = (reason or "").strip()
    if not reason:
        raise ReviewError("사유를 입력하세요.")
    if len(reason) > REASON_MAX:
        raise ReviewError(f"사유는 {REASON_MAX}자 이내로 입력하세요.")
    return reason


def _assignee_only(user: dict):
    def check(tx, case):
        if case.get("assignee_uid") != user["uid"]:
            raise PermissionDenied("본인에게 배정된 케이스가 아닙니다.")
        return {}, None
    return check


def _change(db, user: dict, case_id: str, nxt: str, *, now: datetime | None,
            reason: str | None = None, check=None) -> dict:
    """트랜잭션 안에서 현재 상태를 다시 읽고, 규칙·조건을 확인한 뒤 상태와 이력을 함께 저장한다.

    check(tx, case) → (케이스에 더 바꿀 필드, 이력 extra). 조건이 안 맞으면 예외를 던진다.
    반환: {"case_id", "from_status", "status"}
    """
    case_ref = db.collection("cases").document(case_id)
    history_ref = db.collection("case_history").document()  # 자동 ID

    def work(tx):
        snap = case_ref.get(transaction=tx)
        if not snap.exists:
            raise ReviewError("케이스를 찾을 수 없습니다.")
        case = snap.to_dict() or {}
        current = case.get("status")
        require_transition(user, current, nxt)
        updates, extra = check(tx, case) if check else ({}, None)
        at = now or datetime.now(timezone.utc)
        tx.update(case_ref, {"status": nxt, "updated_at": at, **updates})
        tx.set(history_ref, {
            "case_id": case_id,
            "from_status": current,
            "to_status": nxt,
            "reason": reason,
            "extra": extra,
            "actor_uid": user["uid"],
            "at": at,
        })
        return {"case_id": case_id, "from_status": current, "status": nxt}

    return _run_transaction(db, work)


def _run_transaction(db, work):
    """Firestore 트랜잭션으로 work(tx) 실행. 다른 요청과 겹치면 Firestore 가 처음부터 다시 실행한다.

    테스트에서는 가짜 DB용으로 이 함수를 바꿔 끼운다.
    """
    from google.cloud.firestore import transactional

    return transactional(work)(db.transaction())
