"""B의 등록 화면. 파일·DB 저장과 QC는 A의 register_case에 위임한다."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pydicom
import streamlit as st

from core.dicom_io import InvalidDicomError
from core.workflow import PermissionDenied, QC_FAILED
from services import cases, db
from services.auth import current_user


def queue_registration(data: bytes, modality: str | None) -> None:
    """버튼 콜백은 요청만 예약한다. 다음 렌더에서 버튼을 비활성화한 뒤 저장한다."""
    user = current_user()
    if not user:
        return
    token = (user["uid"], sha256(data).hexdigest())
    completed = st.session_state.get("cases_completed", {})
    if st.session_state.get("cases_pending") or token in completed:
        return
    st.session_state.cases_pending = {"token": token, "data": data, "modality": modality}
    st.session_state.pop("cases_registration_error", None)


def process_registration(user: dict) -> None:
    pending = st.session_state.get("cases_pending")
    if not pending:
        return
    # 다른 계정으로 로그인했을 때 이전 계정의 예약 요청을 실행하지 않는다.
    if pending["token"][0] != user["uid"]:
        st.session_state.pop("cases_pending", None)
        return
    try:
        with st.spinner("케이스를 등록하는 중입니다."):
            result = cases.register_case(
                user, pending["data"], db=db.get_db(),
                storage_root=Path(st.secrets["storage"]["image_dir"]),
                modality=pending["modality"],
            )
        completed = dict(st.session_state.get("cases_completed", {}))
        completed[pending["token"]] = result
        st.session_state.cases_completed = completed
    except (cases.RegistrationError, InvalidDicomError, PermissionDenied) as exc:
        st.session_state.cases_registration_error = (user["uid"], str(exc))
    except Exception:
        st.session_state.cases_registration_error = (
            user["uid"], "등록 결과를 확인하지 못했습니다. 목록을 새로고침해 확인한 뒤 다시 시도하세요."
        )
    finally:
        st.session_state.pop("cases_pending", None)
    st.rerun()


def render_registration(user: dict) -> None:
    with st.expander("X-ray 케이스 등록", expanded=True):
        upload = st.file_uploader("X-ray DICOM 파일 1개", type=["dcm", "dicom"],
                                  accept_multiple_files=False, key="cases_upload")
        data, modality, valid = b"", None, False
        if upload is not None:
            if upload.size > 500 * 1024 * 1024:
                st.error("파일은 500MB 이하로 선택하세요.")
            else:
                data = upload.getvalue()
                try:
                    # 픽셀 디코딩·점검은 등록 함수에서 한다. 화면은 촬영 종류만 확인한다.
                    header = pydicom.dcmread(BytesIO(data), stop_before_pixels=True)
                    from_file = str(getattr(header, "Modality", "") or "").strip()
                    if not from_file:
                        st.warning("촬영 종류(Modality) 태그가 없습니다. DX 또는 CR을 선택하세요.")
                        modality = st.selectbox("촬영 종류", ["DX", "CR"], index=None,
                                                placeholder="촬영 종류 선택",
                                                key="cases_modality_" + sha256(data).hexdigest())
                        valid = modality is not None
                    elif from_file not in {"DX", "CR"}:
                        st.error("X-ray(CR·DX) 단일 영상만 등록할 수 있습니다.")
                    else:
                        st.caption(f"촬영 종류: {from_file} (파일 태그)")
                        valid = True
                except Exception:
                    st.error("DICOM 헤더를 읽을 수 없습니다. 파일 형식을 확인하세요.")

        token = (user["uid"], sha256(data).hexdigest())
        result = st.session_state.get("cases_completed", {}).get(token)
        busy = bool(st.session_state.get("cases_pending"))
        st.button("등록 중…" if busy else "케이스 등록", type="primary",
                  disabled=not valid or busy or result is not None,
                  on_click=queue_registration, args=(data, modality), key="cases_register")
        if result:
            if result["status"] == QC_FAILED:
                reasons = ", ".join(result.get("qc", {}).get("issues", []))
                st.warning(f"{result['case_id']} 등록됨 — 재촬영 요청: {reasons}")
            else:
                st.success(f"{result['case_id']} 등록됨 ({result['status']})")
            st.caption("이미 등록한 파일입니다. 다른 영상을 선택하면 새로 등록할 수 있습니다.")
        error = st.session_state.get("cases_registration_error")
        if error and error[0] == user["uid"]:
            st.error(error[1])
        process_registration(user)
