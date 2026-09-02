"""Regenerate the pinned pure-Python runtime used by deterministic Google Live tests."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import sys
from importlib.metadata import distribution
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.google_live_deterministic_evidence import (
    NODEID_PLUGIN_GIT_PATH,
    PYTEST_RUNTIME_SCHEMA,
    _PYTEST_DISTRIBUTION_PACKAGES,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(repo_root: Path) -> dict[str, object]:
    distributions = []
    for name in sorted(_PYTEST_DISTRIBUTION_PACKAGES):
        installed = distribution(name)
        package_root = Path(installed.locate_file("")).resolve(strict=True)
        packages = sorted(_PYTEST_DISTRIBUTION_PACKAGES[name])
        files = []
        import_names = set(packages)
        for owned_path in sorted(installed.files or (), key=str):
            if "__pycache__" in owned_path.parts or owned_path.suffix in {".pyc", ".pyo"}:
                continue
            path = Path(installed.locate_file(owned_path))
            try:
                relative = path.resolve(strict=True).relative_to(package_root)
            except ValueError:
                continue
            if any(part.endswith(".dist-info") for part in relative.parts):
                continue
            if path.is_symlink():
                raise RuntimeError("pytest package alias detected")
            opened = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise RuntimeError("pytest package file is invalid")
            if path.suffix.lower() in {".so", ".dylib", ".dll", ".pyd"}:
                raise RuntimeError("platform-specific pytest runtime requires an explicit section")
            if len(relative.parts) == 1 and path.suffix in {".py", ".pyi"}:
                import_names.add(path.stem)
            files.append({"path": relative.as_posix(), "sha256": _sha256(path)})
        distributions.append(
            {
                "files": sorted(files, key=lambda item: item["path"]),
                "importNames": sorted(import_names),
                "name": name,
                "packages": packages,
                "version": installed.version,
            }
        )
    plugin_path = repo_root / NODEID_PLUGIN_GIT_PATH
    candidates = [
        Path(value)
        for value in (
            os.environ.get("OPUS_LIB_PATH", ""),
            "/opt/homebrew/lib/libopus.dylib",
            "/usr/local/lib/libopus.dylib",
            "/usr/lib/x86_64-linux-gnu/libopus.so.0",
        )
        if value
    ]
    native_path = next((path.resolve(strict=True) for path in candidates if path.exists()), None)
    if native_path is None:
        raise RuntimeError("required opus native library is unavailable")
    native_stat = native_path.stat(follow_symlinks=False)
    if not stat.S_ISREG(native_stat.st_mode) or native_stat.st_nlink != 1:
        raise RuntimeError("required opus native library is invalid")
    return {
        "distributions": distributions,
        "nativeLibrary": {
            "device": native_stat.st_dev,
            "inode": native_stat.st_ino,
            "machine": platform.machine(),
            "mode": stat.S_IMODE(native_stat.st_mode),
            "name": "opus",
            "path": str(native_path),
            "sha256": _sha256(native_path),
            "system": platform.system(),
            "uid": native_stat.st_uid,
        },
        "platform": "any",
        "plugin": {
            "path": NODEID_PLUGIN_GIT_PATH,
            "sha256": _sha256(plugin_path),
        },
        "pythonImplementation": sys.implementation.name,
        "pythonMajorMinor": f"{sys.version_info.major}.{sys.version_info.minor}",
        "schemaVersion": PYTEST_RUNTIME_SCHEMA,
    }


def main() -> int:
    module_root = Path(__file__).resolve().parents[1]
    repo_root = module_root.parents[1]
    output = module_root / "tests" / "fixtures" / "google_live_pytest_runtime_manifest.json"
    content = json.dumps(
        build_manifest(repo_root),
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    output.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
