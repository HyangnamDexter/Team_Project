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
IDS = ("CASE-20261119-aaaaaaaa", "IMG-20261119-bbbbbbbb")
RADIOGRAPHER = {"uid": "u-rad", "email": "rad@test.com", "role": "radiographer"}


class AlreadyExists(Exception):
    """Firestore create 가 이미 있는 문서에 쓸 때 내는 오류 흉내"""


# ---------------------------------------------------------------- 가짜 Firestore
class FakeDoc:
    def __init__(self, db, collection, doc_id):
        self.db, self.collection, self.id = db, collection, doc_id

    def get(self):
        if self.db.fail_get:
            raise TimeoutError("read timeout")
        data = self.db.data.get(self.collection, {}).get(self.id)
        return type("Snap", (), {"exists": data is not None, "to_dict": lambda s: data})()


class FakeBatch:
    def __init__(self, db):
        self.db, self.ops = db, []

    def set(self, ref, doc):
        self.ops.append(("set", ref, doc))

    def create(self, ref, doc):
        self.ops.append(("create", ref, doc))

    def commit(self):
        if self.db.fail_commit == "before":  # 아무것도 기록하지 못하고 실패
            raise RuntimeError("network down")
        for kind, ref, _ in self.ops:  # create 는 이미 있으면 전체 실패 (batch 는 모두 반영되거나 하나도 안 됨)
            if kind == "create" and ref.id in self.db.data.get(ref.collection, {}):
                raise AlreadyExists(f"{ref.collection}/{ref.id}")
        for _, ref, doc in self.ops:
            self.db.data.setdefault(ref.collection, {})[ref.id] = doc
        self.db.commits += 1
        if self.db.fail_commit == "after":  # 기록은 됐는데 응답이 유실됨 (타임아웃)
            raise TimeoutError("deadline exceeded")


class FakeDB:
    def __init__(self, fail_commit=None, fail_get=False):
        # fail_commit: None | "before" | "after",  fail_get: 결과 확인용 읽기도 실패
        self.data, self.fail_commit, self.fail_get, self._auto, self.commits = {}, fail_commit, fail_get, 0, 0

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
    assert case_id.startswith("CASE-20261119-") and len(case_id) == len("CASE-20261119-") + 8
    assert image_id.startswith("IMG-20261119-") and len(image_id) == len("IMG-20261119-") + 8


# ---------------------------------------------------------------- 실패 처리
def test_db_failure_removes_files(tmp_path):
    with pytest.raises(RegistrationError, match="DB 저장"):
        register(FakeDB(fail_commit="before"), tmp_path)
    assert not (tmp_path / IDS[0]).exists()  # 파일이 남지 않음


# ---------------------------------------------------------------- 리뷰 회귀 1: ID 충돌 시 덮어쓰기 금지
def other_case_db():
    """다른 케이스가 이미 같은 image_id 를 쓰고 있는 DB"""
    db = FakeDB()
    db.data["images"] = {IDS[1]: {"case_id": "CASE-OTHER", "write_token": "other"}}
    return db


def test_image_id_conflict_never_overwrites_existing(tmp_path):
    db = other_case_db()
    with pytest.raises(RegistrationError, match="이미 사용 중"):
        register(db, tmp_path)  # ids 를 고정했으므로 다시 시도하지 않고 실패
    assert db.data["images"][IDS[1]] == {"case_id": "CASE-OTHER", "write_token": "other"}  # 그대로
    assert IDS[0] not in db.data.get("cases", {}) and "case_history" not in db.data  # 우리 기록은 하나도 없음
    assert not (tmp_path / IDS[0]).exists()  # 우리 임시 파일도 정리


def test_case_id_conflict_never_overwrites_existing(tmp_path):
    db = FakeDB()
    db.data["cases"] = {IDS[0]: {"status": "완료", "write_token": "other"}}
    with pytest.raises(RegistrationError, match="이미 사용 중"):
        register(db, tmp_path)
    assert db.data["cases"][IDS[0]] == {"status": "완료", "write_token": "other"}


