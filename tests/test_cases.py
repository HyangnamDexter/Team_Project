"""services/cases.py 케이스 등록 테스트. Firebase 없이 가짜 Firestore 로 확인한다."""
from datetime import datetime, timezone
from io import BytesIO

import numpy as np
import pytest
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid

from core.dicom_io import InvalidDicomError
from core.workflow import PermissionDenied
from services import cases
from services.cases import RegistrationError, cleanup_orphans, register_case

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
IDS = ("CASE-20261119-aaaaaa", "IMG-20261119-bbbb")
RADIOGRAPHER = {"uid": "u-rad", "email": "rad@test.com", "role": "radiographer"}


# ---------------------------------------------------------------- 가짜 Firestore
class FakeDoc:
    def __init__(self, db, collection, doc_id):
        self.db, self.collection, self.id = db, collection, doc_id

    def get(self):
        data = self.db.data.get(self.collection, {}).get(self.id)
        return type("Snap", (), {"exists": data is not None, "to_dict": lambda s: data})()


class FakeBatch:
    def __init__(self, db):
        self.db, self.ops = db, []

    def set(self, ref, doc):
        self.ops.append((ref, doc))

    def commit(self):
        if self.db.fail_commit:
            raise RuntimeError("network down")
        for ref, doc in self.ops:  # 전부 한 번에 반영 (중간 실패 없음)
            self.db.data.setdefault(ref.collection, {})[ref.id] = doc


class FakeDB:
    def __init__(self, fail_commit=False):
        self.data, self.fail_commit, self._auto = {}, fail_commit, 0

    def collection(self, name):
        db = self

        class Col:
            def document(self, doc_id=None):
                if doc_id is None:
                    db._auto += 1
                    doc_id = f"auto{db._auto}"
                return FakeDoc(db, name, doc_id)
        return Col()

    def batch(self):
        return FakeBatch(self)


# ---------------------------------------------------------------- 합성 DICOM
def dicom_bytes(pixels=None, **tags) -> bytes:
    if pixels is None:  # 대비·선명도가 충분한 X-ray 같은 영상
        rng = np.random.default_rng(0)
        pixels = (np.tile(np.linspace(500, 3500, 64), (64, 1)) + rng.normal(0, 300, (64, 64))).clip(0, 4095)
    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID, ds.SOPInstanceUID = meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID
    ds.Modality = "DX"
    ds.BodyPartExamined = "SPINE"
    ds.StudyDate = "20261119"
    ds.SeriesInstanceUID = "1.2.3"
    ds.Rows, ds.Columns = pixels.shape
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.PixelData = np.asarray(pixels).astype(np.uint16).tobytes()
    for key, value in tags.items():
        setattr(ds, key, value)
    stream = BytesIO()
    ds.save_as(stream, enforce_file_format=True)
    return stream.getvalue()


def register(db, tmp_path, data=None, user=RADIOGRAPHER):
    return register_case(user, data if data is not None else dicom_bytes(), db=db,
                         storage_root=tmp_path, now=NOW, ids=IDS)


# ---------------------------------------------------------------- 정상 등록
def test_register_saves_file_then_db(tmp_path):
    db, data = FakeDB(), dicom_bytes()
    result = register(db, tmp_path, data)

    case_id, image_id = IDS
    assert result["case_id"] == case_id and result["image_id"] == image_id
    saved = tmp_path / case_id / image_id / "original" / "0001.dcm"
    assert saved.read_bytes() == data                                   # 원본 그대로
    assert not (tmp_path / case_id / image_id / "original.tmp").exists()  # 임시 폴더는 이름이 바뀜

    case = db.data["cases"][case_id]
    assert case["created_by"] == "u-rad" and case["assignee_uid"] is None and case["is_mock"] is True
    assert case["modality"] == "DX" and case["body_part"] == "SPINE"
    image = db.data["images"][image_id]
    assert image["case_id"] == case_id
    assert image["original_dir"] == f"{case_id}/{image_id}/original"
    assert image["slice_files"] == ["0001.dcm"] and image["num_slices"] == 1
    assert image["qc"]["thresholds_version"] == "v1" and image["qc"]["series"] is None
    history = list(db.data["case_history"].values())
    assert len(history) == 1
    assert history[0]["from_status"] is None and history[0]["to_status"] == case["status"]
    assert history[0]["actor_uid"] == "u-rad"


