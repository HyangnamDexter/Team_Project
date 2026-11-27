"""원본에서 영상처리 실행·이력·PNG 저장. 권한과 중복 검사는 트랜잭션에서 다시 확인한다."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import uuid4

import cv2

from core.algorithms import ALGORITHMS, roi_stats
from core.dicom_io import apply_window, load_dicom
from core.workflow import READER, require_role
from services import review
from services.access import editable_case, original_path, storage_path, visible_image

VERSION = "1.0"
DUPLICATE_SECONDS = 10


class ProcessingError(ValueError):
    pass


def apply_processing(pixels, algorithm, params, window, roi=None):
    """ROI만 처리하고 바깥 픽셀은 보존한다. 모든 좌표는 원본 영상 기준이다."""
    if algorithm not in ALGORITHMS:
        raise ProcessingError("지원하지 않는 알고리즘입니다.")
    source = apply_window(pixels, **window)
    h, w = source.shape
    region = roi or {"x": 0, "y": 0, "w": w, "h": h}
    roi_stats(source, **region)  # 정수·영상 범위 검사
    x, y, rw, rh = (region[k] for k in ("x", "y", "w", "h"))
    result = source.copy()
    try:
        result[y:y + rh, x:x + rw] = ALGORITHMS[algorithm](source[y:y + rh, x:x + rw], **params)
    except TypeError as exc:
        raise ProcessingError("알고리즘 파라미터를 확인하세요.") from exc
    return result, roi_stats(result, **region)


def list_processing(user, image_id, *, db):
    from google.cloud.firestore_v1.base_query import FieldFilter
    visible_image(user, image_id, db=db)
    logs = [dict(s.to_dict() or {}, log_id=s.id) for s in db.collection("processing_logs")
            .where(filter=FieldFilter("image_id", "==", image_id)).stream()]
    return sorted(logs, key=lambda d: d["run_at"], reverse=True)


def get_processing(user, log_id, *, db):
    require_role(user)
    snap = db.collection("processing_logs").document(log_id).get()
    if not snap.exists:
        raise ProcessingError("처리 이력을 찾을 수 없습니다.")
    log = dict(snap.to_dict() or {}, log_id=log_id)
    image = visible_image(user, log["image_id"], db=db)
    if image["case_id"] != log["case_id"]:
        raise ProcessingError("영상과 처리 이력의 케이스가 다릅니다.")
    return log


def load_processing(user, log_id, *, db, storage_root):
    log = get_processing(user, log_id, db=db)
    image = cv2.imread(str(storage_path(storage_root, log["result_path"])), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ProcessingError("저장된 처리 결과 파일을 읽을 수 없습니다.")
    return log, image


def run_processing(user, image_id, algorithm, params, window, roi=None, *, db, storage_root, now=None):
    require_role(user, READER)
    image = visible_image(user, image_id, db=db)
    editable_case(user, image["case_id"], db=db)
    # 중첩 dict를 복사하고 NaN·직렬화 불가 값도 거절한다.
    settings = json.loads(json.dumps({"image_id": image_id, "algorithm": algorithm,
                                     "algorithm_version": VERSION, "params": params,
                                     "window": window, "roi": roi}, sort_keys=True, allow_nan=False))
    key = sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
    now = now or datetime.now(timezone.utc)
    pixels, _ = load_dicom(original_path(image, storage_root))
    result, features = apply_processing(pixels, algorithm, settings["params"], settings["window"], settings["roi"])
    features.pop("hist")  # 히스토그램은 저장 영상으로 다시 계산한다.
    ok, encoded = cv2.imencode(".png", result)
    if not ok:
        raise ProcessingError("처리 결과를 인코딩하지 못했습니다.")
    log_id = "LOG-" + uuid4().hex
    relative = f"{image['case_id']}/{image_id}/results/{log_id}.png"
    target = storage_path(storage_root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    # 고유 경로에 먼저 완전히 기록하고 DB를 커밋한다. 원본 파일은 수정하지 않는다.
    with target.open("xb") as out:
        out.write(encoded.tobytes())
    log = {**settings, "case_id": image["case_id"], "slice_index": 0,
           "features": features, "result_path": relative, "run_key": key,
           "run_by": user["uid"], "run_at": now}
    log_ref = db.collection("processing_logs").document(log_id)
    guard_ref = db.collection("processing_keys").document(key)

    def commit(tx):
        current = visible_image(user, image_id, db=db, transaction=tx)
        editable_case(user, current["case_id"], db=db, transaction=tx)
        guard = guard_ref.get(transaction=tx)
        last = (guard.to_dict() or {}) if guard.exists else {}
        if last.get("run_at") and (now - last["run_at"]).total_seconds() < DUPLICATE_SECONDS:
            previous = db.collection("processing_logs").document(last["log_id"]).get(transaction=tx)
            if previous.exists:
                return dict(previous.to_dict() or {}, log_id=last["log_id"], reused=True)
        tx.set(log_ref, log)
        tx.set(guard_ref, {"run_at": now, "log_id": log_id})
        return dict(log, log_id=log_id, reused=False)

    try:
        saved = review._run_transaction(db, commit)
    except Exception as exc:
        # 응답 유실이면 실제 문서를 확인한다. 확인도 실패하면 파일을 보존한다.
        try:
            snap = log_ref.get()
        except Exception:
            raise ProcessingError("저장 결과가 불확실합니다. 처리 이력을 확인하세요.") from exc
        if snap.exists:
            return dict(snap.to_dict() or {}, log_id=log_id, reused=False)
        target.unlink(missing_ok=True)
        raise
    if saved["reused"]:
        target.unlink(missing_ok=True)
    return saved


def replay_processing(user, log_id, *, db, storage_root):
    log = get_processing(user, log_id, db=db)
    return run_processing(user, log["image_id"], log["algorithm"], log["params"],
                          log["window"], log["roi"], db=db, storage_root=storage_root)
