"""DICOM 클릭·드래그·측정용 로컬 Streamlit 컴포넌트."""
import base64
from pathlib import Path

import cv2
from streamlit.components.v1 import declare_component

canvas = declare_component("dicom_canvas", path=str(Path(__file__).parent))


def image_canvas(image, **kwargs):
    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise ValueError("뷰어 이미지를 만들 수 없습니다.")
    return canvas(image=base64.b64encode(encoded).decode("ascii"), **kwargs)