def test_conflict_retries_with_new_ids(tmp_path, monkeypatch):
    db = other_case_db()
    fresh = ("CASE-20261119-cccccccc", "IMG-20261119-dddddddd")
    tries = iter([IDS, fresh])
    monkeypatch.setattr(cases, "new_ids", lambda now: next(tries))
    result = register_case(RADIOGRAPHER, dicom_bytes(), db=db, storage_root=tmp_path, now=NOW)
    assert (result["case_id"], result["image_id"]) == fresh
    assert db.data["images"][IDS[1]]["case_id"] == "CASE-OTHER"  # 기존 문서는 그대로
    assert db.data["images"][fresh[1]]["case_id"] == fresh[0]
    assert (tmp_path / fresh[0] / fresh[1] / "original" / "0001.dcm").exists()
    assert not (tmp_path / IDS[0]).exists()


def test_existing_folder_on_disk_is_not_deleted(tmp_path, monkeypatch):
    # 디스크에 같은 ID 폴더(다른 케이스의 원본)가 있으면 지우지 않고 새 ID로 등록
    other = tmp_path / IDS[0] / IDS[1] / "original"
    other.mkdir(parents=True)
    (other / "0001.dcm").write_bytes(b"other case")
    fresh = ("CASE-20261119-eeeeeeee", "IMG-20261119-ffffffff")
    tries = iter([IDS, fresh])
    monkeypatch.setattr(cases, "new_ids", lambda now: next(tries))
    result = register_case(RADIOGRAPHER, dicom_bytes(), db=FakeDB(), storage_root=tmp_path, now=NOW)
    assert result["case_id"] == fresh[0]
    assert (other / "0001.dcm").read_bytes() == b"other case"


# ---------------------------------------------------------------- 리뷰 회귀 2: 응답 유실 시 원본 보존
def test_lost_response_after_db_write_keeps_file_and_succeeds(tmp_path):
    db = FakeDB(fail_commit="after")  # DB에는 기록됐지만 commit 이 타임아웃
    result = register(db, tmp_path)
    assert result["case_id"] == IDS[0] and db.commits == 1
    assert (tmp_path / IDS[0] / IDS[1] / "original" / "0001.dcm").exists()  # 원본이 남아 있음
    assert db.data["images"][IDS[1]]["case_id"] == IDS[0]


def test_unknown_result_keeps_tmp_and_cleanup_recovers(tmp_path):
    db = FakeDB(fail_commit="after", fail_get=True)  # 기록 후 응답 유실 + 확인용 읽기도 실패
    with pytest.raises(RegistrationError, match="확인하지 못했습니다"):
        register(db, tmp_path)
    tmp = tmp_path / IDS[0] / IDS[1] / "original.tmp" / "0001.dcm"
    assert tmp.exists()  # 결과가 불확실하면 파일을 지우지 않는다

    db.fail_get = False  # 네트워크 복구 후 앱 재시작
    assert cleanup_orphans(tmp_path, db) == {"recovered": [IDS[1]], "removed": []}
    assert (tmp_path / IDS[0] / IDS[1] / "original" / "0001.dcm").exists()


def test_unknown_result_without_db_write_is_removed_later(tmp_path):
    db = FakeDB(fail_commit="before", fail_get=True)  # 실제로는 기록 안 됨 + 확인도 실패
    with pytest.raises(RegistrationError, match="확인하지 못했습니다"):
        register(db, tmp_path)
    db.fail_get = False
    assert cleanup_orphans(tmp_path, db) == {"recovered": [], "removed": [IDS[1]]}
    assert not (tmp_path / IDS[0]).exists()


def test_cleanup_skips_when_db_unreachable(tmp_path):
    tmp = tmp_path / "CASE-u" / "IMG-u" / "original.tmp"
    tmp.mkdir(parents=True)
    assert cleanup_orphans(tmp_path, FakeDB(fail_get=True)) == {"recovered": [], "removed": []}
    assert tmp.exists()


