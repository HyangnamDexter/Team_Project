"""케이스 등록 저장 (X-ray 1장). 담당: A — docs/firestore.md 3·4장, docs/workflow.md T1·T2

저장 순서 (파일 먼저, DB 나중 — NFR-04)
1. 원본을 임시 폴더 <저장소>/<case_id>/<image_id>/original.tmp/0001.dcm 에 쓴다
2. Firestore 에 cases·images·case_history 문서를 한 번에(batch) 기록한다
3. 성공하면 임시 폴더 이름을 original/ 로 바꾼다
4. DB 기록이 실패하면 임시 폴더를 지우고 RegistrationError 를 낸다
→ "DB에는 있는데 파일이 없는" 상황이 생기지 않는다. 남은 .tmp 는 앱 시작 때 cleanup_orphans 로 정리한다.

화면(B)에서 쓰는 예:
    from services.cases import register_case, RegistrationError
    result = register_case(user, uploaded.getvalue(), db=get_db(),
                           storage_root=Path(st.secrets["storage"]["image_dir"]))
    # result = {"case_id", "image_id", "status", "qc"}
"""
from __future__ import annotations

import secrets
import shutil
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from core.dicom_io import apply_window, load_dicom
from core.quality import QC_THRESHOLDS_VERSION, image_quality, tag_check
from core.workflow import QC_FAILED, RADIOGRAPHER, RECEIVED, require_role

XRAY_MODALITIES = {"CR", "DX"}  # 이번 범위는 X-ray(척추)만
SLICE_FILE = "0001.dcm"


class RegistrationError(Exception):
    """등록 실패. 메시지는 화면에 그대로 보여 줄 수 있는 문장이다."""


def new_ids(now: datetime) -> tuple[str, str]:
    """(case_id, image_id). 예: CASE-20261119-3fa9c2, IMG-20261119-7b1e"""
    day = now.strftime("%Y%m%d")
    return f"CASE-{day}-{secrets.token_hex(3)}", f"IMG-{day}-{secrets.token_hex(2)}"


def run_checks(pixels, header: dict) -> dict:
    """등록 시 자동 점검 (특화 기능). 결과는 images.qc 에 그대로 저장한다 (firestore.md 의 qc 예시 형식)."""
    modality = header["modality"]
    center, width = header.get("window_center"), header.get("window_width")
    img = apply_window(pixels, center, width) if center is not None and width is not None else apply_window(pixels)
    quality = image_quality(img, modality)
    try:
        tags = tag_check(header, modality)
    except NotImplementedError:  # TODO(B): tag_check 가 구현되면 이 처리는 필요 없다
        tags = {"missing": [], "phi_found": [], "issues": []}
    issues = quality["issues"] + tags["issues"]
    return {
        "result": "fail" if issues else "pass",
        "modality": modality,
        "sampled_slices": [0],
        "brightness": quality["brightness"],
        "contrast": quality["contrast"],
        "sharpness": quality["sharpness"],
        "missing_tags": tags["missing"],
        "phi_removed": [],  # TODO(B): 개인정보 태그 제거(FR-53) 연결
        "series": None,
        "issues": issues,
        "thresholds_version": QC_THRESHOLDS_VERSION,
    }


