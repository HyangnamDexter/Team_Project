"""services/ground_truth.py, scripts/import_ground_truth.py 테스트. Firebase 없이 가짜 DB로 확인한다."""
import pytest

from core.workflow import PermissionDenied
from scripts.prepare_spinexr import collect_images
from services import ground_truth
from services.ground_truth import GroundTruthError, build_docs, get_ground_truth, import_docs
from test_cases import FakeDB

MANAGER = {"uid": "m1", "role": "manager"}
READER1 = {"uid": "r1", "role": "reader"}
READER2 = {"uid": "r2", "role": "reader"}
TECH = {"uid": "t1", "role": "radiographer"}


def row(image_id, study_id, lesion, box=None):
    xmin, ymin, xmax, ymax = box or ("", "", "", "")
    return {"study_id": study_id, "series_id": "s", "image_id": image_id, "rad_id": "rad1",
            "lesion_type": lesion, "xmin": str(xmin), "ymin": str(ymin), "xmax": str(xmax), "ymax": str(ymax)}


IMAGES = collect_images([
    row("imp1", "st1", "Surgical implant", (10, 20, 50, 80)),
    row("imp1", "st1", "Surgical implant", (60, 20, 90, 60)),
    row("imp1", "st1", "Osteophytes", (1, 1, 5, 5)),     # 다른 소견 박스는 정답에 넣지 않음
    row("nor1", "st2", "No finding"),
])
PLAN = [{"image_id": "imp1", "split": "test"}, {"image_id": "nor1", "split": "train"}]


# ---------------------------------------------------------------- 문서 만들기
def test_build_docs():
    docs = build_docs(PLAN, IMAGES)
    assert docs["imp1"] == {
        "source": "VinDr-SpineXR 1.0.0", "study_id": "st1", "split": "test", "label": "implant",
        "class_name": "implant", "n_boxes": 2,
        "boxes": [{"xmin": 10.0, "ymin": 20.0, "xmax": 50.0, "ymax": 80.0},
                  {"xmin": 60.0, "ymin": 20.0, "xmax": 90.0, "ymax": 60.0}],
    }
    assert docs["nor1"]["label"] == "normal" and docs["nor1"]["boxes"] == [] and docs["nor1"]["split"] == "train"


def test_build_docs_unknown_image():
    with pytest.raises(GroundTruthError, match="없는 영상"):
        build_docs([{"image_id": "nope", "split": "train"}], IMAGES)


# ---------------------------------------------------------------- 저장
def test_import_docs_in_batches_and_idempotent(monkeypatch):
    monkeypatch.setattr(ground_truth, "BATCH_LIMIT", 2)
    db = FakeDB()
    docs = {f"img{i}": {"label": "normal", "boxes": []} for i in range(5)}
    assert import_docs(docs, db=db) == 5
    assert db.commits == 3                                 # 2 + 2 + 1
    assert set(db.data["ground_truth"]) == set(docs)
    import_docs(docs, db=db)                               # 다시 넣어도 같은 결과
    assert len(db.data["ground_truth"]) == 5


# ---------------------------------------------------------------- 읽기 (권한)
@pytest.fixture
def db():
    db = FakeDB()
    db.data = {
        "cases": {"C1": {"status": "배정", "assignee_uid": "r1"}},
        "images": {"I1": {"case_id": "C1", "sop_instance_uid": "imp1"},
                   "I2": {"case_id": "C1", "sop_instance_uid": None},
                   "I3": {"case_id": "C1", "sop_instance_uid": "not-in-gt"}},
        "ground_truth": build_docs(PLAN, IMAGES),
    }
    return db


@pytest.mark.parametrize("user", [MANAGER, TECH, READER1])
def test_get_ground_truth_allowed(db, user):
    gt = get_ground_truth(user, "I1", db=db)
    assert gt["id"] == "imp1" and gt["n_boxes"] == 2 and gt["boxes"][0]["xmin"] == 10.0


def test_other_reader_cannot_read(db):
    with pytest.raises(PermissionDenied, match="본인에게 배정된"):
        get_ground_truth(READER2, "I1", db=db)


@pytest.mark.parametrize("user", [None, {}, {"uid": "x", "role": "admin"}])
def test_login_required(db, user):
    with pytest.raises(PermissionDenied):
        get_ground_truth(user, "I1", db=db)


def test_image_without_ground_truth_returns_none(db):
    assert get_ground_truth(MANAGER, "I2", db=db) is None   # SOPInstanceUID 없음
    assert get_ground_truth(MANAGER, "I3", db=db) is None   # 정답을 넣지 않은 영상


def test_missing_image(db):
    with pytest.raises(GroundTruthError, match="찾을 수 없습니다"):
        get_ground_truth(MANAGER, "NOPE", db=db)


# ---------------------------------------------------------------- 가져오기 스크립트 (미리 보기)
def test_import_script_preview_does_not_touch_db(tmp_path, monkeypatch, capsys):
    import csv

    from scripts import import_ground_truth as script

    ann = tmp_path / "annotations"
    ann.mkdir()
    fields = list(row("a", "b", "c"))
    for name, rows in (("train.csv", [row("nor1", "st2", "No finding")]),
                       ("test.csv", [row("imp1", "st1", "Surgical implant", (10, 20, 50, 80))])):
        with open(ann / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    with open(tmp_path / "split.csv", "w", newline="", encoding="utf-8") as f:
        f.write("image_id,study_id,split,label,n_boxes\nimp1,st1,test,implant,1\nnor1,st2,train,normal,0\n")

    monkeypatch.setattr(script, "firestore_client", lambda: pytest.fail("미리 보기에서 DB에 접속하면 안 됨"))
    monkeypatch.setattr("sys.argv", ["import_ground_truth.py", "--src", str(tmp_path)])
    script.main()
    out = capsys.readouterr().out
    assert "정답 문서 2개 (박스 1개)" in out and "test: 임플란트 1 / 정상 0" in out and "--write" in out
