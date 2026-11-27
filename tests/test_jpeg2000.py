"""VinDr-SpineXR 처럼 JPEG 2000 무손실로 압축된 DICOM 을 읽을 수 있는지 확인한다.

VinDr 영상은 거의 전부 JPEG 2000 이고, pydicom 은 Pillow 로 이를 푼다 (pylibjpeg 불필요).
서버에서 pytest 를 돌리면 서버 환경에서도 VinDr 영상을 읽을 수 있는지 함께 확인된다.
"""
from io import BytesIO

import numpy as np
import pytest
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.encaps import encapsulate
from pydicom.uid import JPEG2000Lossless, SecondaryCaptureImageStorage, generate_uid

from core.dicom_io import load_dicom

Image = pytest.importorskip("PIL.Image")
features = pytest.importorskip("PIL.features")


def jpeg2000_dicom(pixels: np.ndarray) -> bytes:
    j2k = BytesIO()
    Image.fromarray(pixels.astype(np.uint16)).save(j2k, format="JPEG2000", irreversible=False, no_jp2=True)
    meta = FileMetaDataset()
    meta.TransferSyntaxUID = JPEG2000Lossless
    meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID, ds.SOPInstanceUID = meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID
    ds.Modality = "DX"
    ds.Rows, ds.Columns = pixels.shape
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 0
    ds.PixelData = encapsulate([j2k.getvalue()])
    ds["PixelData"].VR = "OB"
    out = BytesIO()
    ds.save_as(out, enforce_file_format=True)
    return out.getvalue()


def test_pillow_has_jpeg2000():
    assert features.check("jpg_2000"), "Pillow 에 JPEG 2000(openjpeg) 지원이 없으면 VinDr 영상을 읽을 수 없습니다"


def test_load_jpeg2000_lossless_dicom():
    pixels = np.random.default_rng(0).integers(0, 4000, (64, 48))
    arr, header = load_dicom(BytesIO(jpeg2000_dicom(pixels)))
    assert arr.shape == (64, 48) and header["modality"] == "DX"
    assert np.array_equal(arr.astype(np.int64), pixels)   # 무손실: 값이 그대로
