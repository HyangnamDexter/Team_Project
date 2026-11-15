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
