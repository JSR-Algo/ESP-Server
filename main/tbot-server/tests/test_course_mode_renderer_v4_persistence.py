import hashlib
import json
import os
from pathlib import Path

import pytest


FIXTURE = Path(__file__).parent / "fixtures" / "course-mode" / "course-mode-pilot-cat-ball.persistence-v1.json"
FIXTURE_SHA256 = "b98afc2dd46026dcdfe525c024f52d6773c79570c620967d55ee776f998a3e27"
EXPECTED_CUES = [
    "cat-discover", "cat-meaning", "cat-joint-speech", "cat-recall",
    "cat-transfer", "ball-discover", "ball-meaning", "cat-delayed",
]


def _resolve_authority(root: Path, relative: Path, env_name: str) -> Path:
    explicit_root = os.environ.get(env_name)
    if explicit_root:
        return Path(explicit_root) / relative

    canonical = root / relative
    if canonical.is_file():
        return canonical

    matches = sorted(
        candidate
        for candidate in (root / ".worktrees").glob(f"*/{relative}")
        if candidate.is_file()
    )
    assert len(matches) == 1, (
        f"expected exactly one {env_name} authority under {root / '.worktrees'}, "
        f"found {len(matches)}: {matches}"
    )
    return matches[0]


def test_course_mode_renderer_v4_persistence_fixture_is_frozen() -> None:
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FIXTURE_SHA256
    fixture = json.loads(raw)
    assert fixture["identity"] == {
        "packageId": "course-mode-pilot-cat-ball@v1",
        "lessonVersion": 1,
        "rendererId": "teebot-lesson-renderer.v4",
        "templateVersion": 2,
        "semanticChecksum": "cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264",
        "layoutChecksum": "e61b56d1f8219a86c7f3986e7d5c70b91f512286604b5b206ef11e2c989d275c",
    }
    assert [cue["cueId"] for cue in fixture["cues"]] == EXPECTED_CUES
    assert all(cue["activityId"] == cue["cueId"] == cue["stepKey"] for cue in fixture["cues"])
    assert all(cue["playbackMode"] == "once" for cue in fixture["cues"])
    assert all(cue["derivative"]["path"].endswith(f'/{cue["cueId"]}.mp4') for cue in fixture["cues"])


def _authority_paths(test_file: Path = Path(__file__)) -> tuple[Path, Path] | None:
    repository = next((parent for parent in test_file.resolve().parents if parent.name == "TBOT"), None)
    if repository is None:
        explicit_backend = os.environ.get("TBOT_BACKEND_WORKTREE")
        explicit_firmware = os.environ.get("TBOT_FIRMWARE_WORKTREE")
        assert bool(explicit_backend) == bool(explicit_firmware), (
            "both explicit Course Mode authority roots must be supplied together"
        )
        if not explicit_backend:
            return None
        backend_root = Path(explicit_backend)
        firmware_root = Path(explicit_firmware)
    else:
        backend_root = repository / "tbot-backend"
        firmware_root = repository / "robot" / "TBOT-Firmware"
    return (
        _resolve_authority(
            backend_root,
            Path(
                "src/lessons/fixtures/course-mode/pilot/v1/"
                "course-mode-pilot-cat-ball.persistence-v1.json"
            ),
            "TBOT_BACKEND_WORKTREE",
        ),
        _resolve_authority(
            firmware_root,
            Path(
                "tests/fixtures/course-mode/"
                "course-mode-pilot-cat-ball.persistence-v1.json"
            ),
            "TBOT_FIRMWARE_WORKTREE",
        ),
    )


def test_course_mode_renderer_v4_persistence_matches_authorities() -> None:
    authorities = _authority_paths()
    if authorities is None:
        pytest.skip("cross-repository authority fixtures are unavailable in this standalone checkout")

    expected = FIXTURE.read_bytes()
    for authority in authorities:
        assert authority.is_file(), f"Course Mode persistence authority is missing: {authority}"
        assert authority.read_bytes() == expected


def test_explicit_authority_roots_work_outside_a_tbot_workspace(tmp_path, monkeypatch) -> None:
    backend = tmp_path / "backend"
    firmware = tmp_path / "firmware"
    backend_fixture = backend / "src/lessons/fixtures/course-mode/pilot/v1/course-mode-pilot-cat-ball.persistence-v1.json"
    firmware_fixture = firmware / "tests/fixtures/course-mode/course-mode-pilot-cat-ball.persistence-v1.json"
    backend_fixture.parent.mkdir(parents=True)
    firmware_fixture.parent.mkdir(parents=True)
    backend_fixture.write_bytes(FIXTURE.read_bytes())
    firmware_fixture.write_bytes(FIXTURE.read_bytes())
    monkeypatch.setenv("TBOT_BACKEND_WORKTREE", str(backend))
    monkeypatch.setenv("TBOT_FIRMWARE_WORKTREE", str(firmware))

    assert _authority_paths(tmp_path / "standalone" / "test.py") == (
        backend_fixture,
        firmware_fixture,
    )
