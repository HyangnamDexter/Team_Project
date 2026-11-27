"""scripts/check_detection.py 테스트 (맞힘 판정과 그림 저장). 합성 DICOM 과 가짜 모델로 확인한다."""
import pytest

import core.detector
from scripts.check_detection import iou, main, match
from test_cases import dicom_bytes


def box(x0, y0, x1, y1, score=None):
    b = {"xmin": x0, "ymin": y0, "xmax": x1, "ymax": y1}
    return b if score is None else {**b, "score": score}


def test_iou():
    assert iou(box(0, 0, 10, 10), box(0, 0, 10, 10)) == 1.0
    assert iou(box(0, 0, 10, 10), box(5, 0, 15, 10)) == pytest.approx(50 / 150)
    assert iou(box(0, 0, 10, 10), box(20, 20, 30, 30)) == 0.0


def test_match_one_to_one():
    gts = [box(0, 0, 10, 10), box(50, 50, 60, 60)]
    preds = [box(0, 0, 10, 10, 0.9),     # 정답 1 맞힘
             box(1, 1, 11, 11, 0.8),     # 같은 정답을 또 가리킴 → 이미 짝지어져서 오검출
             box(100, 100, 110, 110, 0.7)]  # 엉뚱한 곳 → 오검출
    m = match(preds, gts)
    assert (m["tp"], m["fp"], m["fn"]) == (1, 2, 1)


def test_match_low_iou_is_not_hit():
    m = match([box(0, 0, 10, 10, 0.9)], [box(6, 0, 16, 10)])  # IoU 0.25
    assert (m["tp"], m["fp"], m["fn"]) == (0, 1, 1)


@pytest.fixture
def files(tmp_path):
    src = tmp_path / "vindr"
    (src / "annotations").mkdir(parents=True)
    (src / "annotations" / "train.csv").write_text(
        "study_id,series_id,image_id,rad_id,lesion_type,xmin,ymin,xmax,ymax\n"
        "st1,s,1.2.3.4,r,Surgical implant,10,10,30,40\n"
        "st2,s,1.2.3.5,r,No finding,,,,\n", encoding="utf-8")
    paths = []
    for uid in ("1.2.3.4", "1.2.3.5"):
        p = tmp_path / f"renamed_{uid}.dcm"  # 파일 이름이 달라도 파일 안의 SOPInstanceUID 로 정답을 찾는다
        p.write_bytes(dicom_bytes(SOPInstanceUID=uid))
        paths.append(p)
    return src, paths


def test_main_without_model_draws_ground_truth(tmp_path, files, capsys):
    src, paths = files
    out = tmp_path / "out"
    total = main([*map(str, paths), "--src", str(src), "--out", str(out), "--model", str(tmp_path / "none.onnx")])
    assert total["images"] == 2 and total["normal"] == 1
    assert (out / "1.2.3.4.png").exists() and (out / "1.2.3.5.png").exists()
    assert "정답만 그립니다" in capsys.readouterr().out


def test_main_with_model_counts_hits(tmp_path, files, monkeypatch, capsys):
    src, paths = files
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")

    class FakeDetector:
        def __init__(self, path, conf):
            pass

        def detect(self, pixels):  # 임플란트 영상·정상 영상 모두 같은 자리에 박스 1개
            return {"boxes": [box(10, 10, 30, 40, 0.9)]}

    monkeypatch.setattr(core.detector, "Detector", FakeDetector)
    total = main([*map(str, paths), "--src", str(src), "--out", str(tmp_path / "out"), "--model", str(model)])
    assert (total["tp"], total["fp"], total["fn"]) == (1, 1, 0)   # 임플란트 맞힘, 정상 영상에서 오검출 1
    assert total["normal_with_fp"] == 1
    out = capsys.readouterr().out
    assert "정밀도 0.500" in out and "재현율 1.000" in out and "정상 영상 오탐: 1/1장" in out


# ---------------------------------------------------------------- 리뷰 반영 (PR #31): C 평가 코드와 같은 매칭 규칙
def test_match_follows_c_rule_best_gt_already_taken_is_fp():
    # 예측 2가 가장 많이 겹치는 정답 A 를 예측 1이 이미 맞혔으면, 정답 B 와 IoU 0.5 이상이어도 오검출 (C 규칙)
    gts = [box(0, 0, 10, 10), box(2, 0, 12, 10)]          # A, B
    preds = [box(0, 0, 10, 10, 0.9),                       # A 와 IoU 1.0 → A 맞힘
             box(0.5, 0, 10.5, 10, 0.8)]                   # A 와 0.90 (가장 큼, 이미 맞힘), B 와 0.74 → 오검출
    m = match(preds, gts)
    assert (m["tp"], m["fp"], m["fn"]) == (1, 1, 1)        # 예전 규칙이면 B 를 맞힘으로 세서 (2, 0, 0)


