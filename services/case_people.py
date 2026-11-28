"""조회 권한이 있는 케이스의 참여자 이름·이메일만 표시용으로 조회한다."""
from services.access import visible_case

UNKNOWN_USER = "알 수 없는 사용자"


def case_user_labels(user, case_id, *, db):
    from google.cloud.firestore_v1.base_query import FieldFilter

    case = visible_case(user, case_id, db=db)
    uids = {user["uid"], case.get("assignee_uid"), case.get("created_by")}
    # 임의의 UID나 전체 users 목록을 받지 않고, 이 케이스에 실제로 기록된 참여자만 읽는다.
    for collection, field in (("notes", "author_uid"), ("case_history", "actor_uid"),
                              ("detection_reviews", "reviewed_by"), ("processing_logs", "run_by")):
        query = db.collection(collection).where(filter=FieldFilter("case_id", "==", case_id))
        for snap in query.stream():
            row = snap.to_dict() or {}
            uids.add(row.get(field))
            for edit in row.get("edits", []):
                uids.add(edit.get("by") or edit.get("reviewed_by"))
    labels = {}
    for uid in sorted(u for u in uids if u):
        snap = db.collection("users").document(uid).get()
        profile = (snap.to_dict() or {}) if snap.exists else {}
        label = profile.get("display_name") or profile.get("name") or profile.get("email")
        if not label and uid == user["uid"]:
            label = user.get("email")
        labels[uid] = label or UNKNOWN_USER
    return labels
