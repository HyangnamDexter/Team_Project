"""영상 조회 권한 확인 (여러 서비스가 같이 쓴다). 담당: A — docs/workflow.md 3장 '영상 보기'

- 로그인 필수
- 판독의는 본인에게 배정된 케이스의 영상만
- 방사선사·관리자는 모든 영상
"""
from __future__ import annotations

from core.workflow import READER, PermissionDenied, require_role


class ImageNotFound(LookupError):
    """영상 문서가 없음. 메시지는 화면에 그대로 보여 줄 수 있는 문장이다."""


def visible_image(user: dict | None, image_id: str, *, db) -> dict:
    """images/{image_id} 문서를 권한 확인 후 돌려준다. 권한이 없으면 PermissionDenied, 없으면 ImageNotFound."""
    require_role(user)
    snap = db.collection("images").document(image_id).get()
    if not snap.exists:
        raise ImageNotFound("영상을 찾을 수 없습니다.")
    image = snap.to_dict() or {}
    if user["role"] == READER:
        case_snap = db.collection("cases").document(image.get("case_id") or "-").get()
        if not case_snap.exists or (case_snap.to_dict() or {}).get("assignee_uid") != user["uid"]:
            raise PermissionDenied("본인에게 배정된 케이스가 아닙니다.")
    return image
