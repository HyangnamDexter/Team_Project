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

# 상태 전이표: (현재 상태, 다음 상태) -> 허용 역할. docs/workflow.md 2장의 T3~T9 와 같게 유지한다.
# T1·T2(처음 등록)는 상태가 없던 케이스를 만드는 것이라 이 표에 넣지 않는다.
# 담당자 본인 여부, 사유·리포트 필수 같은 조건은 여기서 보지 않고 상태를 바꾸는 함수에서 검사한다.
TRANSITIONS: dict[tuple[str, str], set[str]] = {
    (QC_FAILED, RECEIVED): {RADIOGRAPHER},  # T3 재등록 후 점검 통과
    (RECEIVED, ASSIGNED): {MANAGER},        # T4 판독의 지정
    (ASSIGNED, ASSIGNED): {MANAGER},        # T5 재배정
    (ASSIGNED, IN_REVIEW): {READER},        # T6 검토 시작 (담당자만)
    (IN_REVIEW, QC_FAILED): {READER},       # T7 재촬영 요청 (담당자, 사유 필수)
    (IN_REVIEW, DONE): {READER},            # T8 검토 완료 (담당자, 리포트 필수)
    (DONE, IN_REVIEW): {MANAGER},           # T9 재검토 지시 (사유 필수)
}


class PermissionDenied(Exception):
    """권한 없는 요청. 메시지는 화면에 그대로 보여 줄 수 있는 문장이다."""


def can_transition(current: str, nxt: str, role: str) -> bool:
    """role 이 current → nxt 상태 변경을 할 수 있으면 True.

    전이표에 없는 변경, 모르는 상태·역할은 모두 False.
    """
    return role in TRANSITIONS.get((current, nxt), set())


def next_statuses(current: str, role: str) -> list[str]:
    """role 이 current 상태에서 바꿀 수 있는 다음 상태 목록 (화면의 버튼·선택지용)."""
    return [nxt for (cur, nxt), roles in TRANSITIONS.items() if cur == current and role in roles]


def require_role(user: dict | None, *allowed: str) -> None:
    """로그인하지 않았거나 허용 역할이 아니면 PermissionDenied.

    allowed 를 비워 두면 로그인 여부만 검사한다.
    """
    if not user or not user.get("role"):
        raise PermissionDenied("로그인이 필요합니다.")
    role = user["role"]
    if role not in ROLES:
        raise PermissionDenied("권한이 없습니다. (알 수 없는 역할)")
    if allowed and role not in allowed:
        need = ", ".join(ROLE_LABELS.get(r, r) for r in allowed)
        raise PermissionDenied(f"권한이 없습니다. ({ROLE_LABELS[role]} → 필요한 역할: {need})")


def require_transition(user: dict | None, current: str, nxt: str) -> None:
    """로그인 + 상태 전이 권한을 함께 검사한다. 상태를 바꾸는 함수의 맨 앞에서 호출.

    담당자 본인 여부, 사유·리포트 필수 같은 조건은 호출하는 쪽에서 따로 검사한다.
    """
    require_role(user)
    if not can_transition(current, nxt, user["role"]):
        raise PermissionDenied(f"현재 상태({current})에서는 {nxt}(으)로 바꿀 수 없습니다.")
