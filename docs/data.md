# 데이터 출처 및 이용 조건

> 교육용 웹 PACS – 척추 X-ray DICOM 영상 품질 점검 및 고밀도 이물질 검출  
> 이 문서는 보고서 **05 데이터 출처·이용 조건**의 근거 자료입니다.  
> ⚠️ 본 프로젝트의 모든 영상·모델 결과는 교육 및 실습용 모의 업무 목적이며, 실제 의료 진단이나 임상 성능 주장에 사용하지 않습니다.

---

## 1. 주요 데이터셋 출처: VinDr-SpineXR (v1.0.0)

본 프로젝트는 **척추 X-ray(DX/CR) 전용**으로 범위를 확정하였으며(CT·MRI 제외), 영상 품질 기준 검증, 영상처리 알고리즘 비교 실험, 고밀도 이물질(외과적 임플란트) 검출 실험의 핵심 데이터로 **VinDr-SpineXR**을 사용하였다.

| 항목 | 내용 |
| :--- | :--- |
| **데이터셋명** | VinDr-SpineXR: A large annotated medical image dataset for spinal lesions detection and classification from radiographs (Version 1.0.0) |
| **공식 배포처 (URL)** | PhysioNet — https://physionet.org/content/vindr-spinexr/1.0.0/ |
| **원저자 / 기관** | Hieu H. Pham, Hieu T. Nguyen, Ha Q. Nguyen 외 / Vingroup Big Data Institute (VinBigData, Medical Imaging Center, Vietnam) 및 Hanoi Medical University Hospital · Hospital 108 |
| **원본 데이터 규모** | 총 10,468장 척추 X-ray DICOM (5,000건 검사, 13종 병변 바운딩 박스 및 정상 `No finding` 라벨 포함) |
| **본 프로젝트 활용 소견** | `Surgical implant` (고밀도 금속 이물질 의심 영역 대리 라벨, 클래스 0) 및 `No finding` (정상 대조군) |

### 단위 테스트용 보조 데이터
| 용도 | 출처 | 비고 |
| :--- | :--- | :--- |
| **오프라인 단위 테스트** | `pydicom` 내장 익명 예제 파일 및 합성 `numpy` 배열 | 외부 다운로드 없이 `pytest` 검증 전용 |

---

## 2. 공식 인용 문구 (Citation)

VinDr-SpineXR 데이터셋 및 PhysioNet 플랫폼 활용 시 아래 공식 인용을 준수한다.

1. **PhysioNet 데이터셋 인용 (필수)**:
   > Pham, H. H., Nguyen Trung, H., & Nguyen, H. Q. (2021). *VinDr-SpineXR: A large annotated medical image dataset for spinal lesions detection and classification from radiographs* (version 1.0.0). PhysioNet. https://doi.org/10.13026/q45h-5h59
2. **원논문 인용 (MICCAI 2021)**:
   > Nguyen, H. T., Pham, H. H., Nguyen, N. T., Nguyen, H. Q., Huynh, T. Q., Dao, M., & Vu, V. (2021). *VinDr-SpineXR: A deep learning framework for spinal lesions detection and classification from radiographs.* In *Medical Image Computing and Computer Assisted Intervention – MICCAI 2021* (pp. 291–301). Springer. https://doi.org/10.1007/978-3-030-87240-3_28
3. **PhysioNet 플랫폼 표준 인용**:
   > Goldberger, A., Amaral, L., Glass, L., Hausdorff, J., Ivanov, P. C., Mark, R., Mietus, J. E., Moody, G. B., Peng, C.-K., & Stanley, H. E. (2000). *PhysioNet: Components of a new research resource for complex physiologic signals.* Circulation, 101(23), e215–e220.

---

## 3. 라이선스 및 이용 조건 (PhysioNet DUA 준수)

VinDr-SpineXR은 **PhysioNet Restricted Health Data License 1.5.0** (PhysioNet Credentialed Health Data Use Agreement, MIT Laboratory for Computational Physiology)이 적용되는 제한 접근 의료 데이터이다.

