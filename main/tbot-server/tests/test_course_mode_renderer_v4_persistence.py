import hashlib
import json
from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "course-mode" / "course-mode-pilot-cat-ball.persistence-v1.json"
FIXTURE_SHA256 = "b98afc2dd46026dcdfe525c024f52d6773c79570c620967d55ee776f998a3e27"
EXPECTED_CUES = [
    "cat-discover", "cat-meaning", "cat-joint-speech", "cat-recall",
    "cat-transfer", "ball-discover", "ball-meaning", "cat-delayed",
]


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


def test_course_mode_renderer_v4_persistence_matches_available_authorities() -> None:
    repository = next((parent for parent in Path(__file__).resolve().parents if parent.name == "TBOT"), None)
    if repository is None:
        return
    for relative in (
        "tbot-backend/src/lessons/fixtures/course-mode/pilot/v1/course-mode-pilot-cat-ball.persistence-v1.json",
        "robot/TBOT-Firmware/tests/fixtures/course-mode/course-mode-pilot-cat-ball.persistence-v1.json",
    ):
        sibling = repository / relative
        if sibling.exists():
            assert sibling.read_bytes() == FIXTURE.read_bytes()
