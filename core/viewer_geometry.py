"""표시 좌표와 원본 픽셀 좌표를 사용하는 뷰어 계산."""
import math


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
