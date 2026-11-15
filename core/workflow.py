"""업무 상태 전이와 역할별 권한 규칙. 담당: A

화면 표시와 별개로, 데이터를 바꾸는 모든 경로에서 can_transition / require_role 로 검사한다.
"""
from __future__ import annotations

# 역할
RADIOGRAPHER = "radiographer"  # 방사선사: 케이스·영상 등록
READER = "reader"              # 판독의: 처리·비교·메모
MANAGER = "manager"            # 관리자: 배정·진행 현황
ROLES = (RADIOGRAPHER, READER, MANAGER)
ROLE_LABELS = {RADIOGRAPHER: "방사선사", READER: "판독의", MANAGER: "관리자"}

# 상태
RECEIVED = "접수"
QC_FAILED = "재촬영 요청"
ASSIGNED = "배정"
IN_REVIEW = "검토 중"
DONE = "완료"
STATUSES = (RECEIVED, QC_FAILED, ASSIGNED, IN_REVIEW, DONE)

# TODO(A): 상태 전이표 확정 (현재 상태, 다음 상태) -> 허용 역할
TRANSITIONS: dict[tuple[str, str], set[str]] = {}


class PermissionDenied(Exception):
    """권한 없는 요청."""


def can_transition(current: str, nxt: str, role: str) -> bool:
    raise NotImplementedError  # TODO(A)


def require_role(user: dict | None, *allowed: str) -> None:
    """로그인하지 않았거나 허용 역할이 아니면 PermissionDenied."""
    raise NotImplementedError  # TODO(A)
