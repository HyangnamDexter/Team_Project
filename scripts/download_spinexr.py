"""VinDr-SpineXR(PhysioNet)에서 받을 영상 목록(urls.txt)을 만든다.

PhysioNet은 파이썬·curl 요청을 막기 때문에, 실제 다운로드는 공식 안내 방법인 wget으로 한다.
준비: PhysioNet 가입 + 데이터 사용 동의서(DUA) 서명 (팀원 각자), wget 설치
      (Windows: winget install --id GNU.Wget2 -e 로 설치 후 터미널 재시작, 명령 이름은 wget2)

순서 (프로젝트 폴더에서)
1) 라벨 CSV 받기
   wget2 -N -x -nH --cut-dirs=3 -P data/vindr-spinexr --user <아이디> --ask-password https://physionet.org/files/vindr-spinexr/1.0.0/annotations/train.csv https://physionet.org/files/vindr-spinexr/1.0.0/annotations/test.csv
2) 목록 만들기
   python scripts/download_spinexr.py
3) 영상 받기
   wget2 -N -c -x -nH --cut-dirs=3 --max-threads=1 --wait=1 -P data/vindr-spinexr --user <아이디> --ask-password -i data/vindr-spinexr/urls_train.txt

- 임플란트 영상과 정상(No finding) 영상을 같은 수만큼 고른다 (기본 50장씩).
- 고정된 seed로 고르므로 팀원 모두 같은 영상 목록을 받는다.
- 저장 위치: data/vindr-spinexr/ (.gitignore 처리됨, 절대 커밋·공유 금지)
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

BASE = "https://physionet.org/files/vindr-spinexr/1.0.0/"
ROOT = Path(__file__).resolve().parent.parent / "data" / "vindr-spinexr"
IMPLANT = "Surgical implant"
NORMAL = "No finding"


def pick(csv_path: Path, n_implant: int, n_normal: int, seed: int) -> list[str]:
    """임플란트 영상 n_implant장, 정상 영상 n_normal장의 image_id를 고른다."""
    implant, normal = set(), set()
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["lesion_type"] == IMPLANT:
                implant.add(row["image_id"])
            elif row["lesion_type"] == NORMAL:
                normal.add(row["image_id"])
    normal -= implant
    rng = random.Random(seed)
    chosen = rng.sample(sorted(implant), min(n_implant, len(implant)))
    chosen += rng.sample(sorted(normal), min(n_normal, len(normal)))
    return chosen


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["train", "test"], default="train")
    ap.add_argument("--implant", type=int, default=50, help="임플란트 영상 수 (기본 50)")
    ap.add_argument("--normal", type=int, default=50, help="정상 영상 수 (기본 50)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--root", type=Path, default=ROOT, help="데이터 폴더 (기본 data/vindr-spinexr)")
    args = ap.parse_args()

    csv_path = args.root / "annotations" / f"{args.split}.csv"
    if not csv_path.exists():
        sys.exit(f"{csv_path} 가 없습니다. 먼저 1) 라벨 CSV 받기를 실행하세요.")

    ids = pick(csv_path, args.implant, args.normal, args.seed)
    urls = [f"{BASE}{args.split}_images/{i}.dicom" for i in ids]
    out = args.root / f"urls_{args.split}.txt"
    out.write_text("\n".join(urls) + "\n", encoding="utf-8")
    print(f"{len(urls)}장 목록 저장: {out}  (예상 용량 약 {len(urls) * 3.2:.0f}MB)")
    print("다음 명령으로 받으세요:")
    print(f"wget2 -N -c -x -nH --cut-dirs=3 --max-threads=1 --wait=1 -P data/vindr-spinexr --user <아이디> --ask-password -i {out.relative_to(Path.cwd()) if out.is_relative_to(Path.cwd()) else out}")


if __name__ == "__main__":
    main()
