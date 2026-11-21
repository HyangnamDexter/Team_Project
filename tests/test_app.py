"""app.py 메뉴·로그인 화면 테스트 (Streamlit AppTest, Firebase 접속 없음)."""
from pathlib import Path

import pytest

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
APP = str(Path(__file__).resolve().parent.parent / "app.py")
USER = {"uid": "uid-001", "email": "user001@test.com", "role": "manager", "id_token": "t"}


def run(user=None, page=None):
    # WSL 파일 로딩과 최초 Streamlit import 시간을 허용한다.
    at = AppTest.from_file(APP, default_timeout=30)
    if user is not None:
        at.session_state["user"] = user
    if page:
        at.switch_page(page)
    return at.run()


def test_logged_out_shows_only_login_form():
    at = run()
    assert not at.exception
    assert [t.label for t in at.text_input] == ["이메일", "비밀번호"]
    assert not any(b.label == "로그아웃" for b in at.sidebar.button)


@pytest.mark.parametrize("page", ["pages/0_Home.py", "pages/1_Cases.py", "pages/2_Viewer.py", "pages/3_Review.py"])
def test_logged_out_page_shows_login_required(page):
    # 로그인 전에도 페이지는 숨김 등록되어 있어서, 주소로 들어오면 각 페이지가 안내를 보여 준다
    at = run()
    at.switch_page(page).run()
    assert not at.exception
    assert any("로그인이 필요" in w.value for w in at.warning)
    assert not at.title or at.title[0].value != "영상 뷰어"  # 페이지 내용은 보이지 않음


def test_logged_in_shows_home_and_logout():
    at = run(USER)
    assert not at.exception
    assert "user001@test.com" in at.success[0].value
    assert "관리자" in at.success[0].value
    assert any(b.label == "로그아웃" for b in at.sidebar.button)
    assert len(at.text_input) == 0  # 로그인 폼 없음


@pytest.mark.parametrize(
    "page, title",
    [("pages/1_Cases.py", "케이스 목록"), ("pages/2_Viewer.py", "영상 뷰어"), ("pages/3_Review.py", "검토")],
)
def test_menu_pages_open_when_logged_in(page, title):
    at = run(USER, page)
    assert not at.exception
    assert at.title[0].value == title