def test_qc_pass_goes_to_received(tmp_path):
    result = register(FakeDB(), tmp_path)
    assert result["qc"]["issues"] == []
    assert result["status"] == "접수"


def test_qc_fail_goes_to_retake_with_reason(tmp_path):
    db = FakeDB()
    flat = np.full((64, 64), 1000)  # 대비·선명도 0 → 점검 실패
    result = register(db, tmp_path, dicom_bytes(flat))
    assert result["status"] == "재촬영 요청"
    assert "대비 부족" in result["qc"]["issues"]
    history = next(iter(db.data["case_history"].values()))
    assert "대비 부족" in history["reason"]
    assert (tmp_path / IDS[0] / IDS[1] / "original" / "0001.dcm").exists()  # 실패해도 등록은 됨 (T2)


def test_new_ids_format():
    case_id, image_id = cases.new_ids(NOW)
    assert case_id.startswith("CASE-20261119-") and len(case_id) == len("CASE-20261119-") + 6
    assert image_id.startswith("IMG-20261119-") and len(image_id) == len("IMG-20261119-") + 4


# ---------------------------------------------------------------- 실패 처리
def test_db_failure_removes_files(tmp_path):
    with pytest.raises(RegistrationError, match="DB 저장"):
        register(FakeDB(fail_commit=True), tmp_path)
    assert not (tmp_path / IDS[0]).exists()  # 파일이 남지 않음


@pytest.mark.parametrize("user", [None, {"uid": "x", "role": "reader"}, {"uid": "x", "role": "manager"}])
def test_only_radiographer_can_register(tmp_path, user):
    db = FakeDB()
    with pytest.raises(PermissionDenied):
        register(db, tmp_path, user=user)
    assert db.data == {} and not any(tmp_path.iterdir())


def test_invalid_dicom_rejected_without_saving(tmp_path):
    db = FakeDB()
    with pytest.raises(InvalidDicomError):
        register(db, tmp_path, b"not a dicom file")
    assert db.data == {} and not any(tmp_path.iterdir())


def test_empty_file_rejected(tmp_path):
    with pytest.raises(RegistrationError, match="빈 파일"):
        register(FakeDB(), tmp_path, b"")


def test_non_xray_rejected(tmp_path):
    db = FakeDB()
    with pytest.raises(RegistrationError, match="X-ray"):
        register(db, tmp_path, dicom_bytes(Modality="CT"))
    assert db.data == {} and not any(tmp_path.iterdir())


# ---------------------------------------------------------------- 남은 임시 폴더 정리
def test_cleanup_removes_tmp_not_in_db(tmp_path):
    tmp = tmp_path / "CASE-x" / "IMG-x" / "original.tmp"
    tmp.mkdir(parents=True)
    (tmp / "0001.dcm").write_bytes(b"data")
    result = cleanup_orphans(tmp_path, FakeDB())
    assert result == {"recovered": [], "removed": ["IMG-x"]}
    assert not (tmp_path / "CASE-x").exists()


def test_cleanup_recovers_tmp_already_in_db(tmp_path):
    db = FakeDB()
    db.data["images"] = {"IMG-y": {"case_id": "CASE-y"}}
    tmp = tmp_path / "CASE-y" / "IMG-y" / "original.tmp"
    tmp.mkdir(parents=True)
    (tmp / "0001.dcm").write_bytes(b"data")
    result = cleanup_orphans(tmp_path, db)
    assert result == {"recovered": ["IMG-y"], "removed": []}
    assert (tmp_path / "CASE-y" / "IMG-y" / "original" / "0001.dcm").read_bytes() == b"data"


def test_cleanup_never_touches_existing_original(tmp_path):
    db = FakeDB()
    db.data["images"] = {"IMG-z": {"case_id": "CASE-z"}}
    image_dir = tmp_path / "CASE-z" / "IMG-z"
    (image_dir / "original").mkdir(parents=True)
    (image_dir / "original" / "0001.dcm").write_bytes(b"keep")
    (image_dir / "original.tmp").mkdir()
    result = cleanup_orphans(tmp_path, db)
    assert result["removed"] == ["IMG-z"]
    assert (image_dir / "original" / "0001.dcm").read_bytes() == b"keep"
    assert not (image_dir / "original.tmp").exists()


def test_cleanup_missing_root(tmp_path):
    assert cleanup_orphans(tmp_path / "nope", FakeDB()) == {"recovered": [], "removed": []}
