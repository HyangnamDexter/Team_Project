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


@pytest.mark.parametrize('tags,message', [
    (dict(Modality='US'), '지원하는 촬영 종류는 CR, DX, CT, MR입니다.'),
    (dict(NumberOfFrames=2), '다중 프레임 영상은 아직 지원하지 않습니다.'),
    (dict(PhotometricInterpretation='RGB'), '단일 프레임 흑백 영상만 지원합니다.'),
    (dict(WindowWidth=0), 'WindowWidth는 1 이상이어야 합니다.'),
])
def test_reject_unsupported(dicom_file, tags, message):
    with pytest.raises(InvalidDicomError) as exc:
        load_dicom(dicom_file(**tags))
    assert str(exc.value) == message


@pytest.mark.parametrize('number', [1, -2, None])
def test_instance_number_is_integer_or_none(dicom_file, number):
    tags = {} if number is None else {'InstanceNumber': number}
    _, header = load_dicom(dicom_file(**tags))
    assert header['instance_number'] == number
    if number is not None:
        assert type(header['instance_number']) is int


def test_parser_error_uses_fixed_message(monkeypatch):
    def fail_read(file):
        raise ValueError('private patient value or file path')

    monkeypatch.setattr(pydicom, 'dcmread', fail_read)
    with pytest.raises(InvalidDicomError) as exc:
        load_dicom(BytesIO())
    assert str(exc.value) == (
        'DICOM을 읽을 수 없습니다. 파일 형식·픽셀·촬영 종류와 압축 디코더를 확인하세요.'
    )


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


@pytest.mark.parametrize('value', [None, '', '   '])
def test_empty_modality_can_be_read(dicom_file, value):
    pixels, header = load_dicom(dicom_file(Modality=value))
    assert header['modality'] == ''
    assert header['missing_tags'] == ['Modality']
    assert 'Modality' not in header['present_tags']
    assert apply_window(pixels).dtype == np.uint8


def test_missing_modality_can_be_read_without_changing_source(dicom_file):
    ds = pydicom.dcmread(dicom_file())
    del ds.Modality
    stream = BytesIO()
    ds.save_as(stream, enforce_file_format=True)
    original = stream.getvalue()
    pixels, header = load_dicom(stream)
    assert header['modality'] == ''
    assert header['missing_tags'] == ['Modality']
    assert 'Modality' not in header['present_tags']
    np.testing.assert_array_equal(pixels, [[0, 100], [200, 300]])
    assert stream.getvalue() == original


def test_supported_modality_has_no_missing_tag(dicom_file):
    _, header = load_dicom(dicom_file())
    assert header['modality'] == 'DX'
    assert header['missing_tags'] == []


def test_trailing_comma_window_tags_in_encoded_dicom(dicom_file):
    import warnings

    raw = dicom_file(WindowCenter=36483.0, WindowWidth=58567.0).getvalue()
    assert raw.count(b'36483.0 ') == 1 and raw.count(b'58567.0 ') == 1
    malformed = raw.replace(b'36483.0 ', b'36483,  ').replace(b'58567.0 ', b'58567,  ')
    source = BytesIO(malformed)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)  # 의도적으로 잘못 만든 DS 태그
        pixels, header = load_dicom(source)
    assert header['window_center'] == 36483.0 and header['window_width'] == 58567.0
    np.testing.assert_array_equal(pixels, [[0, 100], [200, 300]])
    assert source.getvalue() == malformed


@pytest.mark.parametrize('value', ['36483,', ' 36483, ', '36483,\\40000'])
def test_window_comma_normalization_and_first_window(value):
    from types import SimpleNamespace
    from core.dicom_io import _number
    assert _number(SimpleNamespace(WindowCenter=value), 'WindowCenter') == 36483.0


def test_multivalue_window_with_trailing_comma():
    from types import SimpleNamespace
    from pydicom.multival import MultiValue
    from core.dicom_io import _number
    assert _number(SimpleNamespace(WindowWidth=MultiValue(str, ['58567,', '60000'])), 'WindowWidth') == 58567.0


@pytest.mark.parametrize('value', ['36,483', '36483,,', 'nan,', 'inf,', ','])
def test_window_comma_fix_does_not_accept_other_invalid_numbers(value):
    from types import SimpleNamespace
    from core.dicom_io import _number
    with pytest.raises(ValueError):
        _number(SimpleNamespace(WindowCenter=value), 'WindowCenter')


def test_other_numeric_tags_are_not_silently_repaired():
    from types import SimpleNamespace
    from core.dicom_io import _number
    with pytest.raises(ValueError):
        _number(SimpleNamespace(InstanceNumber='1,'), 'InstanceNumber')
