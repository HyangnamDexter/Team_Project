"""scripts/prepare_spinexr.py 테스트. 실제 VinDr 영상 없이 가짜 CSV 행과 합성 DICOM으로 확인한다."""
from pathlib import Path

import cv2
import numpy as np
import pytest
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid

from scripts.prepare_spinexr import (
    collect_images,
    convert,
    make_plan,
    pick,
    split_by_study,
    to_yolo_line,
)


def row(image_id, study_id, lesion, box=None):
    xmin, ymin, xmax, ymax = box or ("", "", "", "")
    return {"study_id": study_id, "series_id": "s", "image_id": image_id, "rad_id": "rad1",
            "lesion_type": lesion, "xmin": str(xmin), "ymin": str(ymin), "xmax": str(xmax), "ymax": str(ymax)}


def fake_rows(n_studies=20, seed=0):
    """검사마다 영상 2장. 짝수 검사는 임플란트(박스 1~2개)+다른 소견, 홀수 검사는 정상."""
    rows = []
    for s in range(n_studies):
        sid = f"study{s:02d}"
        for k in range(2):
            iid = f"img{s:02d}_{k}"
            if s % 2 == 0 and k == 0:
                rows.append(row(iid, sid, "Surgical implant", (10, 20, 50, 80)))
                rows.append(row(iid, sid, "Surgical implant", (60, 20, 90, 60)))
                rows.append(row(iid, sid, "Osteophytes", (1, 1, 5, 5)))
            elif s % 2 == 0:
                rows.append(row(iid, sid, "Osteophytes", (1, 1, 5, 5)))
            else:
                rows.append(row(iid, sid, "No finding"))
    return rows


# --- 영상 단위로 묶기 ---
def test_collect_images_labels_and_boxes():
    images = collect_images(fake_rows(4))
    assert images["img00_0"]["label"] == "implant"
    assert images["img00_0"]["boxes"] == [(10.0, 20.0, 50.0, 80.0), (60.0, 20.0, 90.0, 60.0)]  # 다른 소견 박스는 제외
    assert images["img00_1"]["label"] == "other"
    assert images["img01_0"]["label"] == "normal"
    assert images["img01_0"]["boxes"] == []


def test_pick_ratio():
    images = collect_images(fake_rows(20))  # 임플란트 10장, 정상 20장
    chosen = pick(images, normal_per_implant=1.0, seed=1)
    labels = [v["label"] for v in chosen.values()]
    assert labels.count("implant") == 10 and labels.count("normal") == 10
    assert "other" not in labels


# --- 검사 단위 분할 ---
def test_split_never_mixes_a_study():
    images = pick(collect_images(fake_rows(40)), 2.0, seed=3)
    split = split_by_study(images, val_ratio=0.25, seed=3)
    by_study = {}
    for image_id, s in split.items():
        by_study.setdefault(images[image_id]["study_id"], set()).add(s)
    assert all(len(v) == 1 for v in by_study.values())
    assert set(split.values()) == {"train", "val"}


def test_split_keeps_implant_in_val():
    images = pick(collect_images(fake_rows(40)), 1.0, seed=5)
    split = split_by_study(images, val_ratio=0.2, seed=5)
    val_implant = [i for i, s in split.items() if s == "val" and images[i]["label"] == "implant"]
    assert len(val_implant) == 4  # 임플란트 검사 20개의 20%


def test_split_is_reproducible():
    images = pick(collect_images(fake_rows(30)), 1.0, seed=7)
    assert split_by_study(images, 0.2, seed=7) == split_by_study(images, 0.2, seed=7)


@pytest.mark.parametrize("ratio", [0, 1, -0.1, 1.5])
def test_split_invalid_ratio(ratio):
    with pytest.raises(ValueError):
        split_by_study({}, ratio, seed=0)


def test_make_plan_uses_test_csv_as_test():
    plan = make_plan(fake_rows(20), fake_rows(6), normal_per_implant=1.0, val_ratio=0.2, seed=0)
    test = [p for p in plan if p["split"] == "test"]
    assert {p["label"] for p in test} == {"implant", "normal"}
    assert all(p["n_boxes"] == 2 for p in plan if p["label"] == "implant")
    assert all(p["n_boxes"] == 0 for p in plan if p["label"] == "normal")


# --- YOLO 좌표 ---
def test_to_yolo_line_normalized():
    assert to_yolo_line((10, 20, 50, 80), width=100, height=200) == "0 0.300000 0.250000 0.400000 0.300000"


def test_to_yolo_line_clips_outside_and_swapped():
    # 영상 밖으로 나간 박스는 잘라 내고, 좌표 순서가 뒤집혀도 처리
    assert to_yolo_line((90, -10, 120, 40), 100, 100) == "0 0.950000 0.200000 0.100000 0.400000"
    assert to_yolo_line((50, 80, 10, 20), 100, 200) == to_yolo_line((10, 20, 50, 80), 100, 200)


def test_to_yolo_line_empty_box():
    assert to_yolo_line((150, 10, 200, 20), 100, 100) is None  # 완전히 영상 밖
    assert to_yolo_line((10, 10, 10, 30), 100, 100) is None    # 너비 0


# --- 변환 (합성 DICOM) ---
def write_dicom(path: Path, pixels: np.ndarray):
    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID, ds.SOPInstanceUID = meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID
    ds.Modality = "DX"
    ds.Rows, ds.Columns = pixels.shape
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.PixelData = pixels.astype(np.uint16).tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(path, enforce_file_format=True)


def test_convert_writes_png_labels_and_yaml(tmp_path):
    src, out = tmp_path / "vindr", tmp_path / "yolo"
    rng = np.random.default_rng(0)
    write_dicom(src / "train_images" / "a.dicom", rng.integers(0, 4000, (200, 100)))  # 세로 200, 가로 100
    write_dicom(src / "train_images" / "b.dicom", rng.integers(0, 4000, (80, 80)))
    (src / "test_images").mkdir()
    (src / "test_images" / "broken.dicom").write_bytes(b"not a dicom")
    plan = [
        {"image_id": "a", "split": "train"},       # 임플란트
        {"image_id": "b", "split": "val"},         # 정상
        {"image_id": "missing", "split": "train"},  # 아직 안 받은 영상
        {"image_id": "broken", "split": "test"},   # 깨진 파일
    ]
    boxes = {"a": [(10, 20, 50, 80)], "b": []}

    result = convert(plan, boxes, src, out, max_size=50)

    assert result["converted"] == {"train": 1, "val": 1, "test": 0}
    assert result["missing"] == 1 and result["failed"] == ["broken"]
    png = cv2.imread(str(out / "images" / "train" / "a.png"), cv2.IMREAD_UNCHANGED)
    assert png.dtype == np.uint8 and png.shape == (50, 25)  # 긴 변 50으로 축소, 비율 유지
    # 라벨은 원본 크기 기준 비율이라 축소와 상관없이 같다
    assert (out / "labels" / "train" / "a.txt").read_text() == "0 0.300000 0.250000 0.400000 0.300000\n"
    assert (out / "labels" / "val" / "b.txt").read_text() == ""  # 정상 영상은 빈 라벨
    yaml = (out / "dataset.yaml").read_text(encoding="utf-8")
    assert "train: images/train" in yaml and "0: implant" in yaml
