import difflib
import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SERVER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parents[1]
BASE_COMMIT = "3474b0cdddfca4a715055e7c643fbefc54412e45"
EXISTING_DIFF_SHA256 = {
    "main/tbot-server/core/api/device_mcp_admin_handler.py": "12a22786d947bba4851c342af30431017d0801c02f06cbc2c3109956f5a0d080",
    "main/tbot-server/core/connection.py": "000da5ebd96c317dc1cc6187ebe01a87b13c1ab8705354aa5c5db8ff71030aa3",
    "main/tbot-server/core/http_server.py": "a5e26b6717e84bc7dadb7bc159c1b881c94c37f162c56c589148ba1f6a264ea0",
    "main/tbot-server/core/websocket_server.py": "92b518f2df7f8e323a3f4ed8e3c61a59c9e320783745121333ad59ecaab8c86f",
    "main/tbot-server/scripts/lesson_studio_task14_hil_storage.py": "eae3897c9cd5a1d8dae89267895a3101b051e500d6c594b7fde01c1f7324adad",
    "main/tbot-server/tests/test_device_mcp_admin_handler.py": "6cbfbae133558f11ad0d067a08aed010afe69af143d3e0019ccfb77304bf2b47",
    "main/tbot-server/tests/test_lesson_studio_task14_hil_storage.py": "b9a2dac9654ece438e117e9f2e89c950008c1cc019f717027556bc164a51d861",
}
EXPECTED_PATHS = frozenset(
    {
        *EXISTING_DIFF_SHA256,
        "main/tbot-server/core/connection_headers.py",
        "main/tbot-server/core/connection_registry.py",
        "main/tbot-server/core/lesson/esp_build_identity.py",
        "main/tbot-server/scripts/hil_storage_identity_contract.py",
        "main/tbot-server/tests/test_task14_esp_build_identity.py",
        "main/tbot-server/tests/test_task14_esp_connection_headers.py",
        "main/tbot-server/tests/test_task14_esp_device_mcp_build_identity.py",
        "main/tbot-server/tests/test_task14_esp_http_metrics_build_identity.py",
        "main/tbot-server/tests/test_task14_esp_websocket_header_boundary.py",
        "main/tbot-server/tests/test_task14_sd_hil_storage_identity.py",
        "main/tbot-server/tests/test_task14_sd_storage_identity_contract.py",
        "main/tbot-server/tests/test_task14_server_rebind_scope.py",
    }
)


def _candidate_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".codex_tmp/task14-blocker-removal"
        if candidate.is_dir():
            return candidate
    raise AssertionError("frozen Task 14 candidate root is unavailable")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_existing_file_diffs_are_exact_reviewed_semantic_hunks():
    probe = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("scope fingerprint requires the dedicated Git worktree")

    changed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--name-only", BASE_COMMIT],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    assert frozenset(changed) == EXPECTED_PATHS
    for path, expected in EXISTING_DIFF_SHA256.items():
        patch = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "diff", "--binary", BASE_COMMIT, "--", path],
            capture_output=True,
            check=True,
        ).stdout
        assert hashlib.sha256(patch).hexdigest() == expected


