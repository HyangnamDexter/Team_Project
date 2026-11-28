"""영상처리·메모·박스 검토의 저장, 권한, 재실행과 실패 복구를 오프라인 검증한다."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace

import cv2
import numpy as np
import pydicom
from pydicom.dataset import Dataset
import pytest

from core.dicom_io import load_dicom
from core.dicom_privacy import identifier_tags, remove_identifiers
from core.detector import Detector
from core.quality import tag_check
from core.viewer_geometry import roi_from_points
from core.workflow import PermissionDenied
from services import box_review, notes, processing, review
from services.access import storage_path
from services.cases import register_case
from services.ground_truth import get_ground_truth
from test_cases import FakeDB as RegistrationDB, RADIOGRAPHER, dicom_bytes
from test_review import FakeDB, Tx, READER1, READER2, TECH, ADMIN


@pytest.fixture
def workbench(monkeypatch, tmp_path):
    db = FakeDB()
    db.add_case("C1", "검토 중", assignee="r1")
    data = dicom_bytes()
    original = tmp_path / "C1/I1/original/0001.dcm"
    original.parent.mkdir(parents=True)
    original.write_bytes(data)
    db.data["images"] = {"I1": {"case_id": "C1", "original_dir": "C1/I1/original",
                                      "slice_files": ["0001.dcm"], "is_current": True, "modality": "DX"}}

    def transaction(database, fn):
        tx = Tx(database)
        result = fn(tx)
        tx.commit()
        return result

    monkeypatch.setattr(review, "_run_transaction", transaction)
    return db, tmp_path


WINDOW = {"center": 2000.0, "width": 4000.0}
ROI = {"x": 8, "y": 10, "w": 20, "h": 25}


def process(db, root, **kwargs):
    return processing.run_processing(READER1, "I1", "CLAHE", {"clip_limit": 2.0, "tile_size": 8},
                                      WINDOW, ROI, db=db, storage_root=root, **kwargs)


def test_processing_roi_history_roundtrip_and_replay(workbench):
    db, root = workbench
    path = root / "C1/I1/original/0001.dcm"
    before = path.read_bytes()
    log = process(db, root)
    saved, image = processing.load_processing(READER1, log["log_id"], db=db, storage_root=root)
    from core.dicom_io import apply_window
    original = apply_window(load_dicom(path)[0], **WINDOW)
    np.testing.assert_array_equal(image[:10], original[:10])
    np.testing.assert_array_equal(image[35:], original[35:])
    assert not np.array_equal(image[10:35, 8:28], original[10:35, 8:28])
    assert path.read_bytes() == before
    assert saved["roi"] == ROI and saved["window"] == WINDOW and "hist" not in saved["features"]
    again = processing.replay_processing(READER1, log["log_id"], db=db, storage_root=root)
    assert again["reused"] and again["log_id"] == log["log_id"]
    assert len(list((root / "C1/I1/results").glob("*.png"))) == 1
    assert processing.list_processing(READER1, "I1", db=db)[0]["log_id"] == log["log_id"]


def test_duplicate_window_expires_after_ten_seconds(workbench):
    db, root = workbench
    now = datetime.now(timezone.utc)
    one = process(db, root, now=now)
    two = process(db, root, now=now + timedelta(seconds=11))
    assert one["log_id"] != two["log_id"] and not two["reused"]


@pytest.mark.parametrize("user", [None, TECH, ADMIN, READER2])
def test_processing_checks_role_and_assignee(workbench, user):
    db, root = workbench
    with pytest.raises(PermissionDenied):
        processing.run_processing(user, "I1", "CLAHE", {}, WINDOW, db=db, storage_root=root)
    assert "processing_logs" not in db.data and not (root / "C1/I1/results").exists()


def test_assignment_changed_before_commit_does_not_save(workbench, monkeypatch):
    db, root = workbench
    transaction = review._run_transaction

    def changed(database, fn):
        db.data["cases"]["C1"]["assignee_uid"] = "r2"
        return transaction(database, fn)

    monkeypatch.setattr(review, "_run_transaction", changed)
    with pytest.raises(PermissionDenied):
        process(db, root)
    assert not list((root / "C1/I1/results").glob("*.png"))
    assert "processing_logs" not in db.data


def test_processing_commit_response_lost_keeps_saved_result(workbench, monkeypatch):
    db, root = workbench
    transaction = review._run_transaction

    def lost(database, fn):
        transaction(database, fn)
        raise TimeoutError("response lost")

    monkeypatch.setattr(review, "_run_transaction", lost)
    log = process(db, root)
    assert storage_path(root, log["result_path"]).is_file()
    assert log["log_id"] in db.data["processing_logs"]


def test_processing_rejects_invalid_roi_without_writing(workbench):
    db, root = workbench
    with pytest.raises(ValueError):
        processing.run_processing(READER1, "I1", "CLAHE", {}, WINDOW,
                                  {"x": 60, "y": 60, "w": 20, "h": 20}, db=db, storage_root=root)
    assert "processing_logs" not in db.data


def test_notes_report_enables_completion_and_tracks_edits(workbench):
    db, _ = workbench
    report = notes.save_note(READER1, "C1", "처음 리포트", "report", db=db)
    edited = notes.save_note(READER1, "C1", "수정 리포트", "report", note_id=report["note_id"],
                             expected_content="처음 리포트", db=db)
    assert edited["edits"][0]["before"] == "처음 리포트"
    with pytest.raises(notes.NoteError, match="변경"):
        notes.save_note(READER1, "C1", "덮어쓰기", "report", note_id=report["note_id"],
                        expected_content="처음 리포트", db=db)
    review.complete_review(READER1, "C1", db=db)
    assert db.data["cases"]["C1"]["status"] == "완료"
    with pytest.raises(PermissionDenied):
        notes.save_note(READER1, "C1", "완료 후 수정", "report", note_id=report["note_id"],
                        expected_content="수정 리포트", db=db)
    assert notes.list_notes(READER1, "C1", db=db)[0]["content"] == "수정 리포트"


@pytest.mark.parametrize("user", [None, TECH, READER2])
def test_notes_deny_other_roles_or_unassigned_reader(workbench, user):
    db, _ = workbench
    with pytest.raises(PermissionDenied):
        notes.save_note(user, "C1", "메모", db=db)


def test_manager_only_creates_memo_not_report(workbench):
    db, _ = workbench
    assert notes.save_note(ADMIN, "C1", "관리 메모", db=db)["type"] == "memo"
    with pytest.raises(PermissionDenied):
        notes.save_note(ADMIN, "C1", "리포트", "report", db=db)


@pytest.mark.parametrize("kind,title", [("memo", "메모"), ("report", "리포트")])
def test_note_history_distinguishes_memo_and_report_creation_and_edit(workbench, kind, title):
    db, _ = workbench
    saved = notes.save_note(READER1, "C1", "처음 내용", kind, db=db)
    notes.save_note(READER1, "C1", "수정 내용", kind, db=db,
                    note_id=saved["note_id"], expected_content="처음 내용")
    assert [row["reason"] for row in db.history()] == [f"{title} 작성", f"{title} 수정"]


class FakeDetector:
    def detect(self, pixels):
        return {"boxes": [{"xmin": 2, "ymin": 3, "xmax": 20, "ymax": 25, "score": .8}],
                "model": "fake.onnx", "conf": .4, "elapsed_ms": 1}


def test_box_review_stale_runs_and_edits(workbench):
    db, root = workbench
    det = box_review.run_boxes(READER1, "I1", "model", db=db, storage_root=root, detector=FakeDetector())
    signature = box_review.fingerprint(det)
    box_review.save_box_review(READER1, "I1", "model", 0, "맞음", signature, db=db)
    result = box_review.save_box_review(READER1, "I1", "model", 0, "오검출", signature, db=db)
    assert result["edits"][0]["verdict"] == "맞음"
    assert box_review.list_box_reviews(READER1, "I1", db=db)[0]["verdict"] == "오검출"
    box_review.run_boxes(READER1, "I1", "model", db=db, storage_root=root, detector=FakeDetector())
    with pytest.raises(box_review.BoxReviewError, match="변경"):
        box_review.save_box_review(READER1, "I1", "model", 0, "맞음", signature, db=db)


@pytest.mark.parametrize("user", [None, TECH, ADMIN, READER2])
def test_box_reviews_deny_unauthorized_writes(workbench, user):
    db, _ = workbench
    with pytest.raises(PermissionDenied):
        box_review.save_box_review(user, "I1", "model", 0, "맞음", "hash", db=db)
    assert "detection_reviews" not in db.data


def test_opencv_boxes_are_in_original_coordinates(workbench):
    db, root = workbench
    doc = box_review.run_boxes(READER1, "I1", "opencv", db=db, storage_root=root)
    assert doc["status"] == "done"
    for b in doc["boxes"]:
        assert 0 <= b["xmin"] < b["xmax"] <= 64
        assert 0 <= b["ymin"] < b["ymax"] <= 64


def test_ground_truth_legacy_image_uses_authorized_original(workbench):
    db, root = workbench
    uid = load_dicom(root / "C1/I1/original/0001.dcm")[1]["sop_instance_uid"]
    db.data["ground_truth"] = {uid: {"boxes": [], "label": "normal"}}
    assert get_ground_truth(READER1, "I1", db=db, storage_root=root)["label"] == "normal"
    with pytest.raises(PermissionDenied):
        get_ground_truth(READER2, "I1", db=db, storage_root=root)


def test_private_and_nested_identifiers_removed_before_registration(tmp_path):
    original = dicom_bytes(PatientName="Private^Name", PatientID="secret-id")
    ds = pydicom.dcmread(BytesIO(original))
    child = Dataset()
    child.PatientName = "Nested^Name"
    ds.RequestAttributesSequence = [child]
    ds.add_new((0x0011, 0x0010), "LO", "private-owner")
    stream = BytesIO()
    ds.save_as(stream, enforce_file_format=True)
    data = stream.getvalue()
    clean, removed = remove_identifiers(data)
    scrubbed = pydicom.dcmread(BytesIO(clean))
    assert identifier_tags(scrubbed) == [] and "PatientName" in removed
    np.testing.assert_array_equal(ds.pixel_array, scrubbed.pixel_array)
    db = RegistrationDB()
    result = register_case(RADIOGRAPHER, data, db=db, storage_root=tmp_path)
    image = db.data["images"][result["image_id"]]
    stored = tmp_path / image["original_dir"] / "0001.dcm"
    assert identifier_tags(pydicom.dcmread(stored)) == []
    assert "PatientID" in result["qc"]["phi_removed"]
    assert image["sop_instance_uid"] == str(ds.SOPInstanceUID)


def test_tag_check_missing_resolved_modality_phi_and_burned_in():
    header = {"modality": "DX", "present_tags": ["Rows", "Columns", "PatientName"],
              "burned_in_annotation": "YES"}
    result = tag_check(header)
    assert result["missing"] == ["BodyPartExamined"] and result["phi_found"] == ["PatientName"]
    assert any("픽셀" in issue for issue in result["issues"])


def test_missing_body_part_is_warning_and_does_not_block_review(tmp_path):
    db = RegistrationDB()
    result = register_case(RADIOGRAPHER, dicom_bytes(BodyPartExamined=""), db=db, storage_root=tmp_path)
    assert result["status"] == "접수" and result["qc"]["result"] == "warning"
    assert "BodyPartExamined" in result["qc"]["missing_tags"]
    assert result["qc"]["warnings"] and not result["qc"]["issues"]


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "C1/../../../secret"])
def test_storage_path_traversal_is_rejected(tmp_path, path):
    with pytest.raises(ValueError):
        storage_path(tmp_path, path)


def test_roi_drag_reverse_and_clip():
    assert roi_from_points([20, 30], [5, 8], (64, 64)) == {"x": 5, "y": 8, "w": 16, "h": 23}
    assert roi_from_points([-4, -8], [100, 100], (64, 64)) == {"x": 0, "y": 0, "w": 64, "h": 64}


def test_model_tensor_and_box_restoration():
    seen = []
    class Session:
        def get_inputs(self):
            return [SimpleNamespace(name="images", shape=[1, 3, 640, 640])]
        def run(self, outputs, feeds):
            seen.append(feeds["images"])
            # 100x200 -> 모델 안쪽 320x640, 상단 160 패딩
            return [np.array([[[320], [320], [320], [160], [.8]]], dtype=np.float32)]
    result = Detector(session=Session()).detect(np.arange(20000, dtype=np.float32).reshape(100, 200))
    assert seen[0].shape == (1, 3, 640, 640) and seen[0].dtype == np.float32
    assert seen[0].min() >= 0 and seen[0].max() <= 1
    assert result["conf"] == .4
    box = result["boxes"][0]
    assert [box[k] for k in ("xmin", "ymin", "xmax", "ymax")] == pytest.approx([50, 25, 150, 75])
