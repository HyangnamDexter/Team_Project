"""VinDr-SpineXR 정답 박스 (ground_truth). 담당: A — docs/firestore.md 3장

- ground_truth/{VinDr image_id} 문서 1개 = 영상 1장의 정답 (임플란트 박스)
- VinDr image_id 는 DICOM 파일 안의 SOPInstanceUID 와 같다.
  등록할 때 images.sop_instance_uid 에 저장하므로, 파일 이름이 바뀌어도 이것으로 연결된다.
  정답을 먼저 넣든 영상을 먼저 등록하든 상관없다 (읽을 때 연결)
- 넣기: scripts/import_ground_truth.py (서비스 계정으로 실행하는 관리 작업, 화면에서 하지 않음)
- 읽기: get_ground_truth(user, image_id, db=) — 그 케이스를 볼 수 있는 사람만 (판독의는 본인 배정 건만)

화면(B)에서 쓰는 예:
    from services.ground_truth import get_ground_truth
    gt = get_ground_truth(user, image_id, db=get_db())
    if gt:  # None 이면 정답이 없는 영상 (VinDr 가 아닌 파일, 또는 아직 넣지 않음)
        for b in gt["boxes"]:  # 원본 영상 픽셀 좌표
            draw_box(b["xmin"], b["ymin"], b["xmax"], b["ymax"])
"""
from __future__ import annotations

from services.access import ImageNotFound, visible_image

COLLECTION = "ground_truth"
SOURCE = "VinDr-SpineXR 1.0.0"
CLASS_NAME = "implant"
BATCH_LIMIT = 400  # Firestore batch 한 번에 최대 500개. 여유를 둔다


class GroundTruthError(Exception):
    """정답을 넣거나 읽을 수 없음. 메시지는 화면에 그대로 보여 줄 수 있는 문장이다."""


def build_docs(plan_rows: list[dict], images: dict[str, dict]) -> dict[str, dict]:
    """학습 분할(split.csv 행)과 영상별 라벨(prepare_spinexr.collect_images 결과)로 문서를 만든다.

    반환: {VinDr image_id: 문서}. 문서 형식은 docs/firestore.md 의 ground_truth 참고.
    """
    docs = {}
    for row in plan_rows:
        image_id = row["image_id"]
        if image_id not in images:
            raise GroundTruthError(f"라벨 CSV에 없는 영상입니다: {image_id}")
        img = images[image_id]
        boxes = [{"xmin": x0, "ymin": y0, "xmax": x1, "ymax": y1} for x0, y0, x1, y1 in img["boxes"]]
        docs[image_id] = {
            "source": SOURCE,
            "study_id": img["study_id"],
            "split": row["split"],      # train / val / test (평가는 test 만 쓴다)
            "label": img["label"],      # implant / normal
            "class_name": CLASS_NAME,
            "boxes": boxes,             # 원본 영상 픽셀 좌표. 정상 영상은 빈 목록
            "n_boxes": len(boxes),
        }
    return docs


def import_docs(docs: dict[str, dict], *, db) -> int:
    """ground_truth 문서를 저장한다. 같은 ID 는 덮어쓰므로 여러 번 실행해도 결과가 같다.

    관리 작업(서비스 계정)이라 user 를 받지 않는다. 화면에서 호출하지 않는다.
    반환: 저장한 문서 수
    """
    items = sorted(docs.items())
    col = db.collection(COLLECTION)
    for start in range(0, len(items), BATCH_LIMIT):
        batch = db.batch()
        for image_id, doc in items[start:start + BATCH_LIMIT]:
            batch.set(col.document(image_id), doc)
        batch.commit()
    return len(items)


def get_ground_truth(user: dict | None, image_id: str, *, db, storage_root=None) -> dict | None:
    """등록된 영상(images/{image_id})의 정답. 정답이 없으면 None.

    권한: 로그인 필수. 판독의는 본인에게 배정된 케이스의 영상만 (workflow.md 3장 '영상 보기').
    반환: ground_truth 문서 + {"id": VinDr image_id}
    """
    try:
        image = visible_image(user, image_id, db=db)  # 로그인·담당자 확인 (services/access.py)
    except ImageNotFound as exc:
        raise GroundTruthError(str(exc)) from exc
    sop_uid = image.get("sop_instance_uid")
    if not sop_uid and storage_root is not None:
        # UID를 기록하지 않았던 이전 등록도 원본 헤더로 정답을 연결한다.
        import pydicom
        from services.access import original_path
        ds = pydicom.dcmread(original_path(image, storage_root), stop_before_pixels=True)
        sop_uid = str(getattr(ds, "SOPInstanceUID", "") or "")
    if not sop_uid:
        return None  # VinDr 가 아니거나 SOPInstanceUID 가 없는 파일
    gt_snap = db.collection(COLLECTION).document(sop_uid).get()
    if not gt_snap.exists:
        return None
    return dict(gt_snap.to_dict() or {}, id=sop_uid)
