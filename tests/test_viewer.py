"""뷰어의 로그인 보호와 파일 전환·초기화를 실제 화면 실행으로 검증한다."""
from pathlib import Path

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from core import dicom_io

PAGE = Path(__file__).resolve().parents[1] / "pages" / "2_Viewer.py"


@pytest.fixture
def viewer(monkeypatch, tmp_path):
    samples = [tmp_path / "first.dicom", tmp_path / "second.dicom"]
    for sample in samples:
        sample.write_bytes(b"test fixture")
    original_glob = Path.glob

    def sample_glob(path, pattern):
        if path.name == "sample" and path.parent.name == "data":
            return iter(samples)
        return original_glob(path, pattern)

    def read_sample(file):
        pixels = np.arange(100, dtype=np.float32).reshape(10, 10)
        return pixels, {"modality": "CT", "missing_tags": [], "rows": 10, "cols": 10,
                        "window_center": 40.0, "window_width": 80.0}

    monkeypatch.setattr(Path, "glob", sample_glob)
    monkeypatch.setattr(dicom_io, "load_dicom", read_sample)
    app = AppTest.from_file(str(PAGE), default_timeout=30)
    app.session_state["user"] = {"uid": "test", "email": "test@example.com", "role": "reader"}
    return app


def test_login_required():
    app = AppTest.from_file(str(PAGE.parents[1] / "app.py"), default_timeout=30).run()
    app.switch_page("pages/2_Viewer.py").run()
    assert not app.exception
    assert any("로그인이 필요" in warning.value for warning in app.warning)
    assert not app.slider


def test_viewer_adjust_and_reset(viewer):
    viewer.run()
    assert not viewer.exception and not viewer.error
    viewer.slider(key="viewer_zoom").set_value(2.0).run()
    viewer.slider(key="viewer_x").set_value(100)
    viewer.run()
    assert not viewer.exception
    assert any("너비=5, 높이=5" in caption.value for caption in viewer.caption)
    viewer.number_input(key="viewer_center").set_value(20.0).run()
    next(button for button in viewer.button if button.label == "화면 초기화").click().run()
    assert not viewer.exception
    assert viewer.number_input(key="viewer_center").value == 40.0
    assert viewer.slider(key="viewer_zoom").value == 1.0
    assert viewer.slider(key="viewer_x").value == 50


def test_new_sample_resets_settings(viewer):
    viewer.run()
    viewer.slider(key="viewer_zoom").set_value(3.0).run()
    next(select for select in viewer.selectbox if select.label == "샘플 파일").select_index(1).run()
    assert not viewer.exception
    assert viewer.slider(key="viewer_zoom").value == 1.0
    assert viewer.number_input(key="viewer_center").value == 40.0


def test_ct_preset_and_auto_window(viewer):
    viewer.run()
    next(button for button in viewer.button if button.label == "프리셋 적용").click().run()
    assert not viewer.exception
    assert viewer.number_input(key="viewer_center").value == -600.0
    assert viewer.number_input(key="viewer_width").value == 1500.0
    next(button for button in viewer.button if button.label == "자동 윈도우").click().run()
    assert not viewer.exception
    assert viewer.number_input(key="viewer_center").value == 50.0


def test_invalid_file_shows_error(viewer, monkeypatch):
    def reject(file):
        raise dicom_io.InvalidDicomError("지원하지 않는 파일입니다.")

    monkeypatch.setattr(dicom_io, "load_dicom", reject)
    viewer.run()
    assert not viewer.exception
    assert viewer.error[0].value == "지원하지 않는 파일입니다."
    assert not viewer.slider


def canvas_event(viewer, kind, **values):
    from hashlib import sha256
    token = sha256(repr(viewer.session_state["viewer_file_id"]).encode()).hexdigest()
    viewer.session_state["viewer_canvas"] = {
        "id": str(values), "token": token, "epoch": viewer.session_state["viewer_epoch"],
        "kind": kind, **values,
    }
    viewer.run()
    assert not viewer.exception


def test_click_navigation_updates_fine_controls(viewer):
    viewer.run()
    canvas_event(viewer, "navigate", zoom=2.5, x=80, y=20)
    assert viewer.slider(key="viewer_zoom").value == 2.5
    assert viewer.slider(key="viewer_x").value == 80
    assert viewer.slider(key="viewer_y").value == 20
    # A later fine adjustment must not replay the previous canvas event.
    viewer.slider(key="viewer_x").set_value(30).run()
    assert viewer.slider(key="viewer_x").value == 30


def test_measurement_survives_zoom_and_clears_on_reset(viewer):
    viewer.run()
    canvas_event(viewer, "measure", point=[1, 2])
    canvas_event(viewer, "measure", point=[4, 6])
    assert viewer.metric[0].value == "5.00 px"
    viewer.slider(key="viewer_zoom").set_value(2.0).run()
    assert viewer.metric[0].value == "5.00 px"
    next(button for button in viewer.button if button.label == "측정 지우기").click().run()
    assert viewer.session_state["viewer_points"] == []
    assert not viewer.metric
    # The retained component value from before reset must be ignored.
    viewer.run()
    assert viewer.session_state["viewer_points"] == []


def test_switch_sample_clears_measurement(viewer):
    viewer.run()
    canvas_event(viewer, "measure", point=[1, 2])
    next(select for select in viewer.selectbox if select.label == "샘플 파일").select_index(1).run()
    assert not viewer.exception
    assert viewer.session_state["viewer_points"] == []