def test_frozen_module_derivations_are_explicit_and_review_bounded():
    frozen = _candidate_root()
    pairs = (
        (
            frozen / "esp-build-identity/source-mirror/server/core/connection_headers.py",
            SERVER_ROOT / "core/connection_headers.py",
            lambda text: text.replace(
                "class CompatibilityHeaders:",
                "class CompatibilityHeaders(dict):",
            ).replace(
                "        self._pairs = tuple((str(name), str(value)) for name, value in pairs)\n",
                "        self._pairs = tuple((str(name), str(value)) for name, value in pairs)\n"
                "        collapsed: dict[str, tuple[str, str]] = {}\n"
                "        for name, value in self._pairs:\n"
                "            collapsed[name.casefold()] = (name, value)\n"
                "        super().__init__(collapsed.values())\n",
            ).replace(
                "\n    def items(self) -> Iterator[tuple[str, str]]:\n"
                "        collapsed: dict[str, tuple[str, str]] = {}\n"
                "        for name, value in self._pairs:\n"
                "            collapsed[name.casefold()] = (name, value)\n"
                "        return iter(collapsed.values())\n",
                "",
            ).replace(
                "        return values[-1] if values else default\n\n\ndef preserve_request_headers",
                "        return values[-1] if values else default\n\ndef preserve_request_headers",
            ).replace("\n\n_SENSITIVE_HEADER_NAMES", "\n_SENSITIVE_HEADER_NAMES"),
        ),
        (
            frozen / "esp-build-identity/source-mirror/server/core/lesson/esp_build_identity.py",
            SERVER_ROOT / "core/lesson/esp_build_identity.py",
            lambda text: text.replace(
                "from dataclasses import dataclass\nimport hashlib\nimport json\nimport re\n",
                "import hashlib\nimport json\nimport re\nfrom dataclasses import dataclass\n",
            ).replace("\n\n_HEADERS", "\n_HEADERS"),
        ),
        (
            frozen / "sd-identity/candidate/hil_storage_identity_contract.py",
            SERVER_ROOT / "scripts/hil_storage_identity_contract.py",
            lambda text: text.replace("\n\nSTATUS_V1_FIELDS", "\nSTATUS_V1_FIELDS"),
        ),
    )
    for source, destination, normalize in pairs:
        source_text = source.read_text(encoding="utf-8")
        destination_text = destination.read_text(encoding="utf-8")
        assert normalize(source_text) == destination_text, "".join(
            difflib.unified_diff(
                normalize(source_text).splitlines(keepends=True),
                destination_text.splitlines(keepends=True),
            )
        )


def test_frozen_and_normalized_modules_have_equivalent_observable_behavior():
    frozen = _candidate_root()
    frozen_headers = _load(
        frozen / "esp-build-identity/source-mirror/server/core/connection_headers.py",
        "task14_frozen_headers",
    )
    current_headers = _load(SERVER_ROOT / "core/connection_headers.py", "task14_current_headers")
    pairs = [("client-id", "one"), ("set-cookie", "a"), ("set-cookie", "b")]
    for module in (frozen_headers, current_headers):
        headers = module.CompatibilityHeaders(pairs)
        assert headers.get_all("set-cookie") == ["a", "b"]
    assert isinstance(current_headers.CompatibilityHeaders(pairs), dict)

    frozen_identity = _load(
        frozen / "esp-build-identity/source-mirror/server/core/lesson/esp_build_identity.py",
        "task14_frozen_identity",
    )
    current_identity = _load(
        SERVER_ROOT / "core/lesson/esp_build_identity.py", "task14_current_identity"
    )
    identity_headers = {
        "x-tbot-build-schema": "1",
        "x-tbot-hil-profile": "task14-hil-v1",
        "x-tbot-project-name": "xiaozhi",
        "x-tbot-project-version": "2.2.75",
        "x-tbot-idf-version": "v5.4.1",
        "x-tbot-secure-version": "0",
        "x-tbot-elf-sha256": "a" * 64,
        "x-tbot-app-sha256": "b" * 64,
        "x-tbot-build-id": "tbot-esp-v1:" + "a" * 64,
    }
    frozen_parsed = frozen_identity.parse_esp_build_identity(identity_headers)
    current_parsed = current_identity.parse_esp_build_identity(identity_headers)
    assert frozen_parsed.__dict__ == current_parsed.__dict__

    frozen_sd = _load(
        frozen / "sd-identity/candidate/hil_storage_identity_contract.py",
        "task14_frozen_sd",
    )
    current_sd = _load(
        SERVER_ROOT / "scripts/hil_storage_identity_contract.py", "task14_current_sd"
    )
    failure = {"status": "unavailable", "kind": "sdmmc-fat"}
    assert frozen_sd.validate_storage_identity(failure) == current_sd.validate_storage_identity(
        failure
    )
