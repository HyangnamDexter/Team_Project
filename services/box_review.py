"""모델/OpenCV 탐지 저장과 박스별 판독의 검토. 좌표·실행 정보의 서명으로 검토 대상을 고정한다."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import uuid4

from core.dicom_io import load_dicom
from core.quality import detect_foreign_objects
from core.workflow import READER, require_role
from services import review
from services.access import editable_case, original_path, visible_image
from services.detection import load_detector

COLLECTIONS = {"model": "detections", "opencv": "opencv_detections"}


class BoxReviewError(ValueError):
    pass


def fingerprint(doc):
    values = {k: doc.get(k) for k in ("boxes", "model", "conf", "run_id", "run_at")}
    return sha256(json.dumps(values, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()


def get_boxes(user, image_id, source, *, db):
    visible_image(user, image_id, db=db)
    if source not in COLLECTIONS:
        raise BoxReviewError("탐지 종류를 확인하세요.")
    snap = db.collection(COLLECTIONS[source]).document(image_id).get()
    return dict(snap.to_dict() or {}) if snap.exists else None


def run_boxes(user, image_id, source, *, db, storage_root, detector=None):
    require_role(user, READER)
    image = visible_image(user, image_id, db=db)
    editable_case(user, image["case_id"], db=db)
    if source not in COLLECTIONS:
        raise BoxReviewError("탐지 종류를 확인하세요.")
    pixels, _ = load_dicom(original_path(image, storage_root))
    if source == "model":
        detector = detector or load_detector()
        if detector is None:
            raise BoxReviewError("탐지 모델이 없습니다. 모델 파일 경로를 확인하세요.")
        result = detector.detect(pixels)
        doc = dict(result, status="done")
    else:
        objects = detect_foreign_objects(pixels, bg_sigma=25.0)["objects"]
        boxes = [{"xmin": o["x"], "ymin": o["y"], "xmax": o["x"] + o["w"],
                  "ymax": o["y"] + o["h"], "class_name": "candidate", "shape": o["shape"]} for o in objects]
        doc = {"status": "done", "boxes": boxes, "model": "OpenCV bg_sigma=25.0", "conf": None}
    doc.update(image_id=image_id, case_id=image["case_id"], n_boxes=len(doc["boxes"]),
               run_at=datetime.now(timezone.utc), run_by=user["uid"], run_id=uuid4().hex)
    ref = db.collection(COLLECTIONS[source]).document(image_id)

    def commit(tx):
        latest = visible_image(user, image_id, db=db, transaction=tx)
        editable_case(user, latest["case_id"], db=db, transaction=tx)
        tx.set(ref, doc)
        return doc

    return review._run_transaction(db, commit)


def list_box_reviews(user, image_id, *, db):
    from google.cloud.firestore_v1.base_query import FieldFilter
    visible_image(user, image_id, db=db)
    return [dict(s.to_dict() or {}, review_id=s.id) for s in db.collection("detection_reviews")
            .where(filter=FieldFilter("image_id", "==", image_id)).stream()]


def save_box_review(user, image_id, source, box_index, verdict, expected_fingerprint, *, db):
    require_role(user, READER)
    if source not in COLLECTIONS or verdict not in {"맞음", "오검출"}:
        raise BoxReviewError("탐지 종류와 검토 결과를 확인하세요.")
    if isinstance(box_index, bool) or not isinstance(box_index, int) or box_index < 0:
        raise BoxReviewError("박스 번호를 확인하세요.")

    def commit(tx):
        image = visible_image(user, image_id, db=db, transaction=tx)
        editable_case(user, image["case_id"], db=db, transaction=tx)
        snap = db.collection(COLLECTIONS[source]).document(image_id).get(transaction=tx)
        doc = (snap.to_dict() or {}) if snap.exists else {}
        signature = fingerprint(doc)
        if signature != expected_fingerprint or doc.get("status") != "done":
            raise BoxReviewError("탐지 결과가 변경되었습니다. 새로고침 후 다시 검토하세요.")
        if box_index >= len(doc.get("boxes", [])):
            raise BoxReviewError("탐지 박스를 찾을 수 없습니다.")
        review_id = f"{image_id}_{source}_{signature[:24]}_{box_index}"
        ref = db.collection("detection_reviews").document(review_id)
        old = ref.get(transaction=tx)
        previous = (old.to_dict() or {}) if old.exists else {}
        edits = list(previous.get("edits", []))
        now = datetime.now(timezone.utc)
        if previous:
            edits.append({"verdict": previous["verdict"], "reviewed_by": previous["reviewed_by"],
                          "reviewed_at": previous["reviewed_at"]})
        result = {"case_id": image["case_id"], "image_id": image_id, "source": source,
                  "box_index": box_index, "box": doc["boxes"][box_index], "fingerprint": signature,
                  "verdict": verdict, "reviewed_by": user["uid"], "reviewed_at": now, "edits": edits}
        tx.set(ref, result)
        return result

    return review._run_transaction(db, commit)
