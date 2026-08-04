from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
DOCKER_READY_MODULES = [
    TESTS_DIR / "test_nginx_generation_cache_runtime.py",
    TESTS_DIR / "test_nginx_sample_assets_runtime.py",
]


def _load_docker_ready(module_path: Path):
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    body = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "DOCKER_INFO_TIMEOUT_SECONDS"
                for target in node.targets
            )
        )
        or (isinstance(node, ast.FunctionDef) and node.name == "_docker_ready")
    ]
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, object] = {}
    exec(compile(module, str(module_path), "exec"), namespace)
    return namespace["_docker_ready"]


@pytest.mark.parametrize("module_path", DOCKER_READY_MODULES)
def test_docker_ready_returns_false_when_docker_info_times_out(module_path, monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeShutil:
        @staticmethod
        def which(command: str) -> str | None:
            return "/usr/local/bin/docker" if command == "docker" else None

    class FakeSubprocess:
        TimeoutExpired = subprocess.TimeoutExpired

        @staticmethod
        def run(command: list[str], **kwargs: object) -> object:
            calls.append({"command": command, "kwargs": kwargs})
            raise subprocess.TimeoutExpired(command, timeout=kwargs.get("timeout"))

    docker_ready = _load_docker_ready(module_path)
    docker_ready.__globals__["shutil"] = FakeShutil
    docker_ready.__globals__["subprocess"] = FakeSubprocess

    assert docker_ready() is False
    assert calls == [
        {
            "command": ["docker", "info"],
            "kwargs": {"capture_output": True, "check": False, "timeout": 2},
        }
    ]
