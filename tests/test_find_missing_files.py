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
        if self.db.fail_on_commit == self.db.commits:   # 지정한 번째 배치는 아무것도 지우지 못하고 실패 (원자적)
            raise ConnectionError("network down")
        for ref in self.refs:
            self.db.data.get(ref.col, {}).pop(ref.id, None)  # 없는 문서를 지워도 오류 없음 (Firestore 와 같음)


class FakeDB:
    def __init__(self):
        self.data, self.commits, self.fail_on_commit = {}, 0, None

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


# ---------------------------------------------------------------- 리뷰 반영 (PR #47): 중간 실패 후 재시도, 삭제 직전 재검사
LAPTOP_DOCS = [("cases", "LAPTOP"), ("images", "I2"), ("detections", "I2"), ("opencv_detections", "I2"),
               ("notes", "N1"), ("case_history", "h_LAPTOP"), ("detection_reviews", "R1"),
               ("processing_logs", "L1"), ("processing_keys", "K1")]


def remaining(db):
    return [(c, i) for c, i in LAPTOP_DOCS if i in db.data.get(c, {})]


def test_delete_order_keeps_lookup_keys_until_last(setup):
    db, root = setup
    from scripts.find_missing_files import related_refs
    order = [(r.col, r.id) for r in related_refs(db, "LAPTOP")]
    pos = {key: n for n, key in enumerate(order)}
    assert order[-1] == ("cases", "LAPTOP")                                   # 케이스는 맨 마지막
    assert pos[("images", "I2")] > pos[("detections", "I2")]                 # 영상은 탐지 결과보다 나중
    assert pos[("images", "I2")] > pos[("opencv_detections", "I2")]
    assert pos[("processing_logs", "L1")] > pos[("processing_keys", "K1")]  # 처리 기록은 열쇠 문서보다 나중


@pytest.mark.parametrize("fail_on", [1, 2, 3])
def test_failed_batch_can_be_retried_without_orphans(setup, monkeypatch, fail_on, capsys):
    import scripts.find_missing_files as script
    db, root = setup
    monkeypatch.setattr(script, "BATCH_LIMIT", 3)   # 9개 문서 → 배치 3번
    db.fail_on_commit = fail_on
    with pytest.raises(SystemExit, match="다시 실행하면"):
        main(["--delete", "LAPTOP"], db=db, storage_root=root, ask=lambda _: "삭제")
    assert ("cases", "LAPTOP") in remaining(db)                  # 케이스가 남아 있어 목록에 다시 나온다
    assert "LAPTOP" in [c["case_id"] for c in find_missing(db, root)]
    db.fail_on_commit = None
    main(["--delete", "LAPTOP"], db=db, storage_root=root, ask=lambda _: "삭제")   # 같은 명령으로 재시도
    assert remaining(db) == []                                   # 고아 기록 없이 모두 삭제
    assert "SERVER" in db.data["cases"] and "N2" in db.data["notes"]


def test_second_case_failure_keeps_first_done_and_second_retryable(setup, monkeypatch):
    import scripts.find_missing_files as script
    db, root = setup
    add_case(db, root, "LAPTOP2", "I3", with_file=False, created_at="2026-11-26")
    monkeypatch.setattr(script, "BATCH_LIMIT", 400)
    db.fail_on_commit = 2                                        # 첫 케이스 성공, 둘째 케이스 실패
    with pytest.raises(SystemExit):
        main(["--delete", "LAPTOP", "LAPTOP2"], db=db, storage_root=root, ask=lambda _: "삭제")
    assert remaining(db) == [] and "LAPTOP2" in db.data["cases"]
    db.fail_on_commit = None
    main(["--delete", "LAPTOP2"], db=db, storage_root=root, ask=lambda _: "삭제")
    assert "LAPTOP2" not in db.data["cases"] and "I3" not in db.data["detections"]


def test_file_restored_during_confirmation_cancels_delete(setup):
    db, root = setup

    def restore_then_confirm(_):
        f = root / "LAPTOP" / "I2" / "original" / "0001.dcm"   # 확인을 기다리는 동안 파일이 복구됨
        f.parent.mkdir(parents=True)
        f.write_bytes(b"dicom")
        return "삭제"

    with pytest.raises(SystemExit, match="상태가 바뀌어 아무것도 지우지 않았습니다"):
        main(["--delete", "LAPTOP"], db=db, storage_root=root, ask=restore_then_confirm)
    assert remaining(db) == LAPTOP_DOCS and db.commits == 0


def test_case_removed_during_confirmation_cancels_all(setup):
    db, root = setup
    add_case(db, root, "LAPTOP2", "I3", with_file=False, created_at="2026-11-26")

    def someone_deleted_one(_):
        db.data["cases"].pop("LAPTOP2")
        return "삭제"

    with pytest.raises(SystemExit, match="LAPTOP2"):
        main(["--delete", "LAPTOP", "LAPTOP2"], db=db, storage_root=root, ask=someone_deleted_one)
    assert remaining(db) == LAPTOP_DOCS and db.commits == 0      # 하나라도 바뀌면 전부 중단
