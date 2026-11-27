"""표시 좌표와 원본 픽셀 좌표를 사용하는 뷰어 계산."""
import math


def roi_from_points(start, end, shape):
    """두 드래그 끝점을 영상 안의 정수 ROI로 변환한다."""
    h, w = shape
    if len(start) != 2 or len(end) != 2 or not all(math.isfinite(v) for v in (*start, *end)):
        raise ValueError("ROI 좌표를 확인하세요.")
    x = max(0, min(w - 1, math.floor(min(start[0], end[0]))))
    y = max(0, min(h - 1, math.floor(min(start[1], end[1]))))
    right = max(x + 1, min(w, math.ceil(max(start[0], end[0])) + 1))
    bottom = max(y + 1, min(h, math.ceil(max(start[1], end[1])) + 1))
    return {"x": x, "y": y, "w": right - x, "h": bottom - y}


def viewport(shape, zoom, x, y):
    height, width = shape
    crop_height = max(1, round(height / zoom))
    crop_width = max(1, round(width / zoom))
    left = round((width - crop_width) * x / 100)
    top = round((height - crop_height) * y / 100)
    return left, top, crop_width, crop_height


def measure_distance(points, pixel_spacing):
    """원본 좌표 두 점의 (길이, 단위). PixelSpacing은 행·열 순서(mm)."""
    dx = points[1][0] - points[0][0]
    dy = points[1][1] - points[0][1]
    try:
        row, col = map(float, pixel_spacing)
        if math.isfinite(row) and math.isfinite(col) and row > 0 and col > 0:
            return math.hypot(dx * col, dy * row), "mm"
    except (TypeError, ValueError):
        pass
    return math.hypot(dx, dy), "px"
