"""영상 조회 권한 확인 (여러 서비스가 같이 쓴다). 담당: A — docs/workflow.md 3장 '영상 보기'

- 로그인 필수
- 판독의는 본인에게 배정된 케이스의 영상만
- 방사선사·관리자는 모든 영상
"""
from __future__ import annotations

from pathlib import Path

from core.workflow import READER, IN_REVIEW, PermissionDenied, require_role


class ImageNotFound(LookupError):
    """영상 문서가 없음. 메시지는 화면에 그대로 보여 줄 수 있는 문장이다."""


def visible_image(user: dict | None, image_id: str, *, db, transaction=None) -> dict:
    """images/{image_id} 문서를 권한 확인 후 돌려준다. 권한이 없으면 PermissionDenied, 없으면 ImageNotFound."""
    require_role(user)
    snap = db.collection("images").document(image_id).get(transaction=transaction)
    if not snap.exists:
        raise ImageNotFound("영상을 찾을 수 없습니다.")
    image = snap.to_dict() or {}
    visible_case(user, image.get("case_id") or "-", db=db, transaction=transaction)
    return image


def visible_case(user, case_id, *, db, transaction=None):
    require_role(user)
    snap = db.collection("cases").document(case_id).get(transaction=transaction)
    if not snap.exists:
        raise ImageNotFound("케이스를 찾을 수 없습니다.")
    case = snap.to_dict() or {}
    if user["role"] == READER and case.get("assignee_uid") != user["uid"]:
        raise PermissionDenied("본인에게 배정된 케이스가 아닙니다.")
    return case


def editable_case(user, case_id, *, db, transaction=None):
    require_role(user, READER)
    case = visible_case(user, case_id, db=db, transaction=transaction)
    if case.get("status") != IN_REVIEW:
        raise PermissionDenied("검토 중인 본인 배정 케이스에서만 실행할 수 있습니다.")
    return case


def case_images(user, case_id, *, db):
    from google.cloud.firestore_v1.base_query import FieldFilter
    visible_case(user, case_id, db=db)
    rows = [dict(s.to_dict() or {}, image_id=s.id)
            for s in db.collection("images").where(filter=FieldFilter("case_id", "==", case_id)).stream()]
    return sorted(rows, key=lambda r: (not r.get("is_current", True), r["image_id"]))


def storage_path(root, relative):
    """DB 상대 경로도 검사하여 저장 영역 밖으로의 읽기·쓰기를 거절한다."""
    root = Path(root).resolve()
    path = Path(relative)
    target = (root / path).resolve()
    if path.is_absolute() or target == root or root not in target.parents:
        raise ValueError("저장 영역 밖의 경로는 사용할 수 없습니다.")
    return target


def original_path(image, storage_root):
    files = image.get("slice_files") or ["0001.dcm"]
    if len(files) != 1:
        raise ValueError("X-ray 단일 영상만 지원합니다.")
    return storage_path(storage_root, str(Path(image["original_dir"]) / files[0]))
