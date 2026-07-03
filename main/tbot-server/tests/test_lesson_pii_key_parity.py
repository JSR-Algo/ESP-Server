import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[5]
ROBOT_MANAGE_API_CLIENT = (
    REPO_ROOT / "robot/esp32-server/main/tbot-server/config/manage_api_client.py"
)
BACKEND_INGEST_LOGIC = (
    REPO_ROOT / "tbot-backend/src/lessons/lesson-event-ingest.logic.ts"
)


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
    source = BACKEND_INGEST_LOGIC.read_text()
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
