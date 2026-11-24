"""케이스 등록 저장 (X-ray 1장). 담당: A — docs/firestore.md 3·4장, docs/workflow.md T1·T2

저장 순서 (파일 먼저, DB 나중 — NFR-04)
1. 원본을 임시 폴더 <저장소>/<case_id>/<image_id>/original.tmp/0001.dcm 에 쓴다
2. Firestore 에 cases·images·case_history 문서를 한 번에(batch) 기록한다
   cases·images 는 create 로 쓴다 → 같은 ID 문서가 이미 있으면 덮어쓰지 않고 실패한다
3. 성공하면 임시 폴더 이름을 original/ 로 바꾼다
4. DB 기록에서 예외가 나면 DB를 다시 읽어 결과를 확인한다
   - 우리 기록이 있음(응답만 유실): 성공으로 처리
   - 기록 없음: 임시 폴더를 지우고 RegistrationError
   - 다른 등록이 같은 ID를 이미 씀: 우리 임시 폴더만 지우고 새 ID로 다시 시도
   - 확인도 실패(결과 불확실): 임시 폴더를 남기고 RegistrationError → 앱 시작 때 cleanup_orphans 가 DB를 보고 복구·삭제
→ "DB에는 있는데 파일이 없는" 상황이 생기지 않는다.

화면(B)에서 쓰는 예:
    from services.cases import register_case, RegistrationError
    result = register_case(user, uploaded.getvalue(), db=get_db(),
                           storage_root=Path(st.secrets["storage"]["image_dir"]),
                           modality=selected)  # 파일에 Modality 태그가 없을 때 화면에서 고른 "DX" / "CR"
    # result = {"case_id", "image_id", "status", "qc"}
    # 파일에 Modality 가 없는데 modality 를 안 넘기면 RegistrationError → 화면에서 DX/CR 선택을 보여 주면 된다
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


ID_HEX = 8         # 날짜 + 무작위 16진수 8자리(32비트). 겹쳐도 create 로 막고 다시 시도한다
MAX_ATTEMPTS = 3   # ID가 겹쳤을 때 새 ID로 다시 시도하는 최대 횟수


def new_ids(now: datetime) -> tuple[str, str]:
    """(case_id, image_id). 예: CASE-20261121-3fa9c2d1, IMG-20261121-7b1e04aa"""
    day = now.strftime("%Y%m%d")
    return f"CASE-{day}-{secrets.token_hex(ID_HEX // 2)}", f"IMG-{day}-{secrets.token_hex(ID_HEX // 2)}"


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
        "missing_tags": sorted(set(header.get("missing_tags", [])) | set(tags["missing"])),
        "phi_removed": [],  # TODO(B): 개인정보 태그 제거(FR-53) 연결
        "series": None,
        "issues": issues,
        "thresholds_version": QC_THRESHOLDS_VERSION,
    }


def register_case(user: dict | None, data: bytes, *, db, storage_root: Path, modality: str | None = None,
                  now: datetime | None = None, ids: tuple[str, str] | None = None) -> dict:
    """X-ray DICOM 1장으로 케이스를 등록한다 (T1: 점검 통과 → 접수, T2: 점검 실패 → 재촬영 요청).

    - user: 로그인 사용자. 방사선사만 등록할 수 있다 (PermissionDenied)
    - data: 업로드한 파일 내용 (bytes)
    - db: Firestore 클라이언트 (services.db.get_db()). 테스트에서는 가짜 DB
    - modality: 파일에 Modality 태그가 없을 때만 쓰는 촬영 종류 ("DX" 또는 "CR", 화면에서 사용자가 선택).
      파일에 태그가 있으면 파일 값을 쓴다. (VinDr-SpineXR 은 Modality 태그가 없다)
    - now, ids: 테스트용 (시각·ID 고정)
    반환: {"case_id", "image_id", "status", "qc"}
    잘못된 DICOM은 core.dicom_io.InvalidDicomError, X-ray가 아니거나 저장 실패는 RegistrationError
    """
    require_role(user, RADIOGRAPHER)
    if not data:
        raise RegistrationError("빈 파일은 등록할 수 없습니다.")
    pixels, header = load_dicom(BytesIO(data))
    header = dict(header, **_resolve_modality(header.get("modality"), modality))

    qc = run_checks(pixels, header)
    status = QC_FAILED if qc["issues"] else RECEIVED
    now = now or datetime.now(timezone.utc)
    root = Path(storage_root)

    for _ in range(1 if ids else MAX_ATTEMPTS):
        case_id, image_id = ids or new_ids(now)
        if _save_once(db, root, data, header, qc, status, user, now, case_id, image_id) == "ok":
            return {"case_id": case_id, "image_id": image_id, "status": status, "qc": qc}
        # "conflict": 다른 등록이 같은 ID를 이미 씀 → 새 ID로 다시 시도
    raise RegistrationError("등록 번호가 이미 사용 중입니다. 다시 시도하세요.")


def _save_once(db, root: Path, data: bytes, header: dict, qc: dict, status: str, user: dict,
               now: datetime, case_id: str, image_id: str) -> str:
    """파일 → DB 저장을 한 번 시도한다. "ok" 또는 "conflict" 를 돌려주고, 그 밖의 실패는 RegistrationError."""
    image_dir = root / case_id / image_id
    tmp_dir, final_dir = image_dir / "original.tmp", image_dir / "original"
    if image_dir.exists():  # 디스크에 같은 ID 폴더가 있음 → 남의 폴더이므로 건드리지 않고 새 ID로
        return "conflict"
    try:
        tmp_dir.mkdir(parents=True)
        (tmp_dir / SLICE_FILE).write_bytes(data)
    except FileExistsError:  # 동시에 같은 ID 폴더를 만든 경우
        return "conflict"
    except OSError as exc:
        _remove_image_dir(root, case_id, image_id)
        raise RegistrationError("영상 파일을 저장하지 못했습니다. 저장 공간을 확인하세요.") from exc

    write_token = secrets.token_hex(16)  # 이 등록이 쓴 문서인지 나중에 확인하는 표시
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
        "write_token": write_token,
    }
    image_doc = {
        "case_id": case_id,
        "modality": header["modality"],
        "series_uid": header.get("series_uid", ""),
        "sop_instance_uid": _sop_instance_uid(data),  # 영상 고유 번호. VinDr 정답(ground_truth)과 연결하는 키
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
        "modality_source": header["modality_source"],  # "file": 파일 태그 / "user": 등록할 때 사용자가 선택
        "qc": qc,
        "is_current": True,
        "replaces_image_id": None,
        "uploaded_by": user["uid"],
        "uploaded_at": now,
        "write_token": write_token,
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
        batch.create(db.collection("cases").document(case_id), case_doc)    # 이미 있으면 실패 (덮어쓰기 방지)
        batch.create(db.collection("images").document(image_id), image_doc)
        batch.set(db.collection("case_history").document(), history_doc)   # 자동 ID라 겹치지 않음
        batch.commit()
    except Exception as exc:
        result = _check_written(db, case_id, image_id, write_token)
        if result == "unknown":  # 결과를 알 수 없음 → 파일을 남겨 두고 앱 시작 때 DB를 보고 정리
            raise RegistrationError(
                f"저장 결과를 확인하지 못했습니다. 잠시 후 케이스 목록에서 {case_id} 를 확인하세요."
            ) from exc
        if result != "ours":  # 기록 안 됨 또는 남의 기록 → 우리 파일만 지운다
            _remove_image_dir(root, case_id, image_id)
            if result == "conflict":
                return "conflict"
            raise RegistrationError("DB 저장에 실패해 등록을 취소했습니다. 잠시 후 다시 시도하세요.") from exc
        # "ours": DB에는 저장됐고 응답만 유실 → 성공으로 이어서 처리

    tmp_dir.rename(final_dir)
    return "ok"


def _check_written(db, case_id: str, image_id: str, write_token: str) -> str:
    """DB 기록 예외 뒤 실제 결과 확인. "ours" | "absent" | "conflict" | "unknown" """
    try:
        snaps = [db.collection(c).document(i).get() for c, i in (("cases", case_id), ("images", image_id))]
    except Exception:
        return "unknown"
    docs = [snap.to_dict() or {} for snap in snaps if snap.exists]
    if not docs:
        return "absent"
    if any(d.get("write_token") != write_token for d in docs):
        return "conflict"
    if len(docs) == 2:
        return "ours"
    return "unknown"  # 한쪽만 우리 기록 (batch 는 한꺼번에 반영되므로 정상이라면 생기지 않음)


def _sop_instance_uid(data: bytes) -> str | None:
    """파일의 SOPInstanceUID (영상 고유 번호). 없거나 읽지 못하면 None (등록은 그대로 진행)."""
    import pydicom

    try:
        ds = pydicom.dcmread(BytesIO(data), stop_before_pixels=True, specific_tags=["SOPInstanceUID"])
    except Exception:
        return None
    return str(getattr(ds, "SOPInstanceUID", "") or "").strip() or None


def _resolve_modality(from_file: str | None, chosen: str | None) -> dict:
    """등록에 쓸 촬영 종류. 파일 태그가 있으면 그 값, 없으면 사용자가 고른 값."""
    from_file = (from_file or "").strip()
    if from_file:
        if from_file not in XRAY_MODALITIES:
            raise RegistrationError(f"X-ray(CR·DX) 영상만 등록할 수 있습니다. (이 파일: {from_file})")
        return {"modality": from_file, "modality_source": "file"}
    chosen = (chosen or "").strip().upper()
    if not chosen:
        raise RegistrationError("파일에 촬영 종류(Modality) 태그가 없습니다. DX 또는 CR을 선택하세요.")
    if chosen not in XRAY_MODALITIES:
        raise RegistrationError(f"촬영 종류는 DX 또는 CR만 선택할 수 있습니다. (선택: {chosen})")
    return {"modality": chosen, "modality_source": "user"}


def cleanup_orphans(storage_root: Path, db) -> dict:
    """앱 시작 때 남은 임시 폴더(original.tmp)를 정리한다.

    - DB에 이 폴더의 images 문서가 있으면(같은 case_id): DB 기록 후 멈춘 것 → original/ 로 마저 바꾼다
    - DB에 없거나 다른 케이스의 문서면: DB 기록 전에 멈춘 것 → 지운다
    - DB를 읽지 못하면: 그 폴더는 건드리지 않는다 (다음 시작 때 다시 확인)
    반환: {"recovered": [image_id...], "removed": [image_id...]}
    """
    recovered, removed = [], []
    root = Path(storage_root)
    if not root.exists():
        return {"recovered": recovered, "removed": removed}
    for tmp_dir in sorted(root.glob("*/*/original.tmp")):
        image_dir = tmp_dir.parent
        image_id, case_id = image_dir.name, image_dir.parent.name
        try:
            snap = db.collection("images").document(image_id).get()
        except Exception:
            continue
        if snap.exists and (snap.to_dict() or {}).get("case_id") == case_id:
            final_dir = image_dir / "original"
            if final_dir.exists():  # 원본은 이미 있음 → 임시 폴더만 지운다 (원본은 절대 건드리지 않음)
                shutil.rmtree(tmp_dir, ignore_errors=True)
                removed.append(image_id)
            else:
                tmp_dir.rename(final_dir)
                recovered.append(image_id)
            continue
        if (image_dir / "original").exists():  # 원본이 있는 폴더는 지우지 않고 임시 폴더만
            shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            _remove_image_dir(root, case_id, image_id)
        removed.append(image_id)
    return {"recovered": recovered, "removed": removed}


def _remove_image_dir(root: Path, case_id: str, image_id: str) -> None:
    """이 등록이 만든 영상 폴더만 지운다. 케이스 폴더는 비었을 때만 지운다 (다른 영상 보호)."""
    shutil.rmtree(root / case_id / image_id, ignore_errors=True)
    case_dir = root / case_id
    if case_dir.exists() and not any(case_dir.iterdir()):
        case_dir.rmdir()
