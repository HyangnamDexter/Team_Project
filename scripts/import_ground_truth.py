"""VinDr-SpineXR 정답 박스를 Firestore ground_truth 컬렉션에 넣는다. 담당: A

필요한 파일 (prepare_spinexr.py plan 을 먼저 실행)
- data/vindr-spinexr/annotations/train.csv, test.csv  (라벨)
- data/vindr-spinexr/split.csv                         (학습에 쓰는 642장과 train/val/test 분할)
- .streamlit/secrets.toml 의 [firebase] service_account_path (서비스 계정 키)

실행 (프로젝트 폴더)
    python scripts/import_ground_truth.py            # 미리 보기: 무엇을 넣을지 개수만 보여 줌 (DB 안 건드림)
    python scripts/import_ground_truth.py --write    # 실제로 저장

같은 영상은 덮어쓰므로 여러 번 실행해도 결과가 같다.
라벨 CSV 는 저장소에 올리지 않는다 (data/ 는 .gitignore, 데이터 동의서).
"""
from __future__ import annotations

import argparse
import sys
import tomllib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.prepare_spinexr import SRC, collect_images, read_rows  # noqa: E402
from services.ground_truth import COLLECTION, build_docs, import_docs  # noqa: E402

SECRETS = ROOT / ".streamlit" / "secrets.toml"


def load_docs(src: Path) -> dict[str, dict]:
    ann, split_csv = src / "annotations", src / "split.csv"
    for path in (ann / "train.csv", ann / "test.csv", split_csv):
        if not path.exists():
            sys.exit(f"{path} 가 없습니다. 라벨 CSV를 받고 prepare_spinexr.py plan 을 먼저 실행하세요.")
    images = {**collect_images(read_rows(ann / "train.csv")), **collect_images(read_rows(ann / "test.csv"))}
    return build_docs(read_rows(split_csv), images)


def firestore_client():
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not SECRETS.exists():
        sys.exit(f"{SECRETS} 가 없습니다. secrets.toml.example 을 참고해 만드세요.")
    key_path = tomllib.loads(SECRETS.read_text(encoding="utf-8"))["firebase"]["service_account_path"]
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(key_path))
    return firestore.client()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=SRC, help="VinDr 폴더 (annotations/, split.csv 가 있는 곳)")
    ap.add_argument("--write", action="store_true", help="실제로 Firestore 에 저장 (없으면 미리 보기만)")
    args = ap.parse_args()

    docs = load_docs(args.src)
    count = Counter((d["split"], d["label"]) for d in docs.values())
    n_boxes = sum(d["n_boxes"] for d in docs.values())
    print(f"정답 문서 {len(docs)}개 (박스 {n_boxes}개)")
    for split in ("train", "val", "test"):
        print(f"  {split}: 임플란트 {count[(split, 'implant')]} / 정상 {count[(split, 'normal')]}")

    if not args.write:
        print(f"미리 보기만 했습니다. 저장하려면 --write 를 붙여 실행하세요. (컬렉션: {COLLECTION})")
        return
    saved = import_docs(docs, db=firestore_client())
    print(f"저장 완료: {COLLECTION} 컬렉션에 {saved}개")


if __name__ == "__main__":
    main()
