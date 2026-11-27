"""메모·리포트 및 본인 메모 수정 이력. 상태 변경은 A의 review 서비스가 담당한다."""
from datetime import datetime, timezone

from core.workflow import DONE, MANAGER, READER, PermissionDenied, require_role
from services import review
from services.access import editable_case, visible_case

MAX_CONTENT = 10000


class NoteError(ValueError):
    pass


def list_notes(user, case_id, *, db):
    from google.cloud.firestore_v1.base_query import FieldFilter
    visible_case(user, case_id, db=db)
    rows = [dict(s.to_dict() or {}, note_id=s.id) for s in db.collection("notes")
            .where(filter=FieldFilter("case_id", "==", case_id)).stream()]
    return sorted(rows, key=lambda n: n.get("created_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def list_case_history(user, case_id, *, db):
    from google.cloud.firestore_v1.base_query import FieldFilter
    visible_case(user, case_id, db=db)
    rows = [dict(s.to_dict() or {}, history_id=s.id) for s in db.collection("case_history")
            .where(filter=FieldFilter("case_id", "==", case_id)).stream()]
    return sorted(rows, key=lambda n: n["at"], reverse=True)


def save_note(user, case_id, content, kind="memo", *, db, note_id=None, expected_content=None):
    require_role(user, READER, MANAGER)
    content = content.strip()
    if not content or len(content) > MAX_CONTENT:
        raise NoteError(f"내용은 1~{MAX_CONTENT}자로 입력하세요.")
    if kind not in {"memo", "report"}:
        raise NoteError("메모 또는 리포트만 저장할 수 있습니다.")
    if user["role"] == MANAGER and kind == "report":
        raise PermissionDenied("리포트는 담당 판독의만 작성할 수 있습니다.")
    ref = db.collection("notes").document(note_id) if note_id else db.collection("notes").document()
    history_ref = db.collection("case_history").document()
    now = datetime.now(timezone.utc)

    def commit(tx):
        case = visible_case(user, case_id, db=db, transaction=tx)
        before = None
        if note_id:
            old = ref.get(transaction=tx)
            if not old.exists:
                raise NoteError("메모를 찾을 수 없습니다.")
            before = old.to_dict() or {}
            if before.get("case_id") != case_id or before.get("author_uid") != user["uid"]:
                raise PermissionDenied("본인이 작성한 이 케이스의 메모만 수정할 수 있습니다.")
            if expected_content is None or before["content"] != expected_content:
                raise NoteError("메모가 변경되었습니다. 새로고침 후 다시 수정하세요.")
            if before["type"] != kind:
                raise NoteError("수정할 때 메모 종류는 변경할 수 없습니다.")
            if user["role"] == READER and case["status"] == DONE:
                raise PermissionDenied("완료된 케이스의 메모는 수정할 수 없습니다.")
        elif user["role"] == READER:
            editable_case(user, case_id, db=db, transaction=tx)
        edits = list(before.get("edits", [])) if before else []
        if before:
            edits.append({"before": before["content"], "at": now, "by": user["uid"]})
        doc = {"case_id": case_id, "type": kind, "content": content, "edits": edits,
               "author_uid": user["uid"], "created_at": before["created_at"] if before else now,
               "updated_at": now, "image_id": None, "log_id": None, "slice_index": None}
        tx.set(ref, doc)
        tx.set(history_ref, {"case_id": case_id, "from_status": case["status"],
                            "to_status": case["status"], "reason": "메모 수정" if before else "메모 작성",
                            "extra": {"note_id": ref.id, "type": kind}, "actor_uid": user["uid"], "at": now})
        return dict(doc, note_id=ref.id)

    return review._run_transaction(db, commit)
