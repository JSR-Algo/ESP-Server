"""Round-2 branch gap for ``manage_api_client._normalize_assignment_payload``.

Coverage showed the alias-fill assignment line (``normalized[camelKey] =
assignment[snake_key]``) was never executed: the existing assignment-leg tests
feed payloads that are already camelCase, so the loop body never fired. This
drives a pure snake_case payload through the helper so the alias-fill runs, and
pins the non-dict short-circuit and the "camel already present wins" branch.
"""

import importlib.util
from pathlib import Path


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "config._manage_api_client_normalize_gap",
        Path(__file__).resolve().parents[1] / "config" / "manage_api_client.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_snake_case_payload_is_aliased_to_camel_case():
    mac = _load_module()
    out = mac._normalize_assignment_payload(
        {
            "assignment_id": "a1",
            "assignment_version": 2,
            "device_id": "d9",
            "child_id": "c3",
            "lesson_id": "L7",
            "lesson_title": "Animals",
            "lesson_version": 4,
            "manifest_checksum": "sha256:abc",
            "created_at": "2026-06-21T00:00:00Z",
        }
    )
    assert out["assignmentId"] == "a1"
    assert out["assignmentVersion"] == 2
    assert out["deviceId"] == "d9"
    assert out["childId"] == "c3"
    assert out["lessonId"] == "L7"
    assert out["lessonTitle"] == "Animals"
    assert out["lessonVersion"] == 4
    assert out["manifestChecksum"] == "sha256:abc"
    assert out["createdAt"] == "2026-06-21T00:00:00Z"


def test_existing_camel_key_is_not_overwritten_by_snake_alias():
    mac = _load_module()
    out = mac._normalize_assignment_payload(
        {"assignmentId": "keep", "assignment_id": "drop"}
    )
    assert out["assignmentId"] == "keep"


def test_non_dict_payload_returns_none():
    mac = _load_module()
    assert mac._normalize_assignment_payload(["not", "a", "dict"]) is None
    assert mac._normalize_assignment_payload(None) is None
