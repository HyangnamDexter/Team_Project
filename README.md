# 교육용 웹 PACS – X-ray·CT·MRI DICOM 품질 점검

초급프로젝트 PBL 분반 Term Project. 공개 데이터와 모의 케이스로 동작하는 교육용 PACS이며, 실제 진단 용도가 아닙니다.

## 기술 스택
Python · Streamlit / 로그인: Firebase Authentication / DB: Cloud Firestore / 배포: JCloud

## 폴더 구조
```
app.py              진입 화면
pages/              Streamlit 화면 (케이스, 뷰어, 검토)
core/               순수 로직
  dicom_io.py       DICOM 읽기·윈도우잉            (B)
  algorithms.py     알고리즘 3종 + ROI 통계         (C)
  quality.py        특화 기능: 품질·태그 점검       (C, B)
  workflow.py       상태 전이·권한 규칙            (A)
services/           Firebase 연동 (auth, db)       (A, B)
tests/              pytest 테스트
docs/               설계 문서 (보고서에 활용)
```

## 로컬 실행
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .streamlit\secrets.toml.example .streamlit\secrets.toml   # 값 채우기
streamlit run app.py
```

## 테스트
```bash
pytest
```

## 협업 규칙
1. 작업은 **Issue**로 등록하고 담당자를 지정한다.
2. `main`에서 직접 작업하지 않고 브랜치를 만든다: `feat/<이슈번호>-<내용>`, `fix/<이슈번호>-<내용>`
3. 커밋 메시지에 무엇을 왜 바꿨는지 적는다. 예: `feat: CLAHE 알고리즘 추가 (#12)`
4. **PR**을 올리고 다른 팀원 1명 이상의 리뷰 승인 후 병합한다. PR 본문에 `Closes #이슈번호`.
5. 본인 GitHub 계정 이메일로 커밋한다: `git config user.email "<GitHub에 등록된 이메일>"`

## 보안
- `.streamlit/secrets.toml`, 서비스 계정 JSON은 **절대 커밋하지 않는다** (.gitignore 처리됨).
- 테스트 계정 비밀번호는 LMS 제출란에만 적는다.

## 데이터
- X-ray: [VinDr-SpineXR v1.0.0](https://physionet.org/content/vindr-spinexr/1.0.0/) (PhysioNet) – 척추 X-ray 10,466장, 13개 소견 박스 라벨 (수술 임플란트 포함)
  - 이용 조건: PhysioNet Restricted Health Data License 1.5.0
  - **팀원 각자** PhysioNet 가입 + 데이터 사용 동의서(DUA) 서명 후 직접 다운로드, 받은 파일은 다른 사람에게 넘기지 않는다
  - 배포한 웹앱에서는 로그인한 사용자만 영상을 볼 수 있게 한다
  - 전체(33GB) 대신 일부만 받는다 → `scripts/download_spinexr.py` 맨 위 안내 참고 (`data/vindr-spinexr/`에 저장)
  - 예비: RSNA Pneumonia Detection Challenge (Kaggle, 흉부 X-ray)
- CT·MRI: TCIA 공개 컬렉션 (후보 선정 중)
- 출처와 이용 조건은 확인 후 `docs/data.md`에 기록
- 영상 파일은 저장소에 올리지 않는다 (`data/` 제외).

## JCloud 배포
TODO(A): 접속 확인 후 작성

## DICOM 모듈 사용법

`load_dicom`, `auto_window`, `apply_window`는 단일 프레임 CR/DX/CT/MR 흑백 영상을
읽고 화면 표시용 배열로 변환합니다. 압축 영상은 별도 디코더가 필요할 수 있습니다.

```python
from core.dicom_io import load_dicom, apply_window

pixels, header = load_dicom("data/sample/example.dcm")
center, width = header["window_center"], header["window_width"]
if center is None or width is None:
    image = apply_window(pixels)
else:
    image = apply_window(pixels, center, width)
# image: uint8 2D. st.image(image, clamp=True) 등으로 표시.
```

Rescale 적용 후 MONOCHROME1은 관측 범위에서 반전합니다. 반환 윈도우 중심도
변환되므로 화면에서 추가 반전을 하지 않습니다. MONOCHROME1 반환값은
표시를 위해 변환된 값이므로 원래 물리량으로 해석하지 않습니다.
자동 윈도우는 기본 1~99 백분위이며 상수 영상은 회색으로 표시합니다.
윈도우 변환은 [DICOM LINEAR 규칙](https://dicom.nema.org/MEDICAL/dicom/2019a/output/chtml/part03/sect_C.11.2.html)을
따릅니다. VOI LUT·SIGMOID·presentation LUT는 현재 지원하지 않습니다.

### 샘플 데이터

- [RSNA Kaggle 데이터](https://www.kaggle.com/c/rsna-pneumonia-detection-challenge/data)와
  [이용 규칙](https://www.kaggle.com/c/rsna-pneumonia-detection-challenge/rules)을
  본인 계정으로 확인하고 DICOM 10장을 `data/sample/`에 둡니다.
  [RSNA 원 제공자 조건](https://www.rsna.org/-/media/files/rsna/education/ai-resources-and-training/ai-image-challenge/pneumonia-detection-challenge-terms-of-use-and-attribution.pdf)도
  확인하고, 실제 사용 파일·출처·이용 조건을 `docs/data.md`에 기록합니다.

## 뷰어·검토 기능 사용 순서

1. `streamlit run app.py` 실행 후 방사선사 계정으로 케이스를 등록합니다.
2. 관리자가 검토 화면에서 판독의를 배정하고, 담당 판독의가 **검토 시작**을 누릅니다.
3. 케이스 목록의 **영상 뷰어에서 열기**를 누르면 등록된 원본과 결과를 비교할 수 있습니다.
4. 뷰어의 **탐지 박스**에서 정답(초록), 모델(노랑), OpenCV(파랑)를 개별 표시합니다.
   모델/OpenCV 탐지는 검토 중인 본인 배정 케이스에서 실행·저장할 수 있습니다.
5. **ROI 선택** 도구로 드래그한 뒤 **ROI 영역만 처리**와 알고리즘을 선택하고 적용합니다.
   처리 결과는 PNG와 이력으로 저장됩니다. **처리 이력**에서 불러오기·같은 조건 재실행을 할 수 있습니다.
   같은 영상·WL·알고리즘·파라미터·ROI를 10초 내 다시 실행하면 기존 이력을 재사용합니다.
6. 검토 화면에서 탐지 박스를 골라 **맞음 / 오검출**을 저장하고, 메모·리포트를 작성합니다.
   새 탐지를 실행하면 이전 박스의 판정이 새 박스에 잘못 연결되지 않도록 실행 정보를 구분합니다.
   리포트를 저장한 담당 판독의는 **검토 완료**를 누를 수 있습니다.

모델 경로는 `.streamlit/secrets.toml`의 `[model] path`로 지정합니다. 미지정 시
`models/implant.onnx`, `models/best.onnx` 순서로 찾습니다. 프로젝트 밖의 파일은 `[model] path`로 명시합니다.
`conf=0.40`, NMS IoU `0.45`를 사용하며, 자동 윈도우→긴 변 1024→640 Letterbox 전처리를 적용합니다.
의존성은 `pip install -r requirements.txt`로 설치합니다. ONNX Runtime이 있는 환경은 이를 사용하고,
미설치 환경은 OpenCV DNN을 사용합니다. 모델 파일과 데이터는 Git에 올리지 않습니다.

초록 정답은 모델 예측이 아니라 VinDr 라벨입니다. `ground_truth`가 비어 있으면
`python scripts/import_ground_truth.py`로 로컬 CSV를 미리 확인한 뒤,
관리자가 `--write` 옵션으로 가져올 수 있습니다. 원본 SOPInstanceUID로 연결하며,
정답이 없는 일반 업로드 영상에는 **정답 데이터 없음**을 표시합니다.

등록 전에 직접 식별 태그·PN·private 태그를 중첩 시퀀스까지 제거하고 태그 이름만 QC에 기록합니다.
검사일과 영상 UID·픽셀은 보존하므로 전체 DICOM 익명화 프로파일이나 픽셀 내 문자 제거를 의미하지 않습니다.
`BurnedInAnnotation=YES`는 수동 확인 사유로 남깁니다. 기존 저장 영상은 소급 수정하지 않습니다.
VinDr 샘플의 `BodyPartExamined` 누락은 QC 경고로 기록하고 접수를 허용합니다.
픽셀 품질 실패·해결되지 않은 필수 촬영 종류 누락과는 구분합니다.
