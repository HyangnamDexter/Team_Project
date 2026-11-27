"""모델 기본 탐색은 프로젝트 안으로 제한하고 외부 경로는 명시 설정만 허용한다."""
from services import detection


def test_detector_search_does_not_use_parent_folder(monkeypatch, tmp_path):
    default = tmp_path / "project/models/implant.onnx"
    outside = tmp_path / "best.onnx"
    outside.write_bytes(b"outside model fixture")
    monkeypatch.setattr(detection, "DEFAULT_MODEL_PATH", default)
    monkeypatch.setattr(detection.st, "secrets", {})
    monkeypatch.setattr(detection, "_open_detector", lambda path: path)
    assert detection.load_detector() == str(default)
    default.parent.mkdir(parents=True)
    inside = default.parent / "best.onnx"
    inside.write_bytes(b"inside model fixture")
    assert detection.load_detector() == str(inside)


def test_explicit_model_path_overrides_defaults(monkeypatch, tmp_path):
    configured = tmp_path / "elsewhere/best.onnx"
    override = tmp_path / "override.onnx"
    monkeypatch.setattr(detection.st, "secrets", {"model": {"path": str(configured)}})
    monkeypatch.setattr(detection, "_open_detector", lambda path: path)
    assert detection.load_detector() == str(configured)
    assert detection.load_detector(override) == str(override)