def register_case(user: dict | None, data: bytes, *, db, storage_root: Path,
                  now: datetime | None = None, ids: tuple[str, str] | None = None) -> dict:
    """X-ray DICOM 1장으로 케이스를 등록한다 (T1: 점검 통과 → 접수, T2: 점검 실패 → 재촬영 요청).

    - user: 로그인 사용자. 방사선사만 등록할 수 있다 (PermissionDenied)
    - data: 업로드한 파일 내용 (bytes)
    - db: Firestore 클라이언트 (services.db.get_db()). 테스트에서는 가짜 DB
    - now, ids: 테스트용 (시각·ID 고정)
    반환: {"case_id", "image_id", "status", "qc"}
    잘못된 DICOM은 core.dicom_io.InvalidDicomError, X-ray가 아니거나 저장 실패는 RegistrationError
    """
    require_role(user, RADIOGRAPHER)
    if not data:
        raise RegistrationError("빈 파일은 등록할 수 없습니다.")
    pixels, header = load_dicom(BytesIO(data))
    if header["modality"] not in XRAY_MODALITIES:
        raise RegistrationError(f"X-ray(CR·DX) 영상만 등록할 수 있습니다. (이 파일: {header['modality']})")

    qc = run_checks(pixels, header)
    status = QC_FAILED if qc["issues"] else RECEIVED
    now = now or datetime.now(timezone.utc)
    case_id, image_id = ids or new_ids(now)

    image_dir = Path(storage_root) / case_id / image_id
    tmp_dir, final_dir = image_dir / "original.tmp", image_dir / "original"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=False)
        (tmp_dir / SLICE_FILE).write_bytes(data)
    except OSError as exc:
        _remove_case_dir(Path(storage_root), case_id)
        raise RegistrationError("영상 파일을 저장하지 못했습니다. 저장 공간을 확인하세요.") from exc

    case_doc = {
        "status": status,
        "modality": header["modality"],
        "body_part": header.get("body_part", ""),
        "study_date": header.get("study_date", ""),
        "assignee_uid": None,
        "created_by": user["uid"],
        "created_at": now,
        "updated_at": now,
        "is_mock": True,
    }
    image_doc = {
        "case_id": case_id,
        "modality": header["modality"],
        "series_uid": header.get("series_uid", ""),
        "original_dir": f"{case_id}/{image_id}/original",  # 저장소(storage.image_dir) 기준 상대 경로
        "num_slices": 1,
        "slice_files": [SLICE_FILE],
        "slice_positions": None,
        "rows": header["rows"],
        "cols": header["cols"],
        "pixel_spacing": header.get("pixel_spacing"),
        "slice_thickness": header.get("slice_thickness"),
        "window_center": header.get("window_center"),
        "window_width": header.get("window_width"),
        "qc": qc,
        "is_current": True,
        "replaces_image_id": None,
        "uploaded_by": user["uid"],
        "uploaded_at": now,
    }
    history_doc = {
        "case_id": case_id,
        "from_status": None,
        "to_status": status,
        "reason": "; ".join(qc["issues"]) or None,  # T2(점검 실패)는 실패 사유를 남긴다
        "extra": {"image_id": image_id},
        "actor_uid": user["uid"],
        "at": now,
    }

    try:
        batch = db.batch()
        batch.set(db.collection("cases").document(case_id), case_doc)
        batch.set(db.collection("images").document(image_id), image_doc)
        batch.set(db.collection("case_history").document(), history_doc)
        batch.commit()
    except Exception as exc:
        _remove_case_dir(Path(storage_root), case_id)
        raise RegistrationError("DB 저장에 실패해 등록을 취소했습니다. 잠시 후 다시 시도하세요.") from exc

    tmp_dir.rename(final_dir)
    return {"case_id": case_id, "image_id": image_id, "status": status, "qc": qc}


def cleanup_orphans(storage_root: Path, db) -> dict:
    """앱 시작 때 남은 임시 폴더(original.tmp)를 정리한다.

    - DB에 images 문서가 있으면: DB 기록 후 이름 바꾸기 직전에 멈춘 것 → original/ 로 마저 바꾼다
    - DB에 없으면: DB 기록 전에 멈춘 것 → 지운다
    반환: {"recovered": [image_id...], "removed": [image_id...]}
    """
    recovered, removed = [], []
    root = Path(storage_root)
    if not root.exists():
        return {"recovered": recovered, "removed": removed}
    for tmp_dir in sorted(root.glob("*/*/original.tmp")):
        image_dir = tmp_dir.parent
        image_id, case_id = image_dir.name, image_dir.parent.name
        if db.collection("images").document(image_id).get().exists:
            final_dir = image_dir / "original"
            if final_dir.exists():  # 원본은 이미 있음 → 임시 폴더만 지운다 (원본은 절대 건드리지 않음)
                shutil.rmtree(tmp_dir, ignore_errors=True)
                removed.append(image_id)
            else:
                tmp_dir.rename(final_dir)
                recovered.append(image_id)
            continue
        shutil.rmtree(image_dir, ignore_errors=True)
        removed.append(image_id)
        case_dir = root / case_id
        if case_dir.exists() and not any(case_dir.iterdir()):
            case_dir.rmdir()
    return {"recovered": recovered, "removed": removed}


def _remove_case_dir(root: Path, case_id: str) -> None:
    shutil.rmtree(root / case_id, ignore_errors=True)
