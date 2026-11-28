"""등록 화면과 A 서비스 연결·목록 권한을 실제 Streamlit 실행으로 검증한다."""
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from services import cases, db
from test_cases import FakeDB as RegistrationDB, dicom_bytes
from test_review import FakeDB as QueryDB

APP = str(Path(__file__).resolve().parents[1] / "app.py")
TECH = {"uid": "tech", "email": "tech@test.com", "role": "radiographer"}


class Upload(BytesIO):
    name = "test.dicom"

    @property
    def size(self):
        return len(self.getvalue())


@pytest.fixture
def page(monkeypatch, tmp_path):
    # AppTest는 file_uploader 조작을 지원하지 않아 파일 선택만 대체한다.
    selected = {"file": None}
    monkeypatch.setattr(st, "file_uploader", lambda *a, **kw: selected["file"])
    database = QueryDB()
    monkeypatch.setattr(db, "get_db", lambda: database)
    app = AppTest.from_file(APP, default_timeout=60)
    app.secrets["storage"] = {"image_dir": str(tmp_path)}
    # 컴퓨터에 models/best.onnx 가 있어도 결과가 같도록 없는 모델 경로를 지정한다 (모델 없음 = no_model).
    app.secrets["model"] = {"path": str(tmp_path / "no_model.onnx")}
    app.session_state["user"] = TECH
    app.switch_page("pages/1_Cases.py")
    return app, selected, database


def register_button(app):
    return app.button(key="cases_register")


def test_no_upload_disables_registration(page):
    app, _, _ = page
    app.run()
    assert not app.exception
    assert register_button(app).disabled


def test_missing_modality_requires_explicit_choice_and_calls_A(page, monkeypatch, tmp_path):
    app, selected, _ = page
    data = dicom_bytes(Modality="")
    selected["file"] = Upload(data)
    real_db = RegistrationDB()
    real_register = cases.register_case
    calls = []

    def register(user, content, **kwargs):
        calls.append((user, content, kwargs))
        return real_register(user, content, **dict(kwargs, db=real_db))

    monkeypatch.setattr(cases, "register_case", register)
    app.run()
    assert register_button(app).disabled
    next(s for s in app.selectbox if s.label == "촬영 종류").select("CR").run()
    assert not register_button(app).disabled
    register_button(app).click().run()
    assert not app.exception
    assert len(calls) == 1 and calls[0][0] == TECH and calls[0][1] == data
    assert calls[0][2]["modality"] == "CR"
    assert calls[0][2]["storage_root"] == tmp_path
    assert "detector" in calls[0][2]  # 등록할 때 자동 탐지 연결 (모델 파일이 없으면 None)
    assert real_db.commits == 1
    assert next(iter(real_db.data["detections"].values()))["status"] == "no_model"
    assert any("등록됨 (접수)" in msg.value for msg in app.success)
    assert register_button(app).disabled
    app.run()  # 위젯 재실행이 저장을 반복하지 않는다.
    assert len(calls) == 1


def test_qc_failure_is_registered_and_shows_reason(page, monkeypatch):
    app, selected, _ = page
    selected["file"] = Upload(dicom_bytes())
    save = Mock(return_value={"case_id": "CASE-FAIL", "status": "재촬영 요청",
                              "qc": {"issues": ["대비 부족"]}})
    monkeypatch.setattr(cases, "register_case", save)
    app.run()
    register_button(app).click().run()
    assert not app.exception
    assert any("CASE-FAIL 등록됨 — 재촬영 요청: 대비 부족" in w.value for w in app.warning)
    assert save.call_args.kwargs["modality"] is None  # 헤더가 있으면 A가 파일 값을 사용


@pytest.mark.parametrize("data", [b"not dicom", dicom_bytes(Modality="CT"), dicom_bytes(Modality="MR")])
def test_invalid_or_non_xray_never_registers(page, monkeypatch, data):
    app, selected, _ = page
    selected["file"] = Upload(data)
    save = Mock()
    monkeypatch.setattr(cases, "register_case", save)
    app.run()
    assert not app.exception and app.error
    assert register_button(app).disabled
    save.assert_not_called()


def test_registration_failure_allows_retry_without_exposing_exception(page, monkeypatch):
    app, selected, _ = page
    selected["file"] = Upload(dicom_bytes())
    save = Mock(side_effect=RuntimeError("private credential path"))
    monkeypatch.setattr(cases, "register_case", save)
    app.run()
    register_button(app).click().run()
    assert not app.exception
    assert any("등록 결과를 확인하지 못했습니다" in e.value for e in app.error)
    assert all("private credential" not in e.value for e in app.error)
    assert not register_button(app).disabled


def test_search_status_and_modality_filters_combine(page):
    app, _, database = page
    database.add_case("CASE-ONE", "접수", modality="DX", study_date="20261126", body_part="SPINE")
    database.add_case("CASE-TWO", "재촬영 요청", modality="CR", study_date="20261125")
    app.run()
    assert len(app.dataframe[0].value) == 2
    app.text_input(key="cases_search").set_value("20261126").run()
    assert app.dataframe[0].value["케이스 ID"].tolist() == ["CASE-ONE"]
    app.selectbox(key="cases_status").select("재촬영 요청").run()
    assert not app.dataframe
    app.text_input(key="cases_search").set_value("")
    app.selectbox(key="cases_modality_filter").select("CR").run()
    assert app.dataframe[0].value["케이스 ID"].tolist() == ["CASE-TWO"]
    database.add_case("CASE-THREE", "재촬영 요청", modality="CR")
    app.button(key="cases_refresh").click().run()
    assert len(app.dataframe[0].value) == 2


