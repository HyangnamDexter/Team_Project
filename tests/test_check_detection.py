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