| 의무 조항 | 프로젝트 내 준수 방법 |
| :--- | :--- |
| **개인·기관 재식별 금지** | 원본 DICOM은 이미 익명화되어 있으나, 환자·기관 추정 시도를 일절 금지하며 헤더 로딩 시(`core/dicom_io.py`) 환자 식별 값 대신 태그 존재 여부만 기록 |
| **재배포·제3자 공유 금지** | 팀원 각자 PhysioNet 계정 가입 및 데이터 사용 동의서(DUA) 개별 서명 후 공식 `wget2` 명령(`scripts/download_spinexr.py`, `scripts/prepare_spinexr.py`)으로 직접 다운로드 |
| **공개 저장소 업로드 금지** | `.gitignore`에 `data/*` (`data/sample/.gitkeep` 제외), `storage/`, `*.pt`, `*.onnx` 등록 → GitHub 저장소·PR에 DICOM, PNG, 압축파일, 모델 가중치 및 노트북 출력 이미지 절대 커밋 금지 |
| **교육·연구 목적 한정** | 합법적 교육·학술 연구 목적 외 상업적·임상 진단 목적 사용 금지. UI 및 모든 실험 문서에 *"교육용 모의 케이스 – 실제 진단용 아님"* 명시 |
| **보안 유지 및 PHI 신고** | 로컬 디스크 및 비공개 Google Drive(Colab 학습용) 접근 권한을 본인 전용으로 제한; 미익명화 정보 발견 시 `PHI-report@physionet.org` 즉시 보고 |

---

## 4. 프로젝트 데이터 분할 및 사용 파일 요약

`scripts/prepare_spinexr.py` (`seed=42`, `normal_per_implant=1.0`, `val_ratio=0.2`, `--max-size 1024`)를 사용해 데이터 누수 없는 분할과 정상:임플란트 균형 구성을 적용하였다.

### 4.1 분할별 계획 vs 최종 변환·사용 수량
| 분할 (`split`) | 계획 영상 수 (임플란트 / 정상) | 최종 변환·사용 영상 수 (임플란트 / 정상) | 정답 박스 수 (`Surgical implant`) | 고유 검사 수 (`study_id`) | 비고 |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **`train`** | 412 (205 / 207) | **404** (205 / 199) | 348 | 286 | `study_id` 단위 분할 (`train ∩ val = 0`) |
| **`val`** | 102 (52 / 50) | **100** (52 / 48) | 81 | 72 | 동일 검사 영상이 train/val 양쪽에 혼입되지 않음 |
| **`test`** | 128 (64 / 64) | **122** (58 / 64) | 96 | 88 | 원 제공자 공식 `test.csv` 그대로 유지 |
| **합계** | **642** (321 / 321) | **626** (315 / 311) | **525** | **446** | 변환 제외 16장 (아래 원인 실측 확인) |

### 4.2 계획 642장 → 최종 626장 (16장 변환 제외 원인 확인)
로컬 `data/vindr-spinexr/split.csv`(642장)와 최종 변환 결과(626장)를 대조해 누락된 **16장**(`train` 정상 8장, `val` 정상 2장, `test` 임플란트 6장)의 원본 파일 존재 여부와 `core.dicom_io.load_dicom()` 예외 원인을 직접 검증하였다.

- **원본 파일 존재 및 픽셀 디코딩 여부**: **16장 전부 로컬에 원본 `.dicom` 파일이 존재**하며, `pydicom`으로 JPEG 2000 무손실 픽셀 배열(`ds.pixel_array`, `uint16`) 디코딩도 정상 완료됨 (미다운로드나 디코더 패키지 누락이 아님).
- **정확한 실패 원인 (16/16건 100% 동일)**: 해당 16장의 DICOM 메타데이터 `WindowCenter`·`WindowWidth` 태그 문자열 말미에 **비표준 쉼표(`,`)**가 포함되어 있음(예: `WindowCenter='36483,'`, `WindowWidth='58567,'` / `'44394,'`, `'48633,'`). 이로 인해 `core/dicom_io.py::_number()`의 `float(value)` 호출에서 `ValueError: could not convert string to float`가 발생하고 `load_dicom()`이 `InvalidDicomError`로 래핑하여 `prepare_spinexr.py convert` 단계에서 자동 제외됨.
- **정상:임플란트 균형**: 최종 626장은 임플란트 315장(50.3%) 대 정상 311장(49.7%)으로 약 1:1 균형을 유지한다.

