"""공개 데이터나 Firebase 없이 합성 DICOM으로 첫 주 기능을 검증한다."""
from io import BytesIO

import numpy as np
import pydicom
import pytest
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.data import get_testdata_file
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid

from core.dicom_io import InvalidDicomError, apply_window, auto_window, load_dicom


@pytest.mark.parametrize('name,modality', [('CT_small.dcm', 'CT'), ('MR_small.dcm', 'MR')])
def test_pydicom_example_read_and_window(name, modality):
    path = get_testdata_file(name, download=False)
    assert path is not None, f'pydicom 예제 파일이 필요합니다: {name}'
    ds = pydicom.dcmread(path)
    pixels, header = load_dicom(path)
    expected = (ds.pixel_array.astype(np.float64)
                * float(getattr(ds, 'RescaleSlope', 1))
                + float(getattr(ds, 'RescaleIntercept', 0)))
    np.testing.assert_allclose(pixels, expected.astype(np.float32))
    assert pixels.dtype == np.float32
    assert pixels.shape == (ds.Rows, ds.Columns)
    assert header['modality'] == modality

    windows = [auto_window(pixels)]
    if header['window_center'] is not None and header['window_width'] is not None:
        windows.append((header['window_center'], header['window_width']))
    before = pixels.copy()
    for center, width in windows:
        image = apply_window(pixels, center, width)
        assert image.shape == pixels.shape
        assert image.dtype == np.uint8
        assert image.min() < image.max()
    np.testing.assert_array_equal(pixels, before)


@pytest.fixture
def dicom_file():
    def make(pixels=None, **tags):
        pixels = np.array([[0, 100], [200, 300]], dtype=np.uint16) if pixels is None else pixels
        meta = FileMetaDataset()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
        meta.MediaStorageSOPInstanceUID = generate_uid()
        ds = FileDataset(None, {}, file_meta=meta, preamble=b'\0' * 128)
        ds.SOPClassUID = meta.MediaStorageSOPClassUID
        ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
        ds.Modality = 'DX'
        ds.Rows, ds.Columns = pixels.shape
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = 'MONOCHROME2'
        ds.BitsAllocated = ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = int(pixels.dtype.kind == 'i')
        ds.PixelData = pixels.tobytes()
        for key, value in tags.items():
            setattr(ds, key, value)
        stream = BytesIO()
        ds.save_as(stream, enforce_file_format=True)
        stream.seek(0)
        return stream
    return make


def test_load_rescale_and_header(dicom_file):
    pixels, header = load_dicom(dicom_file(Modality='CT', RescaleSlope=2,
                                         RescaleIntercept=-1024, WindowCenter=[40, 80],
                                         WindowWidth=[400, 800], PatientName='Synthetic^Only'))
    np.testing.assert_array_equal(pixels, [[-1024, -824], [-624, -424]])
    assert pixels.dtype == np.float32
    assert header['window_center'] == 40
    assert header['window_width'] == 400
    assert header['rows'] == header['cols'] == 2
    assert 'PatientName' in header['present_tags']
    assert 'Synthetic' not in repr(header)


def test_path_and_repeated_uploaded_file(tmp_path, dicom_file):
    stream = dicom_file()
    path = tmp_path / 'sample.dcm'
    path.write_bytes(stream.getvalue())
    a, _ = load_dicom(path)
    b, _ = load_dicom(stream)
    c, _ = load_dicom(stream)
    np.testing.assert_array_equal(a, b)
    np.testing.assert_array_equal(b, c)


def test_monochrome1(dicom_file):
    a, h = load_dicom(dicom_file(PhotometricInterpretation='MONOCHROME1',
                               WindowCenter=100, WindowWidth=200))
    np.testing.assert_array_equal(a, [[300, 200], [100, 0]])
    original = apply_window(np.array([[0, 100], [200, 300]]), 100, 200)
    np.testing.assert_array_equal(apply_window(a, h['window_center'], h['window_width']),
                                  255 - original)


@pytest.mark.parametrize('tags', [dict(Modality='US'), dict(NumberOfFrames=2),
                                  dict(PhotometricInterpretation='RGB'), dict(WindowWidth=0)])
def test_reject_unsupported(dicom_file, tags):
    with pytest.raises(InvalidDicomError):
        load_dicom(dicom_file(**tags))


def test_fake_and_missing_pixels(dicom_file):
    with pytest.raises(InvalidDicomError):
        load_dicom(BytesIO(b'not a dicom'))
    ds = pydicom.dcmread(dicom_file())
    del ds.PixelData
    stream = BytesIO()
    ds.save_as(stream, enforce_file_format=True)
    with pytest.raises(InvalidDicomError):
        load_dicom(stream)


def test_linear_window():
    pixels = np.array([[-1, 0, 1, 2, 3, 4]])
    before = pixels.copy()
    result = apply_window(pixels, center=2, width=4)
    np.testing.assert_array_equal(result, [[0, 0, 85, 170, 255, 255]])
    np.testing.assert_array_equal(pixels, before)
    assert result.dtype == np.uint8


def test_width_one():
    np.testing.assert_array_equal(apply_window(np.array([[0, 0.5, 1]]), 1, 1),
                                  [[0, 0, 255]])


def test_auto_percentiles_and_constant():
    a = np.arange(101).reshape(1, -1)
    assert auto_window(a) == (50.5, 99.0)
    assert auto_window(a, 0, 100) == (50.5, 101.0)
    result = apply_window(a)
    assert result[0, 0] == 0 and result[0, -1] == 255
    np.testing.assert_array_equal(apply_window(np.full((2, 3), 7)), np.full((2, 3), 128))


@pytest.mark.parametrize('a', [np.array([]), np.ones((2, 2, 2)), np.array([[np.nan]]),
                               np.array([[np.inf]]), np.array([['a']])])
def test_bad_pixels(a):
    for fn in (auto_window, apply_window):
        with pytest.raises(ValueError):
            fn(a)


@pytest.mark.parametrize('center,width', [(None, 2), (2, None), (0, 0), (0, -1),
                                          (np.nan, 1), (0, np.inf)])
def test_bad_window(center, width):
    with pytest.raises(ValueError):
        apply_window(np.ones((2, 2)), center, width)


@pytest.mark.parametrize('low,high', [(-1, 99), (1, 101), (50, 50), (99, 1), (np.nan, 99)])
def test_bad_percentiles(low, high):
    with pytest.raises(ValueError):
        auto_window(np.ones((2, 2)), low, high)
