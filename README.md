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
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate
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
- X-ray: RSNA Pneumonia Detection Challenge (Kaggle)
- CT·MRI: TCIA 공개 컬렉션 (후보 선정 중)
- 출처와 이용 조건은 확인 후 `docs/data.md`에 기록
- 영상 파일은 저장소에 올리지 않는다 (`data/` 제외).

## JCloud 배포
TODO(A): 접속 확인 후 작성

## B 첫 주 작업: DICOM 읽기·윈도우잉

작업 브랜치: `KIMJOUN`. `load_dicom`, `auto_window`, `apply_window`는
Firebase 없이 실행할 수 있습니다. Python 3.12 이상 환경을 권장합니다.
Linux/WSL에서 프로젝트 전용 환경을 준비하려면:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest tests/test_dicom_io.py -q
python -m pytest -q
python -m streamlit run app.py
```

`tests/test_dicom_io.py`는 합성 DICOM을 생성하므로 샘플·인증 키가 필요하지 않습니다.
실제 입력은 단일 프레임 CR/DX/CT/MR 흑백 영상이며, 압축 영상은 별도 디코더가
필요할 수 있습니다. 시리즈·ZIP 읽기는 다음 주 구현 범위입니다.

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

### 아직 필요한 수동 준비

- A에게 Firebase 웹 API 키와 서비스 계정 파일을 받아 설정합니다.
  `.streamlit/secrets.toml.example`을 `secrets.toml`로 복사하되,
  서비스 계정 JSON은 저장소 밖에 두고 경로만 입력합니다.
- [RSNA Kaggle 데이터](https://www.kaggle.com/c/rsna-pneumonia-detection-challenge/data)와
  [이용 규칙](https://www.kaggle.com/c/rsna-pneumonia-detection-challenge/rules)을
  본인 계정으로 확인하고 DICOM 10장을 `data/sample/`에 둡니다.
  [RSNA 원 제공자 조건](https://www.rsna.org/-/media/files/rsna/education/ai-resources-and-training/ai-image-challenge/pneumonia-detection-challenge-terms-of-use-and-attribution.pdf)도
  확인하고, C 담당 `docs/data.md`에 실제 사용 파일·출처·이용 조건을 전달합니다.
  합성 테스트 파일을 RSNA 샘플로 간주하지 않습니다.
- 실제 샘플로 읽기·표시 변환을 확인하고, 파일과 비밀키가 Git에서 제외되는지 확인합니다.
- PR #1은 코드 인터페이스 변경도 포함하므로 문서만의 변경으로 간주하지 말고
  `auto_window`·시리즈 처리 약속까지 확인한 후 본인 GitHub 계정으로 리뷰합니다.