def test_cleanup_does_not_recover_into_other_cases_doc(tmp_path):
    # 같은 image_id 문서가 DB에 있지만 다른 케이스 것 → 우리 임시 폴더는 복구하지 않고 지운다
    db = FakeDB()
    db.data["images"] = {"IMG-x": {"case_id": "CASE-OTHER"}}
    tmp = tmp_path / "CASE-mine" / "IMG-x" / "original.tmp"
    tmp.mkdir(parents=True)
    assert cleanup_orphans(tmp_path, db) == {"recovered": [], "removed": ["IMG-x"]}
    assert not (tmp_path / "CASE-mine").exists()


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


# ---------------------------------------------------------------- Modality 태그 없는 파일 (VinDr-SpineXR)
@pytest.fixture
def no_modality(monkeypatch):
    """PR #15 의 load_dicom 처럼 Modality 가 없으면 modality="" + missing_tags 로 돌려주게 흉내 낸다."""
    real = cases.load_dicom

    def fake(file):
        pixels, header = real(file)
        return pixels, dict(header, modality="", missing_tags=["Modality"])
    monkeypatch.setattr(cases, "load_dicom", fake)


def register_with(db, tmp_path, modality):
    return register_case(RADIOGRAPHER, dicom_bytes(), db=db, storage_root=tmp_path,
                         modality=modality, now=NOW, ids=IDS)


@pytest.mark.parametrize("chosen", ["DX", "CR", "dx"])
def test_missing_modality_uses_user_choice(tmp_path, no_modality, chosen):
    db = FakeDB()
    result = register_with(db, tmp_path, chosen)
    assert result["qc"]["modality"] == chosen.upper()
    assert db.data["cases"][IDS[0]]["modality"] == chosen.upper()
    image = db.data["images"][IDS[1]]
    assert image["modality"] == chosen.upper() and image["modality_source"] == "user"
    assert "Modality" in image["qc"]["missing_tags"]  # 파일에 태그가 없었다는 사실은 남긴다


def test_missing_modality_without_choice_asks_user(tmp_path, no_modality):
    db = FakeDB()
    with pytest.raises(RegistrationError, match="DX 또는 CR을 선택"):
        register_with(db, tmp_path, None)
    assert db.data == {} and not any(tmp_path.iterdir())


@pytest.mark.parametrize("chosen", ["CT", "MR", "abc"])
def test_missing_modality_rejects_non_xray_choice(tmp_path, no_modality, chosen):
    with pytest.raises(RegistrationError, match="DX 또는 CR만"):
        register_with(FakeDB(), tmp_path, chosen)


def test_file_modality_wins_over_choice(tmp_path):
    db = FakeDB()
    register_case(RADIOGRAPHER, dicom_bytes(Modality="CR"), db=db, storage_root=tmp_path,
                  modality="DX", now=NOW, ids=IDS)
    image = db.data["images"][IDS[1]]
    assert image["modality"] == "CR" and image["modality_source"] == "file"


# ---------------------------------------------------------------- 정답(ground_truth) 연결 키
def test_register_saves_sop_instance_uid(tmp_path):
    # VinDr 는 SOPInstanceUID 가 image_id 와 같다 → 정답 문서와 이것으로 연결한다
    db = FakeDB()
    register(db, tmp_path, dicom_bytes(SOPInstanceUID="0036129847a4f5c1e9fda9a1e1d2b3c4"))
    assert db.data["images"][IDS[1]]["sop_instance_uid"] == "0036129847a4f5c1e9fda9a1e1d2b3c4"


def test_register_without_sop_instance_uid_still_works(tmp_path):
    db = FakeDB()
    register(db, tmp_path, dicom_bytes(SOPInstanceUID=""))
    assert db.data["images"][IDS[1]]["sop_instance_uid"] is None
