"""탐지 모델 결과를 정답과 겹쳐 그려서 눈과 숫자로 확인한다. 담당: A

초록 = 정답(VinDr 라벨), 빨강 = 모델이 찾은 박스(점수 표시)
그림은 data/check_detection/ 에 저장한다 (data/ 는 .gitignore. VinDr 영상이 들어 있으므로 공유·커밋 금지)

실행 (프로젝트 폴더)
    python scripts/check_detection.py                         # 받아 둔 val 영상 20장
    python scripts/check_detection.py --split test --limit 0  # test 전부 (0 = 제한 없음)
    python scripts/check_detection.py 경로/파일.dicom ...      # 특정 파일
    python scripts/check_detection.py --model models/best.onnx --conf 0.25

모델 파일이 없으면 정답만 그린다 (영상·라벨 연결 확인용).
숫자는 C 의 평가 코드와 같은 규칙: 점수 높은 예측부터 가장 많이 겹치는 정답과 IoU 0.5 이상이면 1:1 로 맞힘.
라벨 CSV에 없는 영상은 정상(정답 0개)으로 세지 않고 평가에서 뺀다. 정상 오탐률은 "No finding" 영상만 센다.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.prepare_spinexr import SRC, collect_images, read_rows  # noqa: E402

OUT = ROOT / "data" / "check_detection"
MODEL = ROOT / "models" / "implant.onnx"
GREEN, RED = (0, 200, 0), (0, 0, 255)  # OpenCV 는 BGR
IOU_MATCH = 0.5


# ---------------------------------------------------------------- 맞힘 판정
def iou(a: dict, b: dict) -> float:
    ix = max(0.0, min(a["xmax"], b["xmax"]) - max(a["xmin"], b["xmin"]))
    iy = max(0.0, min(a["ymax"], b["ymax"]) - max(a["ymin"], b["ymin"]))
    inter = ix * iy
    union = ((a["xmax"] - a["xmin"]) * (a["ymax"] - a["ymin"])
             + (b["xmax"] - b["xmin"]) * (b["ymax"] - b["ymin"]) - inter)
    return inter / union if union > 0 else 0.0


def match(preds: list[dict], gts: list[dict], threshold: float = IOU_MATCH) -> dict:
    """C 의 평가 코드와 같은 규칙 (scripts/eval_experiments.py 의 iou50, 학습 노트북의 YOLO 평가).

    점수 높은 예측부터, **모든 정답 중** IoU 가 가장 큰 정답 하나를 고른다 (같으면 앞 번호).
    그 IoU 가 threshold 이상이고 아직 안 맞힌 정답이면 맞힘, 아니면 오검출이다.
    가장 많이 겹치는 정답을 이미 다른 예측이 맞혔다면, 다른 정답과 threshold 이상 겹쳐도 오검출로 센다.
    반환: {"tp", "fp", "fn", "matched": [(pred 번호, gt 번호, iou)]}
    """
    used, matched = set(), []
    for pi in sorted(range(len(preds)), key=lambda i: -preds[i].get("score", 0)):
        if not gts:
            continue
        ious = [iou(preds[pi], gt) for gt in gts]
        best = max(range(len(gts)), key=lambda gi: ious[gi])  # 같은 값이면 앞 번호 (np.argmax 와 같음)
        if ious[best] >= threshold and best not in used:
            used.add(best)
            matched.append((pi, best, round(ious[best], 3)))
    tp = len(matched)
    return {"tp": tp, "fp": len(preds) - tp, "fn": len(gts) - tp, "matched": matched}


# ---------------------------------------------------------------- 그리기
def draw(pixels, gts: list[dict], preds: list[dict], title: str, max_size: int = 1024):
    """자동 윈도우 영상에 정답(초록)·예측(빨강)을 그린 BGR 그림."""
    import cv2

    from core.dicom_io import apply_window

    img = cv2.cvtColor(apply_window(pixels), cv2.COLOR_GRAY2BGR)
    h, w = img.shape[:2]
    s = min(1.0, max_size / max(h, w))
    if s < 1.0:
        img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    thick = 3

    def rect(b, color, label=None):
        p0 = (int(b["xmin"] * s), int(b["ymin"] * s))
        p1 = (int(b["xmax"] * s), int(b["ymax"] * s))
        cv2.rectangle(img, p0, p1, color, thick)
        if label:
            cv2.putText(img, label, (p0[0], max(15, p0[1] - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

    for b in gts:
        rect(b, GREEN)
    for b in preds:
        rect(b, RED, f'{b["score"]:.2f}')
    (tw, th), _ = cv2.getTextSize(title, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
    cv2.rectangle(img, (0, 0), (tw + 20, th + 20), (0, 0, 0), -1)  # 글자 뒤 검은 바탕
    cv2.putText(img, title, (10, th + 10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    return img


# ---------------------------------------------------------------- 실행
def ground_truth_boxes(src: Path) -> dict[str, list[dict]]:
    """라벨 CSV → {VinDr image_id: {"label": implant|normal|other, "boxes": [...]}}.

    라벨에 없는 영상은 이 dict 에 없다 (정답 없음). 정상 영상은 boxes 가 빈 목록으로 들어 있다 (정답 0개).
    """
    paths = [src / "annotations" / name for name in ("train.csv", "test.csv")]
    if not any(p.exists() for p in paths):
        sys.exit(f"라벨 CSV가 없습니다: {src / 'annotations'} (train.csv, test.csv). 정답 없이는 평가할 수 없습니다.")
    images = {}
    for path in paths:
        if path.exists():
            images.update(collect_images(read_rows(path)))
    return {i: {"label": v["label"],
                "boxes": [{"xmin": x0, "ymin": y0, "xmax": x1, "ymax": y1} for x0, y0, x1, y1 in v["boxes"]]}
            for i, v in images.items()}


def pick_files(src: Path, split: str, limit: int) -> list[Path]:
    plan = list(csv.DictReader(open(src / "split.csv", encoding="utf-8")))
    files = []
    for row in plan:
        if split != "all" and row["split"] != split:
            continue
        folder = "test_images" if row["split"] == "test" else "train_images"
        f = src / folder / f"{row['image_id']}.dicom"
        if f.exists():
            files.append(f)
    return files[:limit] if limit else files


def sop_uid(path: Path) -> str:
    """VinDr image_id = DICOM SOPInstanceUID. 파일 이름이 바뀌어도 정답을 찾을 수 있게 파일 안의 값을 쓴다."""
    import pydicom

    ds = pydicom.dcmread(path, stop_before_pixels=True, specific_tags=["SOPInstanceUID"])
    return str(getattr(ds, "SOPInstanceUID", "") or path.stem)


def main(argv: list[str] | None = None) -> dict:
    import warnings

    import cv2

    from core.detector import DEFAULT_CONF, Detector
    from core.dicom_io import load_dicom

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path, help="확인할 DICOM 파일 (없으면 --split 에서 고름)")
    ap.add_argument("--split", default="val", choices=["train", "val", "test", "all"])
    ap.add_argument("--limit", type=int, default=20, help="최대 영상 수 (0 = 제한 없음)")
    ap.add_argument("--model", type=Path, default=MODEL)
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF)
    ap.add_argument("--src", type=Path, default=SRC)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    warnings.filterwarnings("ignore", message="Invalid value for VR UI")  # VinDr UID 형식 경고 (무해)

    files = args.files or pick_files(args.src, args.split, args.limit)
    if not files:
        sys.exit(f"확인할 영상이 없습니다. {args.src} 에 {args.split} 영상을 받았는지 확인하세요.")
    detector = Detector(args.model, conf=args.conf) if args.model.is_file() else None
    if detector is None:
        print(f"모델 파일이 없어 정답만 그립니다: {args.model}")
    gt_all = ground_truth_boxes(args.src)
    args.out.mkdir(parents=True, exist_ok=True)

    total = {"tp": 0, "fp": 0, "fn": 0, "images": 0, "normal": 0, "normal_with_fp": 0, "no_label": 0}
    times = []
    for f in files:
        uid = sop_uid(f)
        if uid not in gt_all:  # 정답 없음 ≠ 정상(정답 0개). 평가에서 빼고 알린다
            total["no_label"] += 1
            print(f"{uid[:12]}  라벨 CSV에 없는 영상 → 평가에서 제외")
            continue
        label, gts = gt_all[uid]["label"], gt_all[uid]["boxes"]
        pixels, _ = load_dicom(str(f))
        if detector:
            start = time.perf_counter()
            preds = detector.detect(pixels)["boxes"]
            times.append(time.perf_counter() - start)
        else:
            preds = []
        m = match(preds, gts)
        total["images"] += 1
        for k in ("tp", "fp", "fn"):
            total[k] += m[k]
        if label == "normal":  # "No finding" 영상만 정상 오탐률에 넣는다 (다른 소견만 있는 영상은 제외)
            total["normal"] += 1
            total["normal_with_fp"] += bool(preds)
        title = f"GT {len(gts)} / pred {len(preds)} / TP {m['tp']}" if detector else f"GT {len(gts)} (no model)"
        cv2.imwrite(str(args.out / f"{uid}.png"), draw(pixels, gts, preds, title))
        print(f"{uid[:12]}  정답 {len(gts)}개  예측 {len(preds)}개  맞힘 {m['tp']}  "
              f"오검출 {m['fp']}  놓침 {m['fn']}")

    if total["no_label"]:
        print(f"\n라벨 CSV에 없어 평가에서 뺀 영상: {total['no_label']}장")
    if not total["images"]:
        sys.exit("평가할 수 있는 영상이 없습니다. 라벨 CSV에 있는 VinDr 영상인지 확인하세요.")
    print(f"\n그림 {total['images']}장 저장: {args.out}  (초록 = 정답, 빨강 = 모델)")
    if detector:
        tp, fp, fn = total["tp"], total["fp"], total["fn"]
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        print(f"IoU≥{IOU_MATCH} 기준  맞힘 {tp}  오검출 {fp}  놓침 {fn}  "
              f"정밀도 {precision:.3f}  재현율 {recall:.3f}")
        if total["normal"]:
            print(f"정상 영상 오탐: {total['normal_with_fp']}/{total['normal']}장")
        print(f"탐지 시간(전처리+모델): 평균 {sum(times) / len(times):.2f}s, 최대 {max(times):.2f}s")
    return total


if __name__ == "__main__":
    main()
