import pytest
import shutil
from pathlib import Path
from uuid import uuid4


@pytest.fixture
def temp_dir() -> Path:
    base = Path("tests") / ".tmp"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"case_{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
