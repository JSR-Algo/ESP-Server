import ast
import re
from pathlib import Path


import pytest


TBOT_SERVER_ROOT = Path(__file__).resolve().parents[1]
ROBOT_MANAGE_API_CLIENT = TBOT_SERVER_ROOT / "config/manage_api_client.py"


def _backend_ingest_logic() -> Path:
    for root in Path(__file__).resolve().parents:
        candidate = root / "tbot-backend/src/lessons/lesson-event-ingest.logic.ts"
        if candidate.exists():
            return candidate
    pytest.skip("tbot-backend checkout not available")


def _robot_sensitive_keys() -> set[str]:
    tree = ast.parse(ROBOT_MANAGE_API_CLIENT.read_text())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name)
            and target.id == "LESSON_EVENT_SENSITIVE_DETAIL_KEYS"
            for target in node.targets
        ):
            continue
        value = ast.literal_eval(node.value)
        return set(value)
    raise AssertionError("LESSON_EVENT_SENSITIVE_DETAIL_KEYS not found")


def _backend_sensitive_keys() -> set[str]:
    source = _backend_ingest_logic().read_text()
    match = re.search(
        r"const SENSITIVE_PROGRESS_PAYLOAD_KEYS = new Set\(\[(.*?)\]\);",
        source,
        re.S,
    )
    if not match:
        raise AssertionError("SENSITIVE_PROGRESS_PAYLOAD_KEYS not found")
    return set(re.findall(r"'([^']+)'", match.group(1)))


def test_lesson_pii_strip_sensitive_key_sets_stay_in_parity():
    robot_keys = _robot_sensitive_keys()
    backend_keys = _backend_sensitive_keys()

    assert robot_keys == backend_keys
    assert {"usertranscript", "userutterance"} <= robot_keys
