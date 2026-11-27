"""등록할 때 자동 탐지(services/detection.py + register_case) 테스트. 실제 모델·Firebase 없이 확인한다."""
import pytest

from core.detector import Detector, DetectorError
from core.workflow import PermissionDenied
from services import detection
from services.access import ImageNotFound
from services.detection import get_detections, load_detector, run_detection
from test_cases import IDS, NOW, RADIOGRAPHER, FakeDB, register_case


BOX = {"xmin": 10.0, "ymin": 20.0, "xmax": 30.0, "ymax": 50.0, "score": 0.9, "class_name": "implant"}


class FakeDetector:
    model_name = "implant.onnx"

    def __init__(self, boxes=(BOX,), error=None):
        self.boxes, self.error, self.calls = list(boxes), error, 0

    def detect(self, pixels):
        self.calls += 1
        if self.error:
            raise self.error
        return {"boxes": self.boxes, "model": self.model_name, "conf": 0.3, "elapsed_ms": 12.5}


def register(db, tmp_path, detector):
    from test_cases import dicom_bytes
    return register_case(RADIOGRAPHER, dicom_bytes(), db=db, storage_root=tmp_path, now=NOW, ids=IDS,
                         detector=detector)


# ---------------------------------------------------------------- 등록할 때 탐지
def test_register_saves_detections(tmp_path):
    db, det = FakeDB(), FakeDetector()
    result = register(db, tmp_path, det)
    case_id, image_id = IDS
    assert det.calls == 1
    assert result["detection"] == {"status": "done", "n_boxes": 1}
    saved = db.data["detections"][image_id]
    assert saved["status"] == "done" and saved["boxes"] == [BOX] and saved["n_boxes"] == 1
    assert saved["image_id"] == image_id and saved["case_id"] == case_id and saved["run_at"] == NOW
    assert saved["model"] == "implant.onnx" and saved["conf"] == 0.3 and saved["error"] is None
    assert db.commits == 1  # 케이스·영상·탐지·이력이 한 번에 저장됨


def test_register_without_model_still_works(tmp_path):
    db = FakeDB()
    result = register(db, tmp_path, None)
    assert result["detection"] == {"status": "no_model", "n_boxes": 0}
    assert db.data["detections"][IDS[1]]["status"] == "no_model"
    assert IDS[0] in db.data["cases"]


def test_detection_with_no_boxes_is_done(tmp_path):
    db = FakeDB()
    assert register(db, tmp_path, FakeDetector(boxes=()))["detection"] == {"status": "done", "n_boxes": 0}


@pytest.mark.parametrize("error,message", [
    (DetectorError("탐지 모델을 실행하지 못했습니다. 모델 파일을 확인하세요."), "탐지 모델을 실행하지 못했습니다"),
    (RuntimeError("onnx internal trace /path/secret"), "탐지 중 오류가 발생했습니다."),  # 내부 메시지는 숨김
])
def test_detection_failure_does_not_block_registration(tmp_path, error, message):
    db = FakeDB()
    result = register(db, tmp_path, FakeDetector(error=error))
    assert result["status"] in ("접수", "재촬영 요청") and IDS[0] in db.data["cases"]
    saved = db.data["detections"][IDS[1]]
    assert saved["status"] == "failed" and saved["boxes"] == [] and message in saved["error"]
    assert "secret" not in saved["error"]


def test_db_failure_leaves_no_detection(tmp_path):
    from services.cases import RegistrationError
    db = FakeDB(fail_commit="before")
    with pytest.raises(RegistrationError):
        register(db, tmp_path, FakeDetector())
    assert "detections" not in db.data  # 케이스가 없는데 탐지 결과만 남지 않음


def test_run_detection_with_real_detector_class():
    from test_detector import FakeSession, model_output, pixels
    det = Detector(session=FakeSession(model_output(((200, 400, 600, 1000), 0.9))))
    out = run_detection(det, pixels())
    assert out["status"] == "done" and out["n_boxes"] == 1 and out["model"] == "custom"
    assert out["boxes"][0]["xmin"] == pytest.approx(200, abs=1)


# ---------------------------------------------------------------- 모델 불러오기
@pytest.fixture
def fresh_cache():
    detection._open_detector.clear()
    yield
    detection._open_detector.clear()


def test_load_detector_missing_file_returns_none(tmp_path, fresh_cache):
    assert load_detector(tmp_path / "none.onnx") is None


def test_load_detector_broken_file_returns_none(tmp_path, fresh_cache):
    bad = tmp_path / "bad.onnx"
    bad.write_bytes(b"broken")
    assert load_detector(bad) is None


# ---------------------------------------------------------------- 조회 (권한)
@pytest.fixture
def db():
    db = FakeDB()
    db.data = {
        "cases": {"C1": {"assignee_uid": "r1"}},
        "images": {"I1": {"case_id": "C1"}, "I2": {"case_id": "C1"}},
        "detections": {"I1": {"status": "done", "boxes": [BOX], "n_boxes": 1, "write_token": "x"}},
    }
    return db


@pytest.mark.parametrize("user", [{"uid": "m1", "role": "manager"}, {"uid": "t1", "role": "radiographer"},
                                  {"uid": "r1", "role": "reader"}])
def test_get_detections_allowed(db, user):
    det = get_detections(user, "I1", db=db)
    assert det["boxes"] == [BOX] and "write_token" not in det


def test_get_detections_other_reader_denied(db):
    with pytest.raises(PermissionDenied, match="본인에게 배정된"):
        get_detections({"uid": "r2", "role": "reader"}, "I1", db=db)


@pytest.mark.parametrize("user", [None, {}, {"uid": "x", "role": "admin"}])
def test_get_detections_login_required(db, user):
    with pytest.raises(PermissionDenied):
        get_detections(user, "I1", db=db)


def test_get_detections_none_and_missing(db):
    manager = {"uid": "m1", "role": "manager"}
    assert get_detections(manager, "I2", db=db) is None  # 탐지 기록 없음
    with pytest.raises(ImageNotFound):
        get_detections(manager, "NOPE", db=db)
