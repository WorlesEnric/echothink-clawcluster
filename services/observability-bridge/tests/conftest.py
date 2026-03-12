import sys
from pathlib import Path

import pytest


SERVICE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(SERVICE_SRC) not in sys.path:
    sys.path.insert(0, str(SERVICE_SRC))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
