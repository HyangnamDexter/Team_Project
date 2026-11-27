"""교육용 DICOM의 직접 식별 태그 제거. 픽셀 속 문자까지 익명화하지는 않는다."""
from io import BytesIO

import pydicom

IDENTIFIERS = frozenset({
    "PatientName", "PatientID", "IssuerOfPatientID", "PatientBirthDate", "PatientBirthTime",
    "PatientAddress", "PatientTelephoneNumbers", "OtherPatientIDs", "OtherPatientNames",
    "OtherPatientIDsSequence", "PatientComments", "MedicalRecordLocator", "AccessionNumber",
    "InstitutionName", "InstitutionAddress", "InstitutionalDepartmentName", "StationName",
    "ReferringPhysicianName", "ReferringPhysicianAddress", "ReferringPhysicianTelephoneNumbers",
    "PerformingPhysicianName", "NameOfPhysiciansReadingStudy", "OperatorsName",
    "RequestingPhysician", "StudyID", "StudyDescription", "SeriesDescription",
})


def identifier_tags(ds) -> list[str]:
    return sorted({e.keyword or str(e.tag) for e in ds.iterall()
                   if not e.is_empty and (e.is_private or e.VR == "PN" or e.keyword in IDENTIFIERS)})


def remove_identifiers(data: bytes) -> tuple[bytes, list[str]]:
    """입력 bytes를 변경하지 않고, 중첩 시퀀스·private·PN 태그도 제거한다.

    SOP/Series UID와 검사일·픽셀은 보존한다. 전체 DICOM 익명화 프로파일 구현이 아니다.
    """
    from core.dicom_io import InvalidDicomError

    try:
        ds = pydicom.dcmread(BytesIO(data))
        removed = sorted({e.keyword or str(e.tag) for e in ds.iterall()
                          if e.is_private or e.VR == "PN" or e.keyword in IDENTIFIERS})
        if not removed:
            return data, []

        def strip(dataset):
            for element in list(dataset):
                if element.is_private or element.VR == "PN" or element.keyword in IDENTIFIERS:
                    del dataset[element.tag]
                elif element.VR == "SQ":
                    for child in element.value:
                        strip(child)

        strip(ds)
        out = BytesIO()
        ds.save_as(out, enforce_file_format=True)
        return out.getvalue(), removed
    except Exception as exc:
        raise InvalidDicomError("개인정보 태그를 제거하지 못했습니다. DICOM 파일을 확인하세요.") from exc
