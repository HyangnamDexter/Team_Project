"""등록 영상에서 박스·ROI·이력·리포트·오검출 검토까지 화면 흐름을 검증한다."""
from hashlib import sha256
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core.dicom_io import load_dicom
from services import db as db_service
from ui import dicom_canvas
from test_review import READER1, TECH
from test_workbench import workbench, FakeDetector

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def screen(workbench, monkeypatch):
    database, root = workbench
    uid = load_dicom(root / "C1/I1/original/0001.dcm")[1]["sop_instance_uid"]
    database.data["images"]["I1"]["sop_instance_uid"] = uid
    database.data["ground_truth"] = {uid: {"boxes": [{"xmin": 5, "ymin": 6, "xmax": 20, "ymax": 30}]}}
    database.data["detections"] = {"I1": {"status": "done", **FakeDetector().detect(None)}}
    database.data["opencv_detections"] = {"I1": {"status": "done", "boxes": [
        {"xmin": 10, "ymin": 12, "xmax": 20, "ymax": 22}]}}
    monkeypatch.setattr(db_service, "get_db", lambda: database)
    frames = []
    monkeypatch.setattr(dicom_canvas, "image_canvas", lambda image, **args: frames.append(args))

    def open_page(page="pages/2_Viewer.py", user=READER1):
        app = AppTest.from_file(APP, default_timeout=60)
        app.secrets["storage"] = {"image_dir": str(root)}
        app.session_state["user"] = user
        app.session_state["viewer_source"] = "등록된 케이스"
        app.session_state["viewer_case_id"] = "C1"
        app.switch_page(page).run()
        assert not app.exception
        return app

    return open_page, database, frames


def button(app, label):
    return next(b for b in app.button if b.label == label)


def test_box_layers_toggle_and_keep_original_coordinates_during_zoom(screen):
    open_page, _, frames = screen
    app = open_page()
    initial = frames[-1]["boxes"]
    assert {box["color"] for box in initial} == {"#30e878", "#ffe34d", "#459dff"}
    app.slider(key="viewer_zoom").set_value(2.0).run()
    app.slider(key="viewer_x").set_value(100).run()
    assert frames[-1]["boxes"] == initial
    assert frames[-1]["crop"][0] == 32
    app.checkbox(key="show_model").uncheck().run()
    assert {box["color"] for box in frames[-1]["boxes"]} == {"#30e878", "#459dff"}


def test_roi_process_save_clear_and_reload(screen):
    open_page, database, frames = screen
    app = open_page()
    token = sha256(repr(app.session_state["viewer_file_id"]).encode()).hexdigest()
    app.session_state["viewer_canvas"] = {"id": "roi1", "kind": "roi", "token": token,
        "epoch": app.session_state["viewer_epoch"], "start": [5, 8], "end": [20, 30]}
    app.run()
    assert app.session_state["viewer_roi"] == {"x": 5, "y": 8, "w": 16, "h": 23}
    assert app.checkbox(key="viewer_roi_enabled").value
    app.button(key="apply_algorithm").click().run()
    assert not app.exception and not app.error
    assert len(database.data["processing_logs"]) == 1
    assert frames[-1]["roi"] == app.session_state["viewer_roi"]
    app.run()  # 방금 저장한 이력 새로 조회
    app.button(key="clear_processed").click().run()
    assert "viewer_processed" not in app.session_state
    app.button(key="load_processing").click().run()
    assert not app.exception and not app.error
    assert app.session_state["viewer_processed"]["log"]["algorithm"] == "CLAHE"


def test_report_form_then_complete_review(screen):
    open_page, database, _ = screen
    app = open_page("pages/3_Review.py")
    next(s for s in app.selectbox if s.label == "작성 종류").select("report")
    next(t for t in app.text_area if t.label == "내용").set_value("교육용 모의 리포트")
    button(app, "메모·리포트 저장").click().run()
    assert not app.exception
    assert any(n["type"] == "report" for n in database.data["notes"].values())
    button(app, "검토 완료").click().run()
    assert not app.exception
    assert database.data["cases"]["C1"]["status"] == "완료"


def test_box_verdict_form_persists_and_reloads(screen):
    open_page, database, _ = screen
    app = open_page("pages/3_Review.py")
    next(r for r in app.radio if r.label == "판정").set_value("오검출")
    button(app, "박스 검토 저장").click().run()
    assert not app.exception and not app.error
    assert list(database.data["detection_reviews"].values())[0]["verdict"] == "오검출"
    app.run()
    assert any("저장된 판정: 오검출" in c.value for c in app.caption)


def test_missing_original_shows_actionable_message_without_local_path(screen, workbench):
    open_page, _, _ = screen
    _, root = workbench
    (root / "C1/I1/original/0001.dcm").unlink()
    app = open_page()
    assert any("원본 파일이 이 서버에 없습니다. 다른 환경에서 등록된 케이스일 수 있습니다." in e.value
               for e in app.error)
    assert all(str(root) not in e.value for e in app.error)


def test_other_original_read_errors_keep_generic_message(screen, monkeypatch):
    from services import access

    open_page, _, _ = screen

    def denied(*args):
        raise PermissionError("private path and server detail")

    monkeypatch.setattr(access, "original_path", denied)
    app = open_page()
    assert [e.value for e in app.error] == ["등록 영상을 불러오지 못했습니다."]


def test_technician_can_view_but_cannot_run_algorithms_or_detection(screen):
    open_page, _, _ = screen
    app = open_page(user=TECH)
    assert app.button(key="apply_algorithm").disabled
    assert app.button(key="detect_model").disabled
    assert app.button(key="detect_opencv").disabled
    assert any("현재 방사선사 계정" in i.value for i in app.info)


def test_assigned_reader_sees_start_review_hint_and_can_enable_detection(screen):
    open_page, database, _ = screen
    database.data["cases"]["C1"]["status"] = "배정"
    app = open_page()
    assert app.button(key="detect_model").disabled
    assert any("검토 시작" in i.value for i in app.info)
    app.button(key="detection_open_review").click().run()
    assert not app.exception
    assert app.session_state["review_case_id"] == "C1"
    # AppTest에는 브라우저 URL 갱신이 없어 후속 rerun의 페이지를 명시한다.
    app.switch_page("pages/3_Review.py").run()
    button(app, "검토 시작").click().run()
    assert database.data["cases"]["C1"]["status"] == "검토 중"
    app.switch_page("pages/3_Review.py").run()
    button(app, "뷰어에서 영상·박스 열기").click().run()
    app.switch_page("pages/2_Viewer.py").run()
    assert not app.exception
    assert not app.button(key="detect_model").disabled
    assert not app.button(key="detect_opencv").disabled
