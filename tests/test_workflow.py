"""상태 전이표와 권한 검사 함수 테스트 (docs/workflow.md 2·3장)."""
import pytest

from core.workflow import (
    ASSIGNED,
    DONE,
    IN_REVIEW,
    MANAGER,
    QC_FAILED,
    RADIOGRAPHER,
    READER,
    RECEIVED,
    ROLES,
    STATUSES,
    TRANSITIONS,
    PermissionDenied,
    can_transition,
    next_statuses,
    require_role,
    require_transition,
)


def user(role):
    return {"uid": f"u-{role}", "email": f"{role}@test.com", "role": role}


# --- 전이표 자체 ---
def test_transitions_use_known_statuses():
    for current, nxt in TRANSITIONS:
        assert current in STATUSES, current
        assert nxt in STATUSES, nxt


def test_transitions_use_known_roles():
    for key, roles in TRANSITIONS.items():
        assert roles, f"{key}: 허용 역할이 비어 있음"
        assert roles <= set(ROLES), f"{key}: 모르는 역할 {roles - set(ROLES)}"


# --- can_transition: 문서 T3~T9 ---
@pytest.mark.parametrize(
    "current, nxt, role",
    [
        (QC_FAILED, RECEIVED, RADIOGRAPHER),  # T3
        (RECEIVED, ASSIGNED, MANAGER),        # T4
        (ASSIGNED, ASSIGNED, MANAGER),        # T5
        (ASSIGNED, IN_REVIEW, READER),        # T6
        (IN_REVIEW, QC_FAILED, READER),       # T7
        (IN_REVIEW, DONE, READER),            # T8
        (DONE, IN_REVIEW, MANAGER),           # T9
    ],
)
def test_allowed(current, nxt, role):
    assert can_transition(current, nxt, role)


@pytest.mark.parametrize(
    "current, nxt, role",
    [
        (RECEIVED, DONE, MANAGER),        # 문서 예시: 접수 → 완료 거절
        (DONE, ASSIGNED, MANAGER),        # 문서 예시: 완료 → 배정 거절
        (RECEIVED, ASSIGNED, READER),     # 판독의는 배정 불가
        (IN_REVIEW, DONE, MANAGER),       # 관리자는 검토 완료 불가
        (DONE, IN_REVIEW, READER),        # 판독의는 재검토 지시 불가
        (RECEIVED, QC_FAILED, READER),    # 검토 중이 아닐 때 재촬영 요청 불가
        (ASSIGNED, IN_REVIEW, "hacker"),  # 모르는 역할
        ("없는상태", DONE, READER),        # 모르는 상태
    ],
)
def test_denied(current, nxt, role):
    assert not can_transition(current, nxt, role)


def test_next_statuses():
    assert next_statuses(RECEIVED, MANAGER) == [ASSIGNED]
    assert set(next_statuses(IN_REVIEW, READER)) == {QC_FAILED, DONE}
    assert next_statuses(DONE, MANAGER) == [IN_REVIEW]
    assert next_statuses(DONE, READER) == []


# --- require_role ---
@pytest.mark.parametrize("u", [None, {}, {"uid": "x"}])
def test_require_role_needs_login(u):
    with pytest.raises(PermissionDenied, match="로그인"):
        require_role(u)


def test_require_role_login_only():
    for role in ROLES:
        require_role(user(role))  # 예외 없음


def test_require_role_allowed():
    require_role(user(MANAGER), MANAGER, READER)


def test_require_role_denied_message():
    with pytest.raises(PermissionDenied, match="권한이 없습니다"):
        require_role(user(RADIOGRAPHER), MANAGER)


def test_require_role_unknown_role():
    with pytest.raises(PermissionDenied, match="알 수 없는 역할"):
        require_role(user("admin"))


# --- require_transition ---
def test_require_transition_ok():
    require_transition(user(READER), ASSIGNED, IN_REVIEW)


def test_require_transition_denied_message():
    with pytest.raises(PermissionDenied, match=r"현재 상태\(접수\)에서는 완료"):
        require_transition(user(MANAGER), RECEIVED, DONE)


def test_require_transition_needs_login():
    with pytest.raises(PermissionDenied, match="로그인"):
        require_transition(None, RECEIVED, ASSIGNED)
