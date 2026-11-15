"""services/auth.py 로그인 테스트 (FR-01, FR-02).

실제 Firebase에 접속하지 않도록 HTTP 요청과 역할 조회를 가짜로 바꿔서 확인한다.
"""
import pytest
import requests

from services import auth


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


OK_PAYLOAD = {"localId": "uid-002", "email": "user002@test.com", "idToken": "token-abc"}


@pytest.fixture
def fake_firebase(monkeypatch):
    """요청을 기록하고, 응답과 역할을 테스트마다 바꿀 수 있게 한다."""
    state = {"response": FakeResponse(200, OK_PAYLOAD), "role": "reader", "calls": []}

    def fake_post(url, params=None, json=None, timeout=None):
        state["calls"].append({"url": url, "params": params, "json": json, "timeout": timeout})
        if isinstance(state["response"], Exception):
            raise state["response"]
        return state["response"]

    monkeypatch.setattr(auth.requests, "post", fake_post)
    monkeypatch.setattr(auth, "_api_key", lambda: "test-key")
    monkeypatch.setattr(auth, "_fetch_role", lambda uid: state["role"])
    return state


def error(code):
    return FakeResponse(400, {"error": {"message": code}})


def test_success_returns_user_with_role(fake_firebase):
    user = auth.sign_in("  user002@test.com ", "pw")
    assert user == {"uid": "uid-002", "email": "user002@test.com", "role": "reader", "id_token": "token-abc"}
    call = fake_firebase["calls"][0]
    assert call["json"]["email"] == "user002@test.com"  # 앞뒤 공백 제거
    assert call["params"] == {"key": "test-key"}
    assert call["timeout"] == auth.TIMEOUT_SEC


@pytest.mark.parametrize("email,pw", [("", "pw"), ("user@test.com", ""), ("   ", "pw"), (None, None)])
def test_empty_input_rejected_without_request(fake_firebase, email, pw):
    with pytest.raises(auth.AuthError, match=auth.MSG_EMPTY):
        auth.sign_in(email, pw)
    assert fake_firebase["calls"] == []


@pytest.mark.parametrize("code", ["INVALID_LOGIN_CREDENTIALS", "EMAIL_NOT_FOUND", "INVALID_PASSWORD", "INVALID_EMAIL"])
def test_wrong_credentials_use_one_message(fake_firebase, code):
    fake_firebase["response"] = error(code)
    with pytest.raises(auth.AuthError) as exc:
        auth.sign_in("user002@test.com", "wrong")
    assert str(exc.value) == auth.MSG_BAD_CREDENTIALS


def test_too_many_attempts(fake_firebase):
    fake_firebase["response"] = error("TOO_MANY_ATTEMPTS_TRY_LATER : Access disabled")
    with pytest.raises(auth.AuthError, match=auth.MSG_TOO_MANY):
        auth.sign_in("user002@test.com", "pw")


def test_disabled_user(fake_firebase):
    fake_firebase["response"] = error("USER_DISABLED")
    with pytest.raises(auth.AuthError, match=auth.MSG_DISABLED):
        auth.sign_in("user002@test.com", "pw")


def test_non_json_error_body(fake_firebase):
    fake_firebase["response"] = FakeResponse(500, None)
    with pytest.raises(auth.AuthError, match=auth.MSG_BAD_CREDENTIALS):
        auth.sign_in("user002@test.com", "pw")


def test_network_error(fake_firebase):
    fake_firebase["response"] = requests.ConnectionError("down")
    with pytest.raises(auth.AuthError, match=auth.MSG_NETWORK):
        auth.sign_in("user002@test.com", "pw")


@pytest.mark.parametrize("role", [None, "", "reder", "admin"])
def test_missing_or_unknown_role_rejected(fake_firebase, role):
    fake_firebase["role"] = role
    with pytest.raises(auth.AuthError, match=auth.MSG_NO_ROLE):
        auth.sign_in("user002@test.com", "pw")


@pytest.mark.parametrize("role", ["radiographer", "reader", "manager"])
def test_all_roles_accepted(fake_firebase, role):
    fake_firebase["role"] = role
    assert auth.sign_in("user002@test.com", "pw")["role"] == role