---

## 5. 대표 실험용 척추 X-ray 5종 목록

품질 기준값 실측 검증([experiments/qc_thresholds.md](experiments/qc_thresholds.md))과 영상처리 알고리즘 파라미터 비교 실험([experiments/algorithms_comparison.md](experiments/algorithms_comparison.md))에 공통 사용한 대표 5종(`test` 분할 선정)은 아래와 같다.

| 유형 | `image_id` | 분할 / 원라벨 | 해상도 (W×H) | 주요 실험 용도 |
| :--- | :--- | :---: | :---: | :--- |
| **정상** | `3414ac0c4bf2aded206569cbeaaead3e` | `test` / normal | 2517×3028 | 정상 기준선(QC 통과), 알고리즘 기본 화질·엣지 보존 기준 비교 |
| **흐림** | `7429a824ee947f7a90cacf0a9a5438e7` | `test` / normal | 2517×3028 | `선명도 부족`(헤더 WL `S=14.25`) 판정 검증, CLAHE 지표 변화 및 Denoise 블러링 한계 확인 |
| **어두움** | `12dd72dad98c015e9f1c5767ff809a9b` | `test` / normal | 2869×2874 | `너무 어두움`(헤더 WL `B=24.39`) 판정 검증, CLAHE 암부 지표 변화 및 Otsu 배경 분리 확인 |
| **밝음** | `d062d5b91723e1a846496307ff81556a` | `test` / normal | 1422×3155 | `test` 정상 고휘도(자동 윈도우 `B=178.73`, 헤더 WL `B=92.37`) 검증, CLAHE 과증폭 한계 확인 |
| **임플란트** | `ddd5226f4b9c771e6510ae86c00d7304` | `test` / implant (5 boxes) | 1182×2497 | 금속 나사·로드·케이지 경계 보존(Bilateral/Median vs Gaussian) 및 이물질 미검출(FN) 사례 분석 |

---

## 6. 전체 사용 파일 목록 관리 및 재현 방법

PhysioNet Restricted Health Data License 1.5.0의 재배포 금지 원칙에 따라, 실제 DICOM 원본 파일과 626장 전체 파일 목록은 Git 저장소에 포함하지 않고 로컬 `data/` 폴더(`.gitignore` 적용)에서 아래와 같이 재현·관리한다.

1. **로컬 파일 목록 저장 위치**: `data/vindr-spinexr/split.csv` (필드: `image_id, study_id, split, label, n_boxes`, 총 642행)
2. **동일 데이터 분할 재현 명령** (고정 `seed=42`로 모든 팀원이 동일한 목록 생성):
   ```bash
   # 1) PhysioNet 공식 라벨 CSV 다운로드 후 분할 계획 생성
   python scripts/prepare_spinexr.py plan --normal-per-implant 1.0 --val-ratio 0.2 --seed 42

   # 2) DUA 서명된 본인 PhysioNet 계정으로 urls_split.txt 목록 다운로드 후 변환
   python scripts/prepare_spinexr.py convert --max-size 1024
   ```
3. **관련 실험 결과 및 재현 스크립트**:
   - 실험 일괄 재현 스크립트: `scripts/eval_experiments.py` (`qc` / `algorithms` / `opencv` / `all`)
   - 촬영 종류별 품질 기준값 실측 근거: [experiments/qc_thresholds.md](experiments/qc_thresholds.md)
   - 영상처리 알고리즘 파라미터 비교 실험: [experiments/algorithms_comparison.md](experiments/algorithms_comparison.md)
   - 이물질(금속·임플란트) 검출 방법 비교 및 오검출 분석: [experiments/foreign_objects_comparison.md](experiments/foreign_objects_comparison.md)
