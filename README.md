# 교육용 웹 PACS – 척추 X-ray 품질 점검과 임플란트 탐지

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
  detector.py       임플란트 탐지 모델 실행 (ONNX)  (A, 학습 C)
services/           Firebase 연동                  (A, B)
  auth.py, db.py    로그인, Firestore 연결
  cases.py          케이스 등록 (품질 점검 + 자동 탐지)
  review.py         배정·상태 변경
  ground_truth.py   VinDr 정답 박스
  detection.py      자동 탐지 결과
scripts/            데이터 준비·정답 넣기·탐지 확인 (prepare_spinexr, import_ground_truth, check_detection)
models/             탐지 모델 파일 (저장소에 올리지 않음)
deploy/             서버 자동 실행 설정
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
- 출처와 이용 조건은 확인 후 `docs/data.md`에 기록
- 영상 파일은 저장소에 올리지 않는다 (`data/` 제외).

## JCloud 배포

| 항목 | 값 |
|---|---|
| 서버 | JCloud `micro`: vCPU 2개, 메모리 2GB, 디스크 10GB, Ubuntu |
| 접속 주소 | `http://<서버 외부 IP>:8501` |
| 자동 실행 | systemd (`deploy/pacs.service`). 재부팅되거나 앱이 죽어도 다시 켜진다 |
| 탐지 모델 | onnxruntime (PyTorch 없이 실행. 메모리·디스크를 아끼려고) |

아래에서 `<IP>`는 서버 외부 IP, `ubuntu`는 서버 사용자 이름이다 (이미지에 따라 다를 수 있음).
내 컴퓨터에서 실행하는 명령은 **PowerShell**, 서버에서 실행하는 명령은 **서버 터미널(ssh 접속 후)**이다.

### 1. 접속 (내 컴퓨터)
SSH 키는 저장소 밖(`C:\Users\<이름>\.ssh\`)에 두고, 나만 읽을 수 있게 권한을 바꾼다.
```powershell
icacls $HOME\.ssh\jcloud.pem /inheritance:r /grant:r "$($env:USERNAME):R"
ssh -i $HOME\.ssh\jcloud.pem ubuntu@<IP>
```

### 2. 스왑 2GB 만들기 (서버, 처음 한 번)
메모리가 잠깐 모자랄 때 앱이 꺼지지 않도록 디스크 일부를 예비 메모리로 쓴다.
```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h    # Swap 2.0Gi 가 보이면 성공
```

### 3. 코드와 패키지 설치 (서버, 처음 한 번)
```bash
sudo apt update && sudo apt install -y git python3-venv
git clone https://github.com/HyangnamDexter/Team_Project.git
cd Team_Project
python3 -m venv venv
venv/bin/pip install --no-cache-dir -r requirements.txt   # 캐시를 남기지 않아 디스크 절약
mkdir -p ~/secrets ~/pacs-storage
```
Python 3.10 이상이면 된다 (Ubuntu 22.04 / 24.04).

### 4. 비밀 정보와 모델 파일 복사 (내 컴퓨터 → 서버)
git 에 없는 파일 3개를 `scp`로 직접 복사한다. **카톡·메일로 주고받지 않는다.**
```powershell
scp -i $HOME\.ssh\jcloud.pem C:\Users\<이름>\firebase\key.json ubuntu@<IP>:~/secrets/key.json
scp -i $HOME\.ssh\jcloud.pem .streamlit\secrets.toml ubuntu@<IP>:~/Team_Project/.streamlit/secrets.toml
scp -i $HOME\.ssh\jcloud.pem models\implant.onnx ubuntu@<IP>:~/Team_Project/models/implant.onnx
```
모델 파일이 아직 없으면 세 번째 줄은 건너뛴다 (탐지 없이 등록된다).

서버에서 경로를 서버 기준으로 고치고, 나만 읽을 수 있게 한다.
```bash
nano ~/Team_Project/.streamlit/secrets.toml
#   [firebase] service_account_path = "/home/ubuntu/secrets/key.json"
#   [storage]  image_dir = "/home/ubuntu/pacs-storage"
#   [model]    path = "models/implant.onnx"
chmod 600 ~/secrets/key.json ~/Team_Project/.streamlit/secrets.toml
```

### 5. 포트 열기 (JCloud 웹 화면)
인스턴스의 **보안 그룹**에 **TCP 8501 인바운드** 규칙을 추가한다 (SSH용 22번은 이미 열려 있어야 접속된다).

### 6. 실행 확인 → 자동 실행 등록 (서버)
먼저 직접 켜서 브라우저로 `http://<IP>:8501` 이 열리는지 본다. 확인되면 `Ctrl+C`로 끈다.
```bash
cd ~/Team_Project
venv/bin/streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true
```
자동 실행으로 등록한다.
```bash
sudo cp deploy/pacs.service /etc/systemd/system/pacs.service
sudo systemctl daemon-reload
sudo systemctl enable --now pacs
systemctl status pacs      # active (running) 이면 성공
```

### 7. 업데이트 (main 에 새 코드가 머지됐을 때, 서버)
```bash
cd ~/Team_Project && git pull
venv/bin/pip install --no-cache-dir -r requirements.txt   # requirements.txt 가 바뀌었을 때만
sudo systemctl restart pacs
```

### 문제 해결 (서버)
| 증상 | 확인 |
|---|---|
| 브라우저에서 안 열림 | `systemctl status pacs` 가 running 인지, 보안 그룹에 8501 이 열렸는지 |
| 앱 오류 | `journalctl -u pacs -n 100` (마지막 100줄 기록) |
| 느리거나 자주 꺼짐 | `free -h` 로 메모리·스왑 확인 |
| 디스크 부족 | `df -h /` 로 남은 용량 확인. 등록 영상은 `~/pacs-storage` 에 쌓인다 |
| 로그인 실패 | `secrets.toml` 의 `web_api_key`, `service_account_path` 경로 |

### 주의
- 서버에는 **시연에 쓸 영상만** 웹 화면에서 등록한다. VinDr 영상 전체를 서버에 올리지 않는다 (데이터 이용 약관, 디스크 10GB).
- `secrets.toml`, `key.json`, `implant.onnx` 는 서버와 각자 컴퓨터에만 둔다. git 에 올리지 않는다.

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
