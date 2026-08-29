import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/course_mode_physical_tft_receipt_verify.py"
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture
def candidate(tmp_path: Path) -> dict:
    repo = tmp_path / "esp"
    voice = repo / "main/tbot-server/tests/test_lesson_voice_output_discipline.py"
    voice.parent.mkdir(parents=True)
    voice.write_text("current reviewed voice source\n", encoding="utf-8")
    voice_sha = hashlib.sha256(voice.read_bytes()).hexdigest()
    return {
        "candidateId": "course-mode-2026-08-29.1",
        "course": {"courseId": "course-1", "courseKey": "english-6month-4-6"},
        "curriculum": {"rendererId": "teebot-lesson-renderer.v5", "contractIdentity": "courseCompanion.v2.contract.v1"},
        "repositories": {
            "backend": {"path": str(tmp_path / "backend"), "sha": "1" * 40, "dirtyExceptions": []},
            "adminEsp": {
                "path": str(repo),
                "sha": "2" * 40,
                "dirtyExceptions": [{"path": str(voice.relative_to(repo)), "sha256": voice_sha}],
            },
            "firmware": {"path": str(tmp_path / "firmware"), "sha": "3" * 40, "dirtyExceptions": []},
        },
        "images": {"backend": {"image": "local/backend:candidate", "imageId": "sha256:" + "4" * 64}},
        "firmware": {"gitSha": "3" * 40, "applicationSha256": "5" * 64, "applicationSize": 1234},
    }


@pytest.fixture
def receipt(candidate: dict) -> dict:
    voice_exception = candidate["repositories"]["adminEsp"]["dirtyExceptions"][0]
    voice = {
        **voice_exception,
        "repositorySha": candidate["repositories"]["adminEsp"]["sha"],
        "binding": "dirtyException",
    }
    return {
        "schemaVersion": 1,
        "candidateId": candidate["candidateId"],
        "result": "PASS",
        "capturedAt": "2026-08-30T00:00:00Z",
        "course": candidate["course"],
        "lesson": {"lessonId": "lesson-2", "lessonKey": "w01-greetings-politeness", "lessonVersion": 5},
        "replacement": {
            "sourceLessonId": "lesson-1",
            "replacementLessonId": "lesson-2",
            "materializationReceiptSha256": "6" * 64,
            "cutoverReceiptSha256": "7" * 64,
        },
        "renderer": {
            "rendererId": "teebot-lesson-renderer.v5",
            "contractIdentity": "courseCompanion.v2.contract.v1",
            "contractChecksum": "8" * 64,
            "manifestChecksum": "9" * 64,
            "assetChecksums": ["a" * 64],
        },
        "repositories": {name: value["sha"] for name, value in candidate["repositories"].items()},
        "backendImage": candidate["images"]["backend"],
        "firmware": candidate["firmware"],
        "protectedSource": voice,
        "device": {
            "macSuffix": "AC:20",
            "appOffset": "0x20000",
            "partitionTableSha256": "b" * 64,
            "nvsBeforeSha256": "c" * 64,
            "nvsAfterSha256": "c" * 64,
        },
        "journey": {"assignmentId": "assignment-1", "lessonSessionId": "session-1", "deliveryId": "delivery-1"},
        "database": {"terminalState": "COMPLETED", "completionCount": 1, "progressCount": 9},
        "evidence": [{"path": "captures/final.png", "sha256": "d" * 64}],
    }


def test_receipt_requires_candidate_curriculum_v5(receipt: dict, candidate: dict) -> None:
    from course_mode_physical_tft_receipt_verify import validate_receipt

    receipt["lesson"]["lessonKey"] = "course-mode-pilot-cat-ball"
    receipt["renderer"]["rendererId"] = "teebot-lesson-renderer.v4"
    assert validate_receipt(receipt, candidate) == ["receipt.lesson", "receipt.renderer"]


def test_receipt_binds_candidate_delivery_and_current_protected_source(receipt: dict, candidate: dict) -> None:
    from course_mode_physical_tft_receipt_verify import validate_receipt

    assert validate_receipt(receipt, candidate) == []
    stale = deepcopy(receipt)
    stale["protectedSource"]["sha256"] = "08f77b5452301224b17b4b333d2d032fff40c06aa2eaea97fa90932dae7d97e3"
    assert validate_receipt(stale, candidate) == ["receipt.protected_source"]
    for field, value, reason in [
        ("candidateId", "wrong", "receipt.candidate"),
        ("repositories", {**receipt["repositories"], "adminEsp": "f" * 40}, "receipt.repositories"),
        ("backendImage", {**receipt["backendImage"], "imageId": "sha256:" + "f" * 64}, "receipt.image"),
        ("firmware", {**receipt["firmware"], "applicationSha256": "f" * 64}, "receipt.firmware"),
        ("journey", {**receipt["journey"], "deliveryId": ""}, "receipt.journey"),
        ("database", {**receipt["database"], "completionCount": 2}, "receipt.database"),
    ]:
        changed = deepcopy(receipt)
        changed[field] = value
        assert reason in validate_receipt(changed, candidate)


def test_protected_source_binding_follows_candidate_dirty_exception(receipt: dict, candidate: dict) -> None:
    from course_mode_physical_tft_receipt_verify import validate_receipt

    candidate["repositories"]["adminEsp"]["dirtyExceptions"][0]["sha256"] = "e" * 64
    receipt["protectedSource"]["sha256"] = "e" * 64
    assert validate_receipt(receipt, candidate) == []


def test_protected_source_can_bind_clean_candidate_repository_sha(receipt: dict, candidate: dict) -> None:
    from course_mode_physical_tft_receipt_verify import validate_receipt

    candidate["repositories"]["adminEsp"]["dirtyExceptions"] = []
    receipt["protectedSource"] = {
        "path": "main/tbot-server/tests/test_lesson_voice_output_discipline.py",
        "repositorySha": candidate["repositories"]["adminEsp"]["sha"],
        "binding": "repository",
    }
    assert validate_receipt(receipt, candidate) == []


def test_cli_is_deterministic_bounded_and_redacted(tmp_path: Path, receipt: dict, candidate: dict) -> None:
    candidate_path, receipt_path = tmp_path / "candidate.json", tmp_path / "receipt.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    command = [sys.executable, str(SCRIPT), str(receipt_path), "--candidate", str(candidate_path)]
    first = subprocess.run(command, capture_output=True, text=True)
    second = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0 and first.stdout == second.stdout
    assert json.loads(first.stdout) == {"candidateId": candidate["candidateId"], "reasons": [], "valid": True}
    assert len(first.stdout) < 1024
    receipt["token"] = "do-not-echo"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    failed = subprocess.run(command, capture_output=True, text=True)
    assert failed.returncode == 1 and "do-not-echo" not in failed.stdout
