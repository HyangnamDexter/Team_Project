# 🔍 [C 담당] 이물질(금속) 의심 영역 찾기 기능

## 한 줄 요약
척추 X-ray(VinDr-SpineXR)에서 **아주 밝게 보이는 금속 물체**(목걸이, 단추, 수술 클립, 철사 등)를 찾아 **사각형 박스 위치**를 돌려주는 함수를 만든다.
딥러닝은 쓰지 않는다. **OpenCV + numpy 영상처리만** 사용한다. (학습 필요 없음)

## 왜 하나요?
- 우리 팀 특화 기능(영상 품질 점검)의 확장
- 몸 밖 물체(목걸이 등)가 찍히면 → "재촬영 요청" 사유로 사용
- 실제 진단용이 아님. 화면 표시 이름은 **"고밀도 이물질 의심 영역"**

---

## 만들 함수

파일: `core/quality.py`

```python
def detect_foreign_objects(pixels: np.ndarray,
                           percentile: float = 99.5,
                           min_area: int = 30,
                           max_area_ratio: float = 0.02) -> dict:
```

### 입력
- `pixels`: `core.dicom_io.load_dicom()`이 돌려준 **원시 픽셀 2D 배열** (float 또는 int)
- ⚠️ 윈도우잉한 uint8 영상이 **아님**. 윈도우잉하면 밝은 값이 255로 잘려서 금속과 뼈가 구분이 안 됨
- 입력 배열은 절대 수정하지 않는다 (복사해서 사용)

### 출력
```python
{
  "objects": [
     {"x": 120, "y": 340, "w": 25, "h": 60,   # 박스 위치 (원본 픽셀 좌표)
      "area": 900,                            # 덩어리 픽셀 수
      "mean": 3950.0,                         # 덩어리 평균 밝기 (원시값)
      "shape": "long"}                        # "round" | "long" | "other"
  ],
  "steps": {                                  # 화면에 단계별로 보여줄 중간 결과 (uint8, 0 또는 255)
     "threshold": mask1,
     "cleaned":   mask2,
  }
}
```
- 아무것도 못 찾으면 `"objects": []`
- 잘못된 입력(2D가 아님, 빈 배열, percentile이 0~100 밖)은 `ValueError`

---

## 처리 순서 (기본 버전)

| 순서 | 할 일 | OpenCV / numpy |
|---|---|---|
| 1 | 밝기 기준값 계산: 상위 0.5% 밝기 | `np.percentile(pixels, percentile)` |
| 2 | 기준보다 밝은 곳만 흰색(255)으로 | `pixels >= 기준값` → uint8 마스크 |
| 3 | 작은 점 잡음 제거 | `cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)` |
| 4 | 끊긴 부분 이어 붙이기 | `cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)` |
| 5 | 덩어리별로 나누고 박스·면적 구하기 | `cv2.connectedComponentsWithStats(mask)` |
| 6 | 크기로 거르기 | 면적 `< min_area` 이면 잡음, `> 전체 × max_area_ratio` 이면 뼈로 보고 제외 |
| 7 | 모양 분류 | 길쭉함(긴 변/짧은 변) ≥ 3 → `"long"`, 원형도(4π·면적/둘레²) ≥ 0.7 → `"round"`, 나머지 `"other"` |

## 개선 버전 (기본 버전이 끝나면)
- 1번 전에 **Top-hat** (`cv2.MORPH_TOPHAT`, 큰 커널) → 뼈 같은 큰 구조를 빼고 작고 밝은 것만 남김
- 영상 가장자리에 붙은 작은 덩어리 제외 → 좌우 표시 마커(L/R 글자) 오검출 줄이기
- 기본 버전과 개선 버전을 같은 영상으로 비교 → 결과를 `docs/experiments/`에 표로 정리

---

## 테스트 (`tests/test_foreign_objects.py`)
진짜 DICOM 없이 **가짜 영상**으로 확인한다.
- [ ] 회색 배경(값 1000) + 가운데 밝은 사각형(값 4000) → 박스 1개, 위치가 사각형과 거의 같음
- [ ] 밝은 가는 막대 → `shape == "long"`
- [ ] 밝은 원 → `shape == "round"`
- [ ] 아주 작은 밝은 점 1개 → 결과 없음 (잡음으로 제거)
- [ ] 아무것도 없는 영상 → `objects == []`
- [ ] 3D 배열, 빈 배열 → `ValueError`
- [ ] 함수 실행 후 입력 배열이 그대로인지

## 완료 조건
- [ ] `detect_foreign_objects` 구현
- [ ] 위 테스트 통과 (`pytest`)
- [ ] 브랜치 `feat/<이슈번호>-foreign-objects` → PR → 리뷰 1명

## 참고
- 뷰어 화면 연결(박스 표시, 클릭하면 ROI로 확대)은 B와 같이 한다
- "재촬영 요청" 연결은 A의 상태 전이 규칙(`docs/workflow.md`)을 따른다

---

## 🤖 AI(Gemini 등)에게 붙여넣을 요청문

```
Python, numpy, OpenCV(cv2)만 사용해서 함수를 하나 만들어줘. 딥러닝은 쓰지 마.

목적: 척추 X-ray DICOM의 원시 픽셀 2D 배열에서 아주 밝은 금속 물체 영역을 찾아 박스로 반환.

함수: detect_foreign_objects(pixels, percentile=99.5, min_area=30, max_area_ratio=0.02) -> dict

규칙:
- pixels는 float 또는 int 2D numpy 배열(윈도우잉 전 원시값). 입력은 수정하지 말고 복사해서 사용.
- 2D가 아니거나 비어 있거나 percentile이 0~100 밖이면 ValueError.
- 순서: np.percentile로 기준값 → 기준 이상을 255인 uint8 마스크로 → MORPH_OPEN(3x3) → MORPH_CLOSE(5x5)
  → connectedComponentsWithStats → 면적이 min_area 미만이거나 전체 픽셀의 max_area_ratio 초과면 제외.
- 각 덩어리의 shape: minAreaRect의 긴 변/짧은 변 >= 3 이면 "long",
  findContours로 구한 원형도 4*pi*area/perimeter^2 >= 0.7 이면 "round", 아니면 "other".
- 반환: {"objects": [{"x","y","w","h","area","mean","shape"}...],
         "steps": {"threshold": 열기 전 마스크, "cleaned": 닫기 후 마스크}}
  mean은 그 덩어리 위치의 원시 pixels 평균. 숫자는 파이썬 int/float로.

pytest 테스트도 만들어줘: 회색 배경(1000)에 밝은 사각형(4000) 1개 검출, 가는 막대는 long,
원은 round, 작은 점 하나는 제거, 빈 영상은 [], 3D/빈 배열은 ValueError, 입력 배열이 바뀌지 않음.
```
