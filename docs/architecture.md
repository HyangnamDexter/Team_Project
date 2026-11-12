# 시스템 구조와 화면 흐름

> 보고서 **02 서비스·데이터 설계**(구성도, 화면 흐름)의 근거 자료입니다.

## 1. 시스템 구성도

```mermaid
flowchart LR
    subgraph Client["사용자 브라우저"]
        U["방사선사 / 판독의 / 관리자"]
    end

    subgraph JCloud["JCloud 서버 (Ubuntu VM)"]
        direction TB
        ST["Streamlit 앱<br/>app.py · pages/"]
        SV["services/<br/>auth.py · db.py"]
        CO["core/<br/>dicom_io · algorithms<br/>quality · workflow"]
        FS[("storage/<br/>원본 DICOM · 결과 PNG")]
        ST --> SV
        ST --> CO
        SV --> CO
        SV --> FS
    end

    subgraph Firebase["Firebase (team-project-e3003)"]
        AU["Authentication<br/>이메일/비밀번호"]
        DB[("Cloud Firestore<br/>asia-northeast3")]
    end

    U -- "HTTP :8501" --> ST
    SV -- "REST API<br/>signInWithPassword<br/>(웹 API 키)" --> AU
    SV -- "firebase-admin<br/>(서비스 계정 키)" --> DB
```

| 구성 요소 | 기술 | 역할 |
|---|---|---|
| 화면 | Streamlit | 로그인, 목록, 뷰어, 검토 화면 |
| 서비스 계층 | `services/` | Firebase 연동, 파일 저장, **권한 검사 후** 데이터 변경 |
| 순수 로직 | `core/` | DICOM 변환, 알고리즘, 품질 점검, 상태 규칙. Firebase와 무관하게 pytest로 테스트할 수 있음 |
| 인증 | Firebase Authentication | 이메일/비밀번호 확인 |
| DB | Cloud Firestore | 업무 데이터 영구 저장 |
| 파일 | 서버 디스크 | 영상 파일 영구 저장 |

## 2. 모듈 구조와 의존 방향

```mermaid
flowchart TB
    P["pages/*.py<br/>(화면)"] --> S["services/<br/>(Firebase·저장·권한 적용)"]
    P --> C1["core/dicom_io.py"]
    P --> C2["core/algorithms.py"]
    S --> C4["core/workflow.py<br/>(상태·권한 규칙)"]
    S --> C3["core/quality.py"]
    S --> C1
    C3 --> C2
```

- `core/`는 Streamlit과 Firebase를 import하지 않는다. 그래서 C가 Firebase 설정 없이 개발하고 테스트할 수 있다.
- 화면(`pages/`)은 Firestore를 직접 부르지 않고 항상 `services/`를 거친다. 그래야 권한 검사를 빠뜨리지 않는다.

## 3. 화면 흐름

```mermaid
flowchart TD
    L["로그인"] -->|성공| H["홈<br/>내 작업 요약"]
    L -->|실패| L
    H --> C["케이스 목록<br/>검색·필터"]
    H --> Q["품질 점검 현황<br/>(관리자)"]

    C -->|방사선사| UP["DICOM 업로드"]
    UP --> QC{"자동 점검"}
    QC -->|통과| CD["케이스 상세"]
    QC -->|실패| RJ["재촬영 요청<br/>사유 표시"]
    RJ -->|재등록| UP

    C --> CD
    CD -->|관리자| AS["판독의 배정"]
    CD -->|판독의| V["영상 뷰어<br/>윈도우잉·확대·이동"]
    V --> PR["알고리즘 적용<br/>원본/결과 비교"]
    PR --> ROI["ROI 분석<br/>통계·히스토그램"]
    PR --> HIS["처리 이력<br/>불러오기·재실행"]
    CD -->|판독의| RV["검토<br/>메모·리포트"]
    RV -->|완료| DN["완료"]
    DN -->|관리자 재검토| RV

    H -.->|로그아웃| L
```

| 페이지 파일 | 화면 |
|---|---|
| `app.py` | 로그인, 홈 |
| `pages/1_Cases.py` | 케이스 목록, 업로드, 케이스 상세, 배정 |
| `pages/2_Viewer.py` | 영상 뷰어, 알고리즘, ROI, 처리 이력 |
| `pages/3_Review.py` | 메모·리포트, 상태 변경, 케이스 이력 |

