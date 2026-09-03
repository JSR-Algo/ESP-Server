from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("version", ["3.10", "3.11"])
def test_course_mode_contract_imports_under_supported_python(version: str) -> None:
    python = Path(f"/opt/homebrew/bin/python{version}")
    if not python.exists():
        pytest.skip(f"Python {version} is not installed")
    result = subprocess.run(
        [str(python), "-c", "import core.lesson.course_mode_contract"],
        cwd=Path(__file__).parents[1], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
