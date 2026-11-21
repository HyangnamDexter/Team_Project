"""모든 모듈이 import 되는지 확인하는 기본 테스트. 기능별 테스트는 각 담당자가 추가."""
import importlib

import pytest

MODULES = [
    "core.dicom_io",
    "core.algorithms",
    "core.quality",
    "core.workflow",
    "services.auth",
    "services.db",
    "services.cases",
]


@pytest.mark.parametrize("name", MODULES)
def test_import(name):
    importlib.import_module(name)
