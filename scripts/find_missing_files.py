"""이 컴퓨터에 원본 파일이 없는 케이스를 찾고, 고른 케이스만 기록째 지운다 (Issue #36). 담당: A

노트북과 서버가 같은 Firestore 를 쓰는데 영상 파일은 등록한 컴퓨터에만 저장된다.
그래서 노트북에서 등록한 케이스는 서버 목록에 보이지만 뷰어에서 열리지 않는다.
케이스 등록은 서버 웹 화면에서만 하고, 이미 섞인 테스트 케이스는 이 스크립트로 정리한다.

실행 (반드시 서버의 프로젝트 폴더에서)
    venv/bin/python scripts/find_missing_files.py                          # 목록만 보여 줌 (DB 안 건드림)
    venv/bin/python scripts/find_missing_files.py --delete CASE-A CASE-B   # 고른 케이스 삭제 ('삭제' 입력 확인)

주의: 노트북에서 실행하면 서버에서 등록한 케이스가 '파일 없음'으로 보인다. 삭제는 서버에서만 한다.
"""
from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.access import original_path  # noqa: E402

SECRETS = ROOT / ".streamlit" / "secrets.toml"
# 케이스 번호(case_id)로 찾는 기록 / 영상 번호(image_id)를 문서 이름으로 쓰는 기록
BY_CASE = ("images", "notes", "case_history", "detection_reviews", "processing_logs")
BY_IMAGE = ("detections", "opencv_detections")
BATCH_LIMIT = 400  # Firestore 한 번에 쓰기 500개 제한보다 작게


def _where(db, collection, field, value):
    from google.cloud.firestore_v1.base_query import FieldFilter
    return db.collection(collection).where(filter=FieldFilter(field, "==", value)).stream()


def file_exists(image: dict, storage_root: Path) -> bool:
    try:
        return original_path(image, storage_root).is_file()
    except (ValueError, KeyError, TypeError):  # 경로 정보가 이상하면 열 수 없는 것으로 본다
        return False


def find_missing(db, storage_root: Path) -> list[dict]:
    """원본 파일이 하나라도 없는 케이스 (영상 기록이 없는 케이스 포함). 오래된 순."""
    missing = []
    for snap in db.collection("cases").stream():
        case = snap.to_dict() or {}
        images = [s.to_dict() or {} for s in _where(db, "images", "case_id", snap.id)]
        lost = sum(not file_exists(image, storage_root) for image in images)
        if lost or not images:
            missing.append({"case_id": snap.id, "status": case.get("status"), "created_at": case.get("created_at"),
                            "created_by": case.get("created_by"), "images": len(images), "missing": lost})
    return sorted(missing, key=lambda c: str(c["created_at"] or ""))


def related_refs(db, case_id: str) -> list:
    """케이스 하나를 지울 때 함께 지울 문서 (케이스·영상·탐지·메모·이력·박스 판정·처리 기록)."""
    refs = [db.collection("cases").document(case_id)]
    for collection in BY_CASE:
        for snap in _where(db, collection, "case_id", case_id):
            refs.append(db.collection(collection).document(snap.id))
            row = snap.to_dict() or {}
            if collection == "images":
                refs += [db.collection(c).document(snap.id) for c in BY_IMAGE]
            if collection == "processing_logs" and row.get("run_key"):
                refs.append(db.collection("processing_keys").document(row["run_key"]))
    return refs


def delete_cases(db, case_ids: list[str]) -> int:
    """고른 케이스의 기록을 지운다. 없는 문서를 지워도 오류가 나지 않는다. 지운 문서 수를 돌려준다."""
    refs = [ref for case_id in case_ids for ref in related_refs(db, case_id)]
    for start in range(0, len(refs), BATCH_LIMIT):
        batch = db.batch()
        for ref in refs[start:start + BATCH_LIMIT]:
            batch.delete(ref)
        batch.commit()
    return len(refs)


def load_storage_root() -> Path:
    if not SECRETS.exists():
        sys.exit(f"{SECRETS} 가 없습니다. 서버의 프로젝트 폴더에서 실행하세요.")
    return Path(tomllib.loads(SECRETS.read_text(encoding="utf-8"))["storage"]["image_dir"])


def main(argv=None, *, db=None, storage_root: Path | None = None, ask=input) -> list[dict]:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--delete", nargs="+", metavar="CASE_ID", help="목록에 나온 케이스 중 지울 케이스 번호")
    args = ap.parse_args(argv)

    if storage_root is None:
        storage_root = load_storage_root()
    if not Path(storage_root).is_dir():
        sys.exit(f"영상 저장 폴더 {storage_root} 가 없습니다. 서버에서 실행했는지 확인하세요.")
    if db is None:
        from scripts.import_ground_truth import firestore_client
        db = firestore_client()

    missing = find_missing(db, Path(storage_root))
    print(f"영상 저장 폴더: {storage_root}")
    if not missing:
        print("원본 파일이 없는 케이스가 없습니다.")
    else:
        print(f"원본 파일이 없는 케이스 {len(missing)}개 (오래된 순)")
        for c in missing:
            print(f"  {c['case_id']}  상태 {c['status']}  등록 {c['created_at']}  "
                  f"등록자 {c['created_by']}  영상 {c['images']}개 중 {c['missing']}개 없음")

    if not args.delete:
        if missing:
            print("\n지우려면: --delete 케이스번호 ... (서버에서만)")
        return missing

    allowed = {c["case_id"] for c in missing}
    wrong = [i for i in args.delete if i not in allowed]
    if wrong:
        sys.exit(f"목록에 없는 케이스는 지울 수 없습니다 (파일이 있거나 없는 번호): {', '.join(wrong)}")
    print(f"\n지울 케이스 {len(args.delete)}개: {', '.join(args.delete)}")
    print("케이스·영상·탐지·메모·리포트·이력·박스 판정·처리 기록이 함께 지워지고 되돌릴 수 없습니다.")
    if ask("계속하려면 '삭제' 를 입력하세요: ").strip() != "삭제":
        print("취소했습니다. 아무것도 지우지 않았습니다.")
        return missing
    print(f"삭제 완료: 문서 {delete_cases(db, args.delete)}개")
    return missing


if __name__ == "__main__":
    main()
