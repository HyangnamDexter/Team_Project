"""VinDr-SpineXR 로 임플란트 탐지 모델 학습 데이터를 만든다 (YOLO 형식).

두 단계로 나눠 쓴다. 1단계는 라벨 CSV만 있으면 되고, 2단계에 영상(DICOM)이 필요하다.

1) plan: 어떤 영상을 학습·검증·평가에 쓸지 정하고, 받아야 할 영상 목록을 만든다
   python scripts/prepare_spinexr.py plan
   → data/vindr-spinexr/split.csv          (image_id, study_id, split, label, n_boxes)
   → data/vindr-spinexr/urls_split.txt     (wget2 -i 로 받을 영상 주소)

2) convert: 받은 DICOM을 PNG + YOLO 라벨로 바꾼다 (없는 영상은 건너뛰고 개수를 알려 줌)
   python scripts/prepare_spinexr.py convert
   → data/vindr-yolo/images/{train,val,test}/<image_id>.png
   → data/vindr-yolo/labels/{train,val,test}/<image_id>.txt
   → data/vindr-yolo/dataset.yaml

규칙
- 소견은 "Surgical implant" 하나만 쓴다 (클래스 0 = implant)
- 정상("No finding") 영상도 섞는다. 라벨 파일은 비어 있다 (오검출을 줄이는 데 필요)
- train.csv 는 검사(study_id) 단위로 train/val 로 나눈다. 같은 검사의 영상이 양쪽에 들어가지 않는다
- test.csv 는 그대로 test 로 쓴다 (데이터셋 제공자가 나눠 둔 평가용)
- seed 가 같으면 누가 실행해도 같은 결과가 나온다
- 영상은 저장소에 올리지 않는다 (data/ 는 .gitignore). 동의서상 팀원 각자 받는다
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE_URL = "https://physionet.org/files/vindr-spinexr/1.0.0/"
SRC = ROOT / "data" / "vindr-spinexr"
OUT = ROOT / "data" / "vindr-yolo"
IMPLANT = "Surgical implant"
NORMAL = "No finding"
CLASS_NAMES = ["implant"]
SPLITS = ("train", "val", "test")


# ---------------------------------------------------------------- 1단계: 고르기·나누기
def read_rows(csv_path: Path) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def collect_images(rows: list[dict]) -> dict[str, dict]:
    """CSV 행(박스 1개 = 1행)을 영상 단위로 묶는다.

    반환: {image_id: {"study_id", "label": "implant"|"normal"|"other", "boxes": [(xmin, ymin, xmax, ymax), ...]}}
    - 임플란트 박스가 하나라도 있으면 implant (다른 소견이 함께 있어도)
    - "No finding" 만 있으면 normal
    - 그 밖의 소견만 있으면 other (이번 학습에는 쓰지 않음)
    """
    images: dict[str, dict] = {}
    for r in rows:
        img = images.setdefault(r["image_id"], {"study_id": r["study_id"], "types": set(), "boxes": []})
        img["types"].add(r["lesion_type"])
        if r["lesion_type"] == IMPLANT:
            img["boxes"].append(tuple(float(r[k]) for k in ("xmin", "ymin", "xmax", "ymax")))
    for img in images.values():
        types = img.pop("types")
        if img["boxes"]:
            img["label"] = "implant"
        elif types == {NORMAL}:
            img["label"] = "normal"
        else:
            img["label"] = "other"
    return images


def pick(images: dict[str, dict], normal_per_implant: float, seed: int) -> dict[str, dict]:
    """임플란트 영상은 모두, 정상 영상은 임플란트 수 × normal_per_implant 장만 고른다."""
    implant = sorted(i for i, v in images.items() if v["label"] == "implant")
    normal = sorted(i for i, v in images.items() if v["label"] == "normal")
    n_normal = min(len(normal), round(len(implant) * normal_per_implant))
    chosen = implant + random.Random(seed).sample(normal, n_normal)
    return {i: images[i] for i in chosen}


def split_by_study(images: dict[str, dict], val_ratio: float, seed: int) -> dict[str, str]:
    """검사(study_id) 단위로 train/val 을 나눈다. 임플란트 검사와 정상 검사를 따로 섞어 비율을 맞춘다.

    반환: {image_id: "train" | "val"}
    """
    if not 0 < val_ratio < 1:
        raise ValueError(f"val_ratio 는 0과 1 사이여야 합니다. (입력값: {val_ratio})")
    studies: dict[str, list[str]] = defaultdict(list)
    for image_id, v in images.items():
        studies[v["study_id"]].append(image_id)

    def has_implant(study):
        return any(images[i]["label"] == "implant" for i in studies[study])

    rng = random.Random(seed)
    result: dict[str, str] = {}
    for group in (sorted(s for s in studies if has_implant(s)), sorted(s for s in studies if not has_implant(s))):
        rng.shuffle(group)
        n_val = round(len(group) * val_ratio)
        for k, study in enumerate(group):
            for image_id in studies[study]:
                result[image_id] = "val" if k < n_val else "train"
    return result


def make_plan(train_rows: list[dict], test_rows: list[dict], normal_per_implant: float = 1.0,
              val_ratio: float = 0.2, seed: int = 42) -> list[dict]:
    """학습·검증·평가에 쓸 영상 목록. 행 = {"image_id", "study_id", "split", "label", "n_boxes"}"""
    plan = []
    train_imgs = pick(collect_images(train_rows), normal_per_implant, seed)
    for image_id, split in sorted(split_by_study(train_imgs, val_ratio, seed).items()):
        v = train_imgs[image_id]
        plan.append({"image_id": image_id, "study_id": v["study_id"], "split": split,
                     "label": v["label"], "n_boxes": len(v["boxes"])})
    test_imgs = pick(collect_images(test_rows), normal_per_implant, seed)
    for image_id in sorted(test_imgs):
        v = test_imgs[image_id]
        plan.append({"image_id": image_id, "study_id": v["study_id"], "split": "test",
                     "label": v["label"], "n_boxes": len(v["boxes"])})
    return plan


def image_url(row: dict) -> str:
    folder = "test_images" if row["split"] == "test" else "train_images"
    return f"{BASE_URL}{folder}/{row['image_id']}.dicom"


# ---------------------------------------------------------------- 2단계: 변환
def to_yolo_line(box: tuple[float, float, float, float], width: int, height: int, cls: int = 0) -> str | None:
    """(xmin, ymin, xmax, ymax) 픽셀 좌표 → YOLO "cls cx cy w h" (0~1 정규화).

    영상 밖으로 나간 부분은 잘라 내고, 잘라 낸 뒤 크기가 0이면 None.
    """
    if width <= 0 or height <= 0:
        raise ValueError("영상 크기는 1 이상이어야 합니다.")
    xmin, ymin, xmax, ymax = box
    xmin, xmax = max(0.0, min(xmin, xmax)), min(float(width), max(xmin, xmax))
    ymin, ymax = max(0.0, min(ymin, ymax)), min(float(height), max(ymin, ymax))
    if xmax - xmin <= 0 or ymax - ymin <= 0:
        return None
    cx, cy = (xmin + xmax) / 2 / width, (ymin + ymax) / 2 / height
    w, h = (xmax - xmin) / width, (ymax - ymin) / height
    return f"{cls} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"


def dicom_to_png(dicom_path: Path, png_path: Path, max_size: int | None = None) -> tuple[int, int]:
    """DICOM → 화면용 8bit PNG (팀 공통 load_dicom + 자동 윈도우). 원본 (가로, 세로) 반환.

    max_size 를 주면 긴 변을 그 크기로 줄여 저장한다. YOLO 라벨은 0~1 비율이라 크기를 바꿔도 그대로 맞다.
    """
    import cv2

    from core.dicom_io import apply_window, load_dicom

    pixels, _ = load_dicom(str(dicom_path))
    img = apply_window(pixels)  # 백분위 자동 윈도우 → uint8
    height, width = img.shape
    if max_size and max(height, width) > max_size:
        scale = max_size / max(height, width)
        img = cv2.resize(img, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(png_path), img):
        raise OSError(f"PNG 저장 실패: {png_path}")
    return width, height


def write_dataset_yaml(out_dir: Path) -> Path:
    path = out_dir / "dataset.yaml"
    names = "\n".join(f"  {i}: {n}" for i, n in enumerate(CLASS_NAMES))
    path.write_text(
        f"# scripts/prepare_spinexr.py 가 만든 파일\npath: {out_dir.as_posix()}\n"
        f"train: images/train\nval: images/val\ntest: images/test\nnames:\n{names}\n",
        encoding="utf-8",
    )
    return path


def convert(plan: list[dict], boxes: dict[str, list], src_dir: Path, out_dir: Path,
            max_size: int | None = None) -> dict:
    """plan 의 영상을 PNG + YOLO 라벨로 바꾼다. 받지 않은 영상은 건너뛴다.

    boxes: {image_id: [(xmin, ymin, xmax, ymax), ...]} (collect_images 결과에서 꺼낸 것)
    반환: {"converted": {split: 개수}, "missing": 개수, "failed": [image_id, ...]}
    """
    converted = {s: 0 for s in SPLITS}
    missing, failed = 0, []
    for row in plan:
        folder = "test_images" if row["split"] == "test" else "train_images"
        dicom_path = src_dir / folder / f"{row['image_id']}.dicom"
        if not dicom_path.exists():
            missing += 1
            continue
        png_path = out_dir / "images" / row["split"] / f"{row['image_id']}.png"
        try:
            width, height = dicom_to_png(dicom_path, png_path, max_size)
        except Exception:  # 읽을 수 없는 파일은 목록에 남기고 계속 진행
            failed.append(row["image_id"])
            continue
        lines = [line for b in boxes.get(row["image_id"], []) if (line := to_yolo_line(b, width, height))]
        label_path = out_dir / "labels" / row["split"] / f"{row['image_id']}.txt"
        label_path.parent.mkdir(parents=True, exist_ok=True)
        label_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        converted[row["split"]] += 1
    write_dataset_yaml(out_dir)
    return {"converted": converted, "missing": missing, "failed": failed}


# ---------------------------------------------------------------- 실행
PLAN_FIELDS = ["image_id", "study_id", "split", "label", "n_boxes"]


def summarize(plan: list[dict]) -> str:
    count = defaultdict(int)
    for r in plan:
        count[(r["split"], r["label"])] += 1
    return "  ".join(f"{s}: 임플란트 {count[(s, 'implant')]} / 정상 {count[(s, 'normal')]}" for s in SPLITS)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("plan", help="라벨 CSV로 데이터 분할과 다운로드 목록 만들기")
    p1.add_argument("--normal-per-implant", type=float, default=1.0, help="임플란트 1장당 정상 영상 수 (기본 1.0)")
    p1.add_argument("--val-ratio", type=float, default=0.2, help="train 중 검증용 검사 비율 (기본 0.2)")
    p1.add_argument("--seed", type=int, default=42)
    p2 = sub.add_parser("convert", help="받은 DICOM을 PNG + YOLO 라벨로 바꾸기")
    p2.add_argument("--max-size", type=int, default=1024, help="긴 변 최대 픽셀 (기본 1024, 0이면 원본 크기)")
    for p in (p1, p2):
        p.add_argument("--src", type=Path, default=SRC, help="VinDr 폴더 (annotations/, train_images/ 가 있는 곳)")
    p2.add_argument("--out", type=Path, default=OUT, help="YOLO 데이터 폴더")
    args = ap.parse_args()

    ann = args.src / "annotations"
    for name in ("train.csv", "test.csv"):
        if not (ann / name).exists():
            sys.exit(f"{ann / name} 가 없습니다. 먼저 download_spinexr.py 안내대로 라벨 CSV를 받으세요.")
    train_rows, test_rows = read_rows(ann / "train.csv"), read_rows(ann / "test.csv")
    split_csv = args.src / "split.csv"

    if args.cmd == "plan":
        plan = make_plan(train_rows, test_rows, args.normal_per_implant, args.val_ratio, args.seed)
        with open(split_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=PLAN_FIELDS)
            w.writeheader()
            w.writerows(plan)
        urls = args.src / "urls_split.txt"
        urls.write_text("\n".join(image_url(r) for r in plan) + "\n", encoding="utf-8")
        print(f"분할 저장: {split_csv}\n  {summarize(plan)}")
        print(f"다운로드 목록: {urls} ({len(plan)}장, 약 {len(plan) * 3.2 / 1024:.1f}GB)")
        print("wget2 -N -c -x -nH --cut-dirs=3 --max-threads=1 --wait=1 -P data/vindr-spinexr "
              "--user <아이디> --ask-password -i data/vindr-spinexr/urls_split.txt")
        return

    if not split_csv.exists():
        sys.exit(f"{split_csv} 가 없습니다. 먼저 plan 을 실행하세요.")
    plan = read_rows(split_csv)
    boxes = {i: v["boxes"] for rows in (train_rows, test_rows) for i, v in collect_images(rows).items()}
    result = convert(plan, boxes, args.src, args.out, args.max_size or None)
    print(f"변환 완료: {result['converted']}  (받지 않아 건너뜀 {result['missing']}장, 실패 {len(result['failed'])}장)")
    if result["failed"]:
        print("실패한 영상:", ", ".join(result["failed"][:10]), "..." if len(result["failed"]) > 10 else "")
    print(f"학습 설정 파일: {args.out / 'dataset.yaml'}")


if __name__ == "__main__":
    main()