def test_reader_only_sees_assigned_cases_and_cannot_upload(page):
    app, _, database = page
    database.add_case("CASE-MINE", "배정", assignee="reader")
    database.add_case("CASE-OTHER", "배정", assignee="someone")
    app.session_state["user"] = {"uid": "reader", "email": "reader@test.com", "role": "reader"}
    app.run()
    assert not app.exception
    assert not any(b.key == "cases_register" for b in app.button)
    assert app.dataframe[0].value["케이스 ID"].tolist() == ["CASE-MINE"]


def test_pending_registration_disables_button_before_service_call(page, monkeypatch):
    app, selected, _ = page
    selected["file"] = Upload(dicom_bytes())
    states = []
    disabled = []
    original_button = st.button

    def button(*args, **kwargs):
        if kwargs.get("key") == "cases_register":
            disabled.append(kwargs.get("disabled", False))
        return original_button(*args, **kwargs)

    monkeypatch.setattr(st, "button", button)

    def save(*args, **kwargs):
        states.append((bool(st.session_state.cases_pending), disabled[-1]))
        return {"case_id": "CASE-ONE", "status": "접수", "qc": {"issues": []}}

    monkeypatch.setattr(cases, "register_case", save)
    app.run()
    register_button(app).click().run()
    assert states == [(True, True)]
    app.run()
    assert register_button(app).disabled and states == [(True, True)]


def test_late_duplicate_callback_is_ignored(monkeypatch):
    from hashlib import sha256
    from ui.case_registration import queue_registration

    data = b"registered file"
    token = (TECH["uid"], sha256(data).hexdigest())
    state = {"user": TECH, "cases_completed": {token: {"case_id": "CASE-ONE"}}}
    monkeypatch.setattr(st, "session_state", state)
    queue_registration(data, "DX")
    assert "cases_pending" not in state


def test_previous_file_error_hidden_for_new_or_completed_file(page, monkeypatch):
    app, selected, _ = page
    completed_file, failed_file, new_file = (dicom_bytes() for _ in range(3))
    selected["file"] = Upload(completed_file)
    save = Mock(side_effect=[
        {"case_id": "CASE-SAVED", "status": "접수", "qc": {"issues": []}},
        cases.RegistrationError("테스트 등록 실패"),
    ])
    monkeypatch.setattr(cases, "register_case", save)
    app.run()
    register_button(app).click().run()
    selected["file"] = Upload(failed_file)
    app.run()
    register_button(app).click().run()
    assert any("테스트 등록 실패" in e.value for e in app.error)

    selected["file"] = Upload(new_file)
    app.run()
    assert not app.exception and not app.error
    selected["file"] = Upload(completed_file)
    app.run()
    assert not app.error and register_button(app).disabled
    assert any("CASE-SAVED 등록됨" in s.value for s in app.success)

    selected["file"] = Upload(failed_file)
    app.run()
    assert any("테스트 등록 실패" in e.value for e in app.error)
    selected["file"] = None
    app.run()
    assert not app.error
    assert save.call_count == 2


@pytest.mark.parametrize("detection,message,warning", [
    ({"status": "done", "n_boxes": 2}, "임플란트 의심 박스 2개", False),
    ({"status": "done", "n_boxes": 0}, "임플란트 의심 박스 없음", False),
    ({"status": "no_model", "n_boxes": 0}, "탐지 모델이 없어 탐지하지 않았습니다", True),
    ({"status": "failed", "n_boxes": 0}, "자동 탐지에 실패했습니다", True),
])
def test_registration_displays_each_detection_outcome_without_repeating_registration(page, monkeypatch, detection, message, warning):
    app, selected, _ = page
    selected["file"] = Upload(dicom_bytes())
    save = Mock(return_value={"case_id": "CASE-ONE", "status": "접수", "qc": {},
                              "detection": {**detection, "error": "private server detail"}})
    monkeypatch.setattr(cases, "register_case", save)
    app.run()
    register_button(app).click().run()
    assert not app.exception
    messages = app.warning if warning else app.info
    assert any(message in item.value for item in messages)
    assert all("private server detail" not in item.value for item in [*app.warning, *app.info])
    app.run()
    assert save.call_count == 1 and register_button(app).disabled


def test_registration_rejects_files_above_50mb_before_reading_content(page, monkeypatch):
    from core.settings import MAX_UPLOAD_BYTES

    class Oversized(Upload):
        @property
        def size(self):
            return MAX_UPLOAD_BYTES + 1

        def getvalue(self):
            pytest.fail("크기 제한을 넘긴 파일의 내용을 읽으면 안 됨")

    app, selected, _ = page
    selected["file"] = Oversized(b"unused")
    save = Mock()
    monkeypatch.setattr(cases, "register_case", save)
    app.run()
    assert not app.exception
    assert any("50MB 이하" in error.value for error in app.error)
    assert register_button(app).disabled
    save.assert_not_called()