def test_match_agrees_with_c_evaluation_code_on_random_cases():
    import numpy as np

    from scripts.eval_experiments import evaluate_opencv_records

    rng = np.random.default_rng(0)
    for case in range(300):
        def rand_boxes(n):
            xy = rng.uniform(0, 80, (n, 2))
            wh = rng.uniform(5, 30, (n, 2))
            return np.hstack([xy, xy + wh])

        gt = rand_boxes(rng.integers(0, 4))
        pr = rand_boxes(rng.integers(0, 5))
        scores = rng.permutation(len(pr)) / 10 + 0.05   # 서로 다른 점수 (동점 없음)
        mine = match([box(*p, float(s)) for p, s in zip(pr, scores)], [box(*g) for g in gt])
        c_rec = {"gt": gt.astype(np.float32), "elapsed": 0.0,
                 "objects": [{"x": p[0], "y": p[1], "w": p[2] - p[0], "h": p[3] - p[1], "mean": float(s)}
                             for p, s in zip(pr, scores)]}
        normal = {"gt": np.zeros((0, 4), np.float32), "objects": [], "elapsed": 0.0}  # C 함수의 정상 0장 나눗셈 방지
        c = evaluate_opencv_records([c_rec, normal], "iou50")
        assert (mine["tp"], mine["fp"], mine["fn"]) == (c["TP"], c["FP"], c["FN"]), case


# ---------------------------------------------------------------- 리뷰 반영 (PR #31): 정답 없음 ≠ 정상
def write_labels(src):
    (src / "annotations").mkdir(parents=True, exist_ok=True)
    (src / "annotations" / "train.csv").write_text(
        "study_id,series_id,image_id,rad_id,lesion_type,xmin,ymin,xmax,ymax\n"
        "st1,s,1.2.3.4,r,Surgical implant,10,10,30,40\n"
        "st2,s,1.2.3.5,r,No finding,,,,\n"
        "st3,s,1.2.3.6,r,Osteophytes,5,5,9,9\n", encoding="utf-8")


def dicom_file(tmp_path, uid):
    p = tmp_path / f"f_{uid}.dcm"
    p.write_bytes(dicom_bytes(SOPInstanceUID=uid))
    return p


def test_unlabeled_image_is_excluded_not_counted_as_normal(tmp_path, monkeypatch, capsys):
    src = tmp_path / "vindr"
    write_labels(src)
    files = [dicom_file(tmp_path, "1.2.3.5"), dicom_file(tmp_path, "9.9.9.9")]   # 정상 1장 + 라벨 없는 1장
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")

    class FakeDetector:
        def __init__(self, path, conf):
            pass

        def detect(self, pixels):
            return {"boxes": [box(10, 10, 30, 40, 0.9)]}

    monkeypatch.setattr(core.detector, "Detector", FakeDetector)
    total = main([*map(str, files), "--src", str(src), "--out", str(tmp_path / "out"), "--model", str(model)])
    assert total["images"] == 1 and total["no_label"] == 1
    assert total["normal"] == 1 and total["fp"] == 1          # 라벨 없는 영상의 박스는 세지 않음
    assert not (tmp_path / "out" / "9.9.9.9.png").exists()
    out = capsys.readouterr().out
    assert "평가에서 제외" in out and "정상 영상 오탐: 1/1장" in out


def test_other_lesion_image_not_counted_as_normal(tmp_path):
    src = tmp_path / "vindr"
    write_labels(src)
    total = main([str(dicom_file(tmp_path, "1.2.3.6")), "--src", str(src), "--out", str(tmp_path / "out"),
                  "--model", str(tmp_path / "none.onnx")])
    assert total["images"] == 1 and total["normal"] == 0      # 다른 소견만 있는 영상은 정상이 아님


def test_missing_label_csv_stops(tmp_path):
    with pytest.raises(SystemExit, match="라벨 CSV가 없습니다"):
        main([str(dicom_file(tmp_path, "1.2.3.4")), "--src", str(tmp_path / "empty"), "--out", str(tmp_path / "out")])


def test_only_unlabeled_images_stops(tmp_path):
    src = tmp_path / "vindr"
    write_labels(src)
    with pytest.raises(SystemExit, match="평가할 수 있는 영상이 없습니다"):
        main([str(dicom_file(tmp_path, "9.9.9.9")), "--src", str(src), "--out", str(tmp_path / "out"),
              "--model", str(tmp_path / "none.onnx")])