## 4. 주요 시퀀스

### 4.1 로그인

```mermaid
sequenceDiagram
    actor U as 사용자
    participant ST as Streamlit
    participant AU as services/auth.py
    participant FA as Firebase Auth
    participant DB as Firestore

    U->>ST: 이메일·비밀번호 입력
    ST->>AU: sign_in(email, pw)
    AU->>FA: POST signInWithPassword
    alt 인증 실패
        FA-->>AU: 400 INVALID_LOGIN_CREDENTIALS
        AU-->>ST: 예외
        ST-->>U: "이메일 또는 비밀번호가 올바르지 않습니다"
    else 인증 성공
        FA-->>AU: idToken, localId(UID)
        AU->>DB: users/{UID} 조회
        alt 문서 없음
            AU-->>ST: 예외
            ST-->>U: "역할이 지정되지 않은 계정입니다"
        else 역할 있음
            DB-->>AU: role
            AU-->>ST: {uid, email, role}
            ST->>ST: session_state.user 저장
            ST-->>U: 홈 화면
        end
    end
```

### 4.2 DICOM 등록과 자동 품질 점검 (특화 기능)

```mermaid
sequenceDiagram
    actor R as 방사선사
    participant ST as 케이스 화면
    participant SV as services
    participant DI as core/dicom_io
    participant QA as core/quality
    participant FS as storage/
    participant DB as Firestore

    R->>ST: .dcm 파일 업로드
    ST->>SV: register_image(user, file)
    SV->>SV: require_role(radiographer)
    SV->>DI: load_dicom(file)
    alt DICOM 아님 / 픽셀 없음
        DI-->>SV: InvalidDicomError
        SV-->>ST: 실패 사유
        ST-->>R: "DICOM 형식이 아닙니다"
    else 정상
        DI-->>SV: 픽셀, 헤더
        SV->>QA: tag_check(헤더)
        SV->>QA: image_quality(윈도우잉된 영상)
        QA-->>SV: qc 결과 (통과/실패, 사유)
        SV->>SV: 개인정보 태그 제거
        SV->>FS: original.dcm.tmp 저장
        SV->>DB: case, image, case_history 기록
        alt DB 기록 실패
            SV->>FS: .tmp 삭제
            SV-->>ST: 저장 실패
        else 성공
            SV->>FS: .tmp → original.dcm
            SV-->>ST: 상태 (접수 또는 재촬영 요청), qc 사유
            ST-->>R: 점검 결과 표시
        end
    end
```

### 4.3 알고리즘 처리와 이력 저장

```mermaid
sequenceDiagram
    actor D as 판독의
    participant ST as 뷰어 화면
    participant SV as services
    participant AL as core/algorithms
    participant FS as storage/
    participant DB as Firestore

    D->>ST: 알고리즘·파라미터·ROI 선택 후 실행
    ST->>SV: run_processing(user, image_id, algo, params, roi)
    SV->>SV: 담당자 확인, 상태가 검토 중인지 확인
    SV->>DB: 같은 run_key가 최근 10초 안에 있나?
    alt 중복 요청
        DB-->>SV: 있음
        SV-->>ST: 기존 결과 반환
    else 새 요청
        SV->>AL: apply_*(영상, params)
        alt 파라미터 오류
            AL-->>SV: ValueError
            SV-->>ST: 오류 사유 (기록 안 함)
        else 성공
            AL-->>SV: 결과 영상
            SV->>AL: roi_stats(결과, roi)
            SV->>FS: 결과 PNG 저장
            SV->>DB: processing_logs 기록
            SV-->>ST: 결과, 특징값
            ST-->>D: 원본/결과 비교, 히스토그램
        end
    end
```

## 5. 배포 구성 (JCloud)

| 항목 | 값 |
|---|---|
| 서버 | JCloud Ubuntu VM |
| 실행 | `streamlit run app.py --server.address 0.0.0.0 --server.port 8501` (백그라운드) |
| 열 포트 | 22 (SSH), 8501 (Streamlit) |
| 비밀 정보 | 서버에 직접 복사: `.streamlit/secrets.toml`, 서비스 계정 JSON |
| 영상 저장 | 서버 영구 디스크의 `storage/` |
| 업데이트 | `git pull` 후 앱 재시작 |

JCloud 접속이 확인되면 README의 배포 절차를 채운다.
