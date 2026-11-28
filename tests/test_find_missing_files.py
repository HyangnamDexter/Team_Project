"""scripts/find_missing_files.py 테스트 (Issue #36). 가짜 DB 와 임시 저장 폴더로 확인한다."""
import pytest

from scripts.find_missing_files import delete_cases, find_missing, main


class Snap:
    def __init__(self, doc_id, data):
        self.id, self._data = doc_id, data

    def to_dict(self):
        return dict(self._data)


class Ref:
    def __init__(self, db, col, doc_id):
        self.db, self.col, self.id = db, col, doc_id


class Query:
    def __init__(self, db, col, filters=()):
        self.db, self.col, self.filters = db, col, list(filters)

    def document(self, doc_id):
        return Ref(self.db, self.col, doc_id)

    def where(self, *, filter):
        return Query(self.db, self.col, self.filters + [(filter.field_path, filter.value)])

    def stream(self):
        return iter([Snap(i, d) for i, d in self.db.data.get(self.col, {}).items()
                     if all(d.get(f) == v for f, v in self.filters)])


class Batch:
    def __init__(self, db):
        self.db, self.refs = db, []

    def delete(self, ref):
        self.refs.append(ref)

    def commit(self):
        self.db.commits += 1
        for ref in self.refs:
            self.db.data.get(ref.col, {}).pop(ref.id, None)  # 없는 문서를 지워도 오류 없음 (Firestore 와 같음)


class FakeDB:
    def __init__(self):
        self.data, self.commits = {}, 0

    def collection(self, name):
        return Query(self, name)

    def batch(self):
        return Batch(self)

    def add(self, col, doc_id, **doc):
        self.data.setdefault(col, {})[doc_id] = doc


def add_case(db, root, case_id, image_id, *, with_file, created_at):
    db.add("cases", case_id, status="접수", created_at=created_at, created_by="t1")
    db.add("images", image_id, case_id=case_id, original_dir=f"{case_id}/{image_id}/original",
           slice_files=["0001.dcm"])
    db.add("detections", image_id, case_id=case_id, status="done")
    db.add("case_history", f"h_{case_id}", case_id=case_id, to_status="접수")
    if with_file:
        f = root / case_id / image_id / "original" / "0001.dcm"
        f.parent.mkdir(parents=True)
        f.write_bytes(b"dicom")


@pytest.fixture
def setup(tmp_path):
    db, root = FakeDB(), tmp_path / "storage"
    root.mkdir()
    add_case(db, root, "SERVER", "I1", with_file=True, created_at="2026-11-27")
    add_case(db, root, "LAPTOP", "I2", with_file=False, created_at="2026-11-26")
    # 노트북 케이스에 딸린 기록들 (함께 지워져야 함)
    db.add("notes", "N1", case_id="LAPTOP", type="report")
    db.add("detection_reviews", "R1", case_id="LAPTOP")
    db.add("processing_logs", "L1", case_id="LAPTOP", run_key="K1")
    db.add("processing_keys", "K1", log_id="L1")
    db.add("opencv_detections", "I2", case_id="LAPTOP")
    db.add("notes", "N2", case_id="SERVER", type="memo")
    db.add("ground_truth", "1.2.3", boxes=[])
    db.add("users", "t1", role="radiographer")
    return db, root


def test_find_missing_lists_only_cases_without_files(setup):
    db, root = setup
    found = find_missing(db, root)
    assert [c["case_id"] for c in found] == ["LAPTOP"]
    assert found[0]["images"] == 1 and found[0]["missing"] == 1


def test_case_without_image_record_and_bad_path_are_missing(setup):
    db, root = setup
    db.add("cases", "EMPTY", status="접수", created_at="2026-11-25")
    db.add("cases", "BAD", status="접수", created_at="2026-11-24")
    db.add("images", "I9", case_id="BAD", original_dir="../../etc", slice_files=["0001.dcm"])  # 저장 폴더 밖
    assert [c["case_id"] for c in find_missing(db, root)] == ["BAD", "EMPTY", "LAPTOP"]


def test_delete_removes_case_and_all_related_records_only(setup):
    db, root = setup
    deleted = delete_cases(db, ["LAPTOP"])
    assert deleted == 9
    for col, doc_id in [("cases", "LAPTOP"), ("images", "I2"), ("detections", "I2"), ("opencv_detections", "I2"),
                        ("notes", "N1"), ("case_history", "h_LAPTOP"), ("detection_reviews", "R1"),
                        ("processing_logs", "L1"), ("processing_keys", "K1")]:
        assert doc_id not in db.data[col], (col, doc_id)
    # 다른 케이스와 공용 기록은 그대로
    assert "SERVER" in db.data["cases"] and "I1" in db.data["images"] and "N2" in db.data["notes"]
    assert "1.2.3" in db.data["ground_truth"] and "t1" in db.data["users"]


def test_delete_splits_into_batches(setup, monkeypatch):
    import scripts.find_missing_files as script
    db, root = setup
    monkeypatch.setattr(script, "BATCH_LIMIT", 3)
    assert delete_cases(db, ["LAPTOP"]) == 9 and db.commits == 3


def test_main_preview_does_not_write(setup, capsys):
    db, root = setup
    main([], db=db, storage_root=root)
    out = capsys.readouterr().out
    assert "LAPTOP" in out and "SERVER" not in out and db.commits == 0


def test_main_delete_needs_confirmation(setup, capsys):
    db, root = setup
    main(["--delete", "LAPTOP"], db=db, storage_root=root, ask=lambda _: "아니오")
    assert "LAPTOP" in db.data["cases"] and db.commits == 0
    assert "취소했습니다" in capsys.readouterr().out
    main(["--delete", "LAPTOP"], db=db, storage_root=root, ask=lambda _: "삭제")
    assert "LAPTOP" not in db.data["cases"] and "SERVER" in db.data["cases"]


def test_main_refuses_case_that_has_file(setup):
    db, root = setup
    with pytest.raises(SystemExit, match="목록에 없는 케이스"):
        main(["--delete", "SERVER"], db=db, storage_root=root, ask=lambda _: "삭제")
    assert "SERVER" in db.data["cases"] and db.commits == 0


def test_main_refuses_missing_storage_folder(setup, tmp_path):
    db, _ = setup
    with pytest.raises(SystemExit, match="영상 저장 폴더"):
        main([], db=db, storage_root=tmp_path / "nope")
