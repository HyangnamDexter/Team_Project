"""3주차 C 담당 실험 결과 재현 스크립트.

세 가지 하위 명령으로 실험 문서의 정량 표를 로컬 VinDr-SpineXR 데이터에서 재현한다.
  1) opencv     : test 122장 OpenCV 기본 / bg_sigma=25.0 / 개선 A(5% 경계 제외) / 개선 B(MORPH_TOPHAT) 비교
                  -> docs/experiments/foreign_objects_comparison.md
  2) qc         : 전체 626장(및 test 122장) 헤더 WL vs 자동 윈도우 품질 분포, sharpness_min v2 스윕, 대표 5종
                  -> docs/experiments/qc_thresholds.md
  3) algorithms : 대표 5종에 대한 CLAHE / Denoise / Edge 파라미터 비교 및 PSNR·경사 보존율·처리 시간 집계
                  -> docs/experiments/algorithms_comparison.md

실행 예:
  ./venv/bin/python scripts/eval_experiments.py qc
  ./venv/bin/python scripts/eval_experiments.py algorithms
  ./venv/bin/python scripts/eval_experiments.py opencv
  ./venv/bin/python scripts/eval_experiments.py all
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
import warnings
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.algorithms import apply_clahe, apply_denoise, apply_edge
from core.dicom_io import apply_window, load_dicom
from core.quality import QC_THRESHOLDS, detect_foreign_objects, image_quality

DEFAULT_DICOM_DIR = ROOT / "data" / "vindr-spinexr"
DEFAULT_YOLO_DIR = ROOT / "data" / "vindr-yolo"

# docs/experiments/qc_thresholds.md 및 docs/data.md에 확정 기록된 대표 척추 X-ray 5종 (test 분할)
REPRESENTATIVE_5: list[tuple[str, str]] = [
    ("정상", "3414ac0c4bf2aded206569cbeaaead3e"),
    ("흐림", "7429a824ee947f7a90cacf0a9a5438e7"),
    ("어두움", "12dd72dad98c015e9f1c5767ff809a9b"),
    ("밝음", "d062d5b91723e1a846496307ff81556a"),
    ("임플란트", "ddd5226f4b9c771e6510ae86c00d7304"),
]


# ---------------------------------------------------------------- 공통 헬퍼
def find_dicom_path(dicom_dir: Path, split: str, image_id: str) -> Path | None:
    folder = "test_images" if split == "test" else "train_images"
    for candidate in (dicom_dir / folder / f"{image_id}.dicom", dicom_dir / f"{image_id}.dicom"):
        if candidate.exists():
            return candidate
    return None


def apply_header_window(pixels: np.ndarray, header: dict) -> np.ndarray:
    """DICOM 헤더 WindowCenter/Width를 우선 적용하고, 없거나 유효하지 않으면 자동 윈도우로 대체한다."""
    wc, ww = header.get("window_center"), header.get("window_width")
    if wc is not None and ww is not None and ww >= 1:
        return apply_window(pixels, center=wc, width=ww)
    return apply_window(pixels)


def yolo_txt_to_xyxy(label_path: Path, img_w: int, img_h: int) -> np.ndarray:
    if not label_path.exists():
        return np.zeros((0, 4), dtype=np.float32)
    boxes = []
    for line in label_path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) < 5:
            continue
        _, cx, cy, w, h = map(float, parts[:5])
        boxes.append([(cx - w / 2.0) * img_w, (cy - h / 2.0) * img_h, (cx + w / 2.0) * img_w, (cy + h / 2.0) * img_h])
    return np.array(boxes, dtype=np.float32) if boxes else np.zeros((0, 4), dtype=np.float32)


def compute_iou_matrix(boxes_a: np.ndarray, boxes_b: np.ndarray) -> np.ndarray:
    if len(boxes_a) == 0 or len(boxes_b) == 0:
        return np.zeros((len(boxes_a), len(boxes_b)), dtype=np.float32)
    x1 = np.maximum(boxes_a[:, None, 0], boxes_b[None, :, 0])
    y1 = np.maximum(boxes_a[:, None, 1], boxes_b[None, :, 1])
    x2 = np.minimum(boxes_a[:, None, 2], boxes_b[None, :, 2])
    y2 = np.minimum(boxes_a[:, None, 3], boxes_b[None, :, 3])
    inter = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    area_a = np.maximum(0.0, boxes_a[:, 2] - boxes_a[:, 0]) * np.maximum(0.0, boxes_a[:, 3] - boxes_a[:, 1])
    area_b = np.maximum(0.0, boxes_b[:, 2] - boxes_b[:, 0]) * np.maximum(0.0, boxes_b[:, 3] - boxes_b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    return np.where(union > 0, inter / union, 0.0)


def center_in_box_matrix(preds: np.ndarray, gts: np.ndarray) -> np.ndarray:
    if len(preds) == 0 or len(gts) == 0:
        return np.zeros((len(preds), len(gts)), dtype=bool)
    cx = (preds[:, 0] + preds[:, 2]) * 0.5
    cy = (preds[:, 1] + preds[:, 3]) * 0.5
    return (
        (cx[:, None] >= gts[None, :, 0])
        & (cx[:, None] <= gts[None, :, 2])
        & (cy[:, None] >= gts[None, :, 1])
        & (cy[:, None] <= gts[None, :, 3])
    )


# ---------------------------------------------------------------- 1) OpenCV 비교 실험
def filter_border_markers(
    objects: list[dict],
    img_h: int,
    img_w: int,
    margin_ratio: float = 0.05,
    max_marker_area_ratio: float = 0.005,
) -> list[dict]:
    """상하좌우 margin_ratio 경계에 닿는 작은 덩어리(area <= max_marker_area_ratio)를 제외한다."""
    mx, my = img_w * margin_ratio, img_h * margin_ratio
    max_area = img_h * img_w * max_marker_area_ratio
    return [
        o
        for o in objects
        if not (
            (o["x"] < mx or o["x"] + o["w"] > img_w - mx or o["y"] < my or o["y"] + o["h"] > img_h - my)
            and o["area"] <= max_area
        )
    ]


def detect_tophat_border5(
    pixels: np.ndarray,
    kernel_size: int = 51,
    percentile: float = 99.5,
    min_area: int = 30,
    max_area_ratio: float = 0.02,
    margin_ratio: float = 0.05,
) -> list[dict]:
    """비교 실험용 개선 B: MORPH_TOPHAT(큰 사각 커널) + 동일 모폴로지·형태 분류 + 5% 경계 제외."""
    work = np.array(pixels, copy=True)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
    target = cv2.morphologyEx(work.astype(np.float32), cv2.MORPH_TOPHAT, kernel)

    thresh = float(np.percentile(target, percentile))
    mask_th = np.where(target >= thresh, 255, 0).astype(np.uint8)
    opened = cv2.morphologyEx(mask_th, cv2.MORPH_OPEN, np.ones((3, 3), dtype=np.uint8))
    cleaned = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, np.ones((5, 5), dtype=np.uint8))

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(cleaned)
    h, w = work.shape
    max_area = h * w * max_area_ratio
    objects = []
    for label in range(1, num_labels):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_area or area > max_area:
            continue
        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        bw = int(stats[label, cv2.CC_STAT_WIDTH])
        bh = int(stats[label, cv2.CC_STAT_HEIGHT])
        mean_val = float(work[labels == label].mean())
        comp_mask = np.where(labels == label, 255, 0).astype(np.uint8)
        contours, _ = cv2.findContours(comp_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        shape = "other"
        if contours:
            cnt = max(contours, key=cv2.contourArea)
            (_, _), (rw, rh), _ = cv2.minAreaRect(cnt)
            long_s, short_s = max(rw, rh), min(rw, rh)
            perim = float(cv2.arcLength(cnt, True))
            if (short_s == 0.0 and long_s > 0.0) or (short_s > 0.0 and long_s / short_s >= 3.0):
                shape = "long"
            elif perim > 0.0 and (4.0 * np.pi * area / (perim**2)) >= 0.7:
                shape = "round"
        objects.append({"x": x, "y": y, "w": bw, "h": bh, "area": area, "mean": mean_val, "shape": shape})

    return filter_border_markers(objects, h, w, margin_ratio=margin_ratio, max_marker_area_ratio=0.005)


def evaluate_opencv_records(records: list[dict], mode: str) -> dict:
    """OpenCV 예측 박스를 GT와 비교한다.

    매칭 순서 및 중복 처리 규칙:
    - iou50       : 예측 박스를 원시 평균 밝기(mean) 내림차순 정렬 후, 각 예측의 최대 IoU GT가
                    IoU >= 0.50이고 미매칭이면 1:1 TP 할당. 동일 GT에 중복 적중한 추가 박스는 모두 FP.
    - iou0_1to1   : IoU > 0인 모든 (pred, gt) 쌍을 IoU 내림차순 정렬해 1:1 greedy 매칭. 중복 적중 박스는 FP.
    - center_1to1 : 예측 중심이 GT 내부에 포함되는 쌍을 IoU 내림차순 1:1 greedy 매칭. 중복 박스는 FP.
    - iou0_any    : 다대일 파편 허용 참고 지표. GT는 1개 이상 겹치면 TP, 예측은 어떤 GT와도 겹치지 않을 때만 FP.
    """
    tp_tot = fp_tot = fn_tot = 0
    n_norm = norm_fp = norm_fp_imgs = 0
    tot_time = 0.0
    tot_preds = 0

    for rec in records:
        gt = rec["gt"]
        objs = sorted(rec["objects"], key=lambda o: o["mean"], reverse=True)
        tot_time += rec["elapsed"]
        tot_preds += len(objs)

        preds = (
            np.array([[o["x"], o["y"], o["x"] + o["w"], o["y"] + o["h"]] for o in objs], dtype=np.float32)
            if objs
            else np.zeros((0, 4), dtype=np.float32)
        )
        iou_mat = compute_iou_matrix(preds, gt)
        matched_gt: set[int] = set()
        matched_pred: set[int] = set()

        if mode == "iou50":
            for pi in range(len(preds)):
                if len(gt) == 0:
                    continue
                gi = int(np.argmax(iou_mat[pi]))
                if iou_mat[pi, gi] >= 0.50 and gi not in matched_gt:
                    matched_gt.add(gi)
                    matched_pred.add(pi)
            tp, fp, fn = len(matched_pred), len(preds) - len(matched_pred), len(gt) - len(matched_gt)
        elif mode in ("iou0_1to1", "center_1to1"):
            cond = iou_mat > 0.0 if mode == "iou0_1to1" else center_in_box_matrix(preds, gt)
            pairs = [
                (float(iou_mat[pi, gi]), pi, gi)
                for pi in range(len(preds))
                for gi in range(len(gt))
                if cond[pi, gi]
            ]
            pairs.sort(key=lambda t: t[0], reverse=True)
            for _, pi, gi in pairs:
                if pi not in matched_pred and gi not in matched_gt:
                    matched_pred.add(pi)
                    matched_gt.add(gi)
            tp, fp, fn = len(matched_pred), len(preds) - len(matched_pred), len(gt) - len(matched_gt)
        elif mode == "iou0_any":
            for pi in range(len(preds)):
                for gi in range(len(gt)):
                    if iou_mat[pi, gi] > 0.0:
                        matched_pred.add(pi)
                        matched_gt.add(gi)
            tp, fp, fn = len(matched_gt), len(preds) - len(matched_pred), len(gt) - len(matched_gt)
        else:
            raise ValueError(mode)

        tp_tot += tp
        fp_tot += fp
        fn_tot += fn
        if len(gt) == 0:
            n_norm += 1
            norm_fp += fp
            if fp > 0:
                norm_fp_imgs += 1

    n_total = len(records)
    if mode == "iou0_any":
        precision = (tot_preds - fp_tot) / tot_preds if tot_preds else 0.0
    else:
        precision = tp_tot / (tp_tot + fp_tot) if (tp_tot + fp_tot) else 0.0
    recall = tp_tot / (tp_tot + fn_tot) if (tp_tot + fn_tot) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "TP": tp_tot,
        "FP": fp_tot,
        "FN": fn_tot,
        "Precision": round(precision, 4),
        "Recall": round(recall, 4),
        "F1": round(f1, 4),
        "FP_per_image": round(fp_tot / n_total, 4),
        "FP_per_normal": round(norm_fp / n_norm, 4),
        "normal_FPR": round(norm_fp_imgs / n_norm * 100.0, 2),
        "avg_time_sec": round(tot_time / n_total, 4),
    }


def run_opencv_benchmark(dicom_dir: Path, yolo_dir: Path) -> None:
    test_img_dir = yolo_dir / "images" / "test"
    test_lbl_dir = yolo_dir / "labels" / "test"
    img_ids = sorted(p.stem for p in test_img_dir.glob("*.png"))
    if not img_ids:
        sys.exit(f"test PNG를 찾을 수 없습니다: {test_img_dir}")

    buckets: dict[str, list[dict]] = {k: [] for k in ("base", "bg25", "bg25_border5", "tophat51_border5")}
    for iid in img_ids:
        dcm = find_dicom_path(dicom_dir, "test", iid)
        if dcm is None:
            sys.exit(f"DICOM 파일을 찾을 수 없습니다: {iid}")
        pixels, _ = load_dicom(str(dcm))
        h, w = pixels.shape
        gt = yolo_txt_to_xyxy(test_lbl_dir / f"{iid}.txt", w, h)

        t0 = time.perf_counter()
        res_base = detect_foreign_objects(pixels)
        buckets["base"].append({"gt": gt, "objects": res_base["objects"], "elapsed": time.perf_counter() - t0})

        t0 = time.perf_counter()
        res_bg25 = detect_foreign_objects(pixels, bg_sigma=25.0)
        dt_bg25 = time.perf_counter() - t0
        buckets["bg25"].append({"gt": gt, "objects": res_bg25["objects"], "elapsed": dt_bg25})

        t0 = time.perf_counter()
        objs_b5 = filter_border_markers(res_bg25["objects"], h, w)
        buckets["bg25_border5"].append({"gt": gt, "objects": objs_b5, "elapsed": dt_bg25 + (time.perf_counter() - t0)})

        t0 = time.perf_counter()
        objs_th = detect_tophat_border5(pixels)
        buckets["tophat51_border5"].append({"gt": gt, "objects": objs_th, "elapsed": time.perf_counter() - t0})

    print(f"\n=== OpenCV 비교 실험 결과 (test {len(img_ids)}장) ===")
    for name, recs in buckets.items():
        print(f"\n[{name}]")
        for mode in ("iou50", "iou0_1to1", "center_1to1", "iou0_any"):
            s = evaluate_opencv_records(recs, mode)
            print(
                f"  {mode:11s} | TP={s['TP']:3d} FP={s['FP']:4d} FN={s['FN']:3d} "
                f"P={s['Precision']:.4f} R={s['Recall']:.4f} F1={s['F1']:.4f} "
                f"FP/img={s['FP_per_image']:.4f} FP/norm={s['FP_per_normal']:.4f} "
                f"normFPR={s['normal_FPR']:.2f}% t={s['avg_time_sec']:.4f}s"
            )


# ---------------------------------------------------------------- 2) QC 분포 및 v2 스윕
def run_qc_benchmark(dicom_dir: Path, yolo_dir: Path) -> None:
    split_csv = dicom_dir / "split.csv"
    if not split_csv.exists():
        sys.exit(f"split.csv를 찾을 수 없습니다: {split_csv}")
    rows = list(csv.DictReader(split_csv.open(encoding="utf-8")))
    converted = [r for r in rows if (yolo_dir / "images" / r["split"] / f"{r['image_id']}.png").exists()]

    records = []
    for r in converted:
        dcm = find_dicom_path(dicom_dir, r["split"], r["image_id"])
        if dcm is None:
            continue
        pixels, header = load_dicom(str(dcm))
        q_hdr = image_quality(apply_header_window(pixels, header), modality="DX")
        q_auto = image_quality(apply_window(pixels), modality="DX")
        records.append({"image_id": r["image_id"], "split": r["split"], "label": r["label"], "hdr": q_hdr, "auto": q_auto})

    print(f"\n=== QC 실측 분포 집계 (N={len(records)}, 현재 DX 기준={QC_THRESHOLDS['DX']}) ===")
    for key, title in (("hdr", "헤더 WL (없으면 자동 윈도우)"), ("auto", "자동 윈도우 (1%~99%)")):
        print(f"\n[{title}]")
        for metric in ("brightness", "contrast", "sharpness"):
            arr = np.array([x[key][metric] for x in records])
            p5, p10, p25, p50, p75, p95 = np.percentile(arr, [5, 10, 25, 50, 75, 95])
            print(
                f"  {metric:10s}: mean={arr.mean():6.2f} std={arr.std():6.2f} "
                f"min={arr.min():5.2f} p5={p5:5.2f} p10={p10:5.2f} p25={p25:5.2f} "
                f"p50={p50:6.2f} p75={p75:6.2f} p95={p95:6.2f} max={arr.max():7.2f}"
            )

    print("\n[헤더 WL sharpness_min 후보 스윕 (bmin=35, bmax=220, cmin=25)]")
    for smin in (20.0, 25.0, 30.0, 35.0, 40.0, 50.0):
        cnt = Counter()
        n_pass = 0
        for x in records:
            q = x["hdr"]
            iss = []
            if q["brightness"] < 35.0:
                iss.append("너무 어두움")
            if q["brightness"] > 220.0:
                iss.append("너무 밝음")
            if q["contrast"] < 25.0:
                iss.append("대비 부족")
            if q["sharpness"] < smin:
                iss.append("선명도 부족")
            if not iss:
                n_pass += 1
            for it in iss:
                cnt[it] += 1
        print(f"  sharpness_min={smin:4.1f} -> pass={n_pass}/{len(records)} ({n_pass/len(records)*100:.2f}%) issues={dict(cnt)}")

    by_id = {x["image_id"]: x for x in records}
    print("\n[대표 5종 현재 DX 기준 판정]")
    for tag, iid in REPRESENTATIVE_5:
        if iid in by_id:
            print(f"  {tag:4s} ({iid}): HDR={by_id[iid]['hdr']} | AUTO={by_id[iid]['auto']}")


# ---------------------------------------------------------------- 3) 알고리즘 비교 실험
def estimate_noise_sigma(img: np.ndarray) -> float:
    k = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float64)
    conv = cv2.filter2D(img.astype(np.float64), cv2.CV_64F, k)
    h, w = img.shape
    return float(np.sum(np.abs(conv)) * np.sqrt(0.5 * np.pi) / (6.0 * (w - 2) * (h - 2)))


def calc_psnr(ref_u8: np.ndarray, out_u8: np.ndarray) -> float:
    """PSNR 기준 영상: apply_window(pixels) 원본 uint8."""
    mse = float(np.mean((ref_u8.astype(np.float64) - out_u8.astype(np.float64)) ** 2))
    return round(10.0 * np.log10((255.0**2) / mse), 2) if mse > 0 else 99.99


def sobel_magnitude_u8(img: np.ndarray, ksize: int) -> np.ndarray:
    gx = cv2.Sobel(img, cv2.CV_64F, 1, 0, ksize=ksize)
    gy = cv2.Sobel(img, cv2.CV_64F, 0, 1, ksize=ksize)
    mag = np.hypot(gx, gy)
    mx = float(mag.max())
    return np.clip((mag / mx) * 255.0, 0, 255).astype(np.uint8) if mx > 0 else np.zeros_like(img)


def run_algorithms_benchmark(dicom_dir: Path) -> None:
    """대표 5종에 대해 CLAHE / Denoise / Edge 파라미터 비교표를 재현한다.

    집계 기준:
    - 기준 영상: load_dicom() -> apply_window(pixels) 8-bit uint8 (PSNR 및 보존율 분모 기준).
    - 5종 평균 지표: 각 대표 영상 5종의 개별 측정값을 단순 산술평균.
    - CLAHE 선명도 변화율·잡음 배수: 5종 평균 후처리 값 / 5종 평균 원본 값.
    - Denoise 선명도 보존율·경사 보존율·잡음 억제율: 각 영상별 비율을 먼저 계산한 뒤 5종 단순 산술평균.
    """
    imgs = {}
    base = {}
    for tag, iid in REPRESENTATIVE_5:
        dcm = find_dicom_path(dicom_dir, "test", iid)
        if dcm is None:
            sys.exit(f"대표 샘플 DICOM을 찾을 수 없습니다: {iid}")
        pixels, _ = load_dicom(str(dcm))
        u8 = apply_window(pixels)
        imgs[tag] = u8
        q = image_quality(u8, modality="DX")
        gx = cv2.Sobel(u8, cv2.CV_64F, 1, 0, ksize=3)
        gy = cv2.Sobel(u8, cv2.CV_64F, 0, 1, ksize=3)
        base[tag] = {
            **q,
            "noise": estimate_noise_sigma(u8),
            "grad": float(np.hypot(gx, gy).mean()),
        }

    print("\n=== 알고리즘 비교: CLAHE (5종 산술평균) ===")
    for clip in (1.0, 2.0, 4.0):
        for tile in (4, 8, 16):
            bs, cs, ss, ns, ts = [], [], [], [], []
            for tag, _ in REPRESENTATIVE_5:
                t0 = time.perf_counter()
                out = apply_clahe(imgs[tag], clip_limit=clip, tile_size=tile)
                ts.append((time.perf_counter() - t0) * 1000.0)
                q = image_quality(out, modality="DX")
                bs.append(q["brightness"])
                cs.append(q["contrast"])
                ss.append(q["sharpness"])
                ns.append(estimate_noise_sigma(out))
            print(
                f"  clip={clip:3.1f} tile={tile:2d} | B={np.mean(bs):6.2f} C={np.mean(cs):5.2f} "
                f"S={np.mean(ss):7.2f} noise={np.mean(ns):.3f} t={np.mean(ts):5.2f}ms"
            )

    print("\n=== 알고리즘 비교: Denoise (5종 영상별 비율 산술평균, PSNR 기준=원본 uint8) ===")
    for method in ("gaussian", "median", "bilateral"):
        for ksize in (3, 5, 7):
            ss, s_ret, g_ret, n_red, psnrs, ts = [], [], [], [], [], []
            for tag, _ in REPRESENTATIVE_5:
                t0 = time.perf_counter()
                out = apply_denoise(imgs[tag], method=method, ksize=ksize)
                ts.append((time.perf_counter() - t0) * 1000.0)
                q = image_quality(out, modality="DX")
                gx = cv2.Sobel(out, cv2.CV_64F, 1, 0, ksize=3)
                gy = cv2.Sobel(out, cv2.CV_64F, 0, 1, ksize=3)
                grad = float(np.hypot(gx, gy).mean())
                noise = estimate_noise_sigma(out)
                ss.append(q["sharpness"])
                s_ret.append(q["sharpness"] / base[tag]["sharpness"] * 100.0)
                g_ret.append(grad / base[tag]["grad"] * 100.0)
                n_red.append((1.0 - noise / base[tag]["noise"]) * 100.0)
                psnrs.append(calc_psnr(imgs[tag], out))
            print(
                f"  {method:9s} k={ksize} | S={np.mean(ss):6.2f} (ret={np.mean(s_ret):5.1f}%) "
                f"grad_ret={np.mean(g_ret):5.1f}% noise_red={np.mean(n_red):5.1f}% "
                f"PSNR={np.mean(psnrs):5.2f}dB t={np.mean(ts):5.2f}ms"
            )

    print("\n=== 알고리즘 비교: Edge (유형별 비율 및 평균 시간) ===")
    for low, high in ((30, 90), (50, 150), (80, 200)):
        vals, ts = [], []
        for tag, _ in REPRESENTATIVE_5:
            t0 = time.perf_counter()
            out = apply_edge(imgs[tag], method="canny", low=low, high=high)
            ts.append((time.perf_counter() - t0) * 1000.0)
            vals.append(f"{tag}:{np.mean(out > 0) * 100:.3f}%")
        print(f"  canny({low:2d},{high:3d}) | t={np.mean(ts):5.2f}ms | " + " ".join(vals))

    for ksize in (3, 5):
        vals, ts = [], []
        for tag, _ in REPRESENTATIVE_5:
            t0 = time.perf_counter()
            out = apply_edge(imgs[tag], method="sobel") if ksize == 3 else sobel_magnitude_u8(imgs[tag], ksize=5)
            ts.append((time.perf_counter() - t0) * 1000.0)
            vals.append(f"{tag}:{np.mean(out >= 30) * 100:.3f}%")
        print(f"  sobel(k={ksize})   | t={np.mean(ts):5.2f}ms | " + " ".join(vals))

    vals, ts = [], []
    for tag, _ in REPRESENTATIVE_5:
        t0 = time.perf_counter()
        out = apply_edge(imgs[tag], method="otsu")
        ts.append((time.perf_counter() - t0) * 1000.0)
        vals.append(f"{tag}:{np.mean(out > 0) * 100:.2f}%")
    print(f"  otsu         | t={np.mean(ts):5.2f}ms | " + " ".join(vals))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task", choices=["opencv", "qc", "algorithms", "all"], help="재현할 실험 항목")
    ap.add_argument("--dicom-dir", type=Path, default=DEFAULT_DICOM_DIR, help="VinDr-SpineXR DICOM 폴더")
    ap.add_argument("--yolo-dir", type=Path, default=DEFAULT_YOLO_DIR, help="변환된 YOLO 데이터 폴더")
    args = ap.parse_args()

    if args.task in ("qc", "all"):
        run_qc_benchmark(args.dicom_dir, args.yolo_dir)
    if args.task in ("algorithms", "all"):
        run_algorithms_benchmark(args.dicom_dir)
    if args.task in ("opencv", "all"):
        run_opencv_benchmark(args.dicom_dir, args.yolo_dir)


if __name__ == "__main__":
    main()
