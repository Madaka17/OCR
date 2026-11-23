import os
import sys
from pathlib import Path

os.environ["OCR_ENV_FILE"] = ""  # a developer's real .env must never leak into tests

import pytest  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.store import JobStore  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: loads the real OCR engine")


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path)
