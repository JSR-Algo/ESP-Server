import copy
import hashlib
from pathlib import Path

import pytest

from scripts import course_mode_candidate_manifest as manifest
from scripts import course_mode_release_gate as gate
from tests.test_course_mode_candidate_manifest import NOW, candidate, repositories


@pytest.fixture
def with_portal(candidate, tmp_path):
    portal = tmp_path / "docs/site/api/openapi.json"
    portal.parent.mkdir(parents=True)
    content = b'{"openapi":"3.1.0","paths":{}}\n'
    portal.write_bytes(content)
    portal.chmod(0o444)
    candidate["tools"]["backendTestInputs"] = {"portalOpenapi": {
        "path": str(portal), "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
    }}
    return candidate


def test_candidate_accepts_independently_bound_portal(with_portal):
    assert manifest.validate_candidate(with_portal, now=NOW) == []


@pytest.mark.parametrize("mutation", ["missing", "writable", "hash", "bytes", "bool-bytes", "symlink", "backend-substitution"])
def test_portal_input_fails_closed(with_portal, mutation):
    value = with_portal["tools"]["backendTestInputs"]["portalOpenapi"]
    path = Path(value["path"])
    if mutation == "missing":
        path.unlink()
    elif mutation == "writable":
        path.chmod(0o644)
    elif mutation == "hash":
        value["sha256"] = "0" * 64
    elif mutation == "bytes":
        value["bytes"] += 1
    elif mutation == "bool-bytes":
        value["bytes"] = True
    elif mutation == "symlink":
        link = path.with_name("linked.json")
        link.symlink_to(path)
        value["path"] = str(link)
    else:
        backend = Path(with_portal["repositories"]["backend"]["path"]) / "openapi.json"
        backend.write_bytes(path.read_bytes())
        backend.chmod(0o444)
        value["path"] = str(backend)
    assert "tools.backendTestInputs.portalOpenapi" in manifest.validate_candidate(with_portal, now=NOW)


def test_stage_portal_copies_independent_bytes_and_rebases(with_portal, tmp_path):
    staged = copy.deepcopy(with_portal)
    root = tmp_path / "stage"
    root.mkdir()
    gate._stage_backend_test_inputs(with_portal, staged, root, {"entries": 0, "bytes": 0})
    target = Path(staged["tools"]["backendTestInputs"]["portalOpenapi"]["path"])
    assert target.is_relative_to(root)
    assert target.read_bytes() == Path(with_portal["tools"]["backendTestInputs"]["portalOpenapi"]["path"]).read_bytes()
    assert target != Path(with_portal["tools"]["backendTestInputs"]["portalOpenapi"]["path"])


def test_stage_rejects_missing_backend_inputs(candidate, tmp_path):
    with pytest.raises(ValueError, match="backend test inputs"):
        gate._stage_backend_test_inputs(candidate, copy.deepcopy(candidate), tmp_path, {"entries": 0, "bytes": 0})


def test_backend_native_dependencies_are_explicit():
    lane = next(item for item in gate.FULL_LANES if item.name == "backend-tests")
    assert lane.command == ("npm", "test", "--", "--no-cache")
    assert gate._python_runtime_stage_required(lane)
    assert gate._container_tools_required(lane)
    assert "COURSE_MODE_NATIVE_TEST_SCRATCH_ROOT" in gate._required_environment(lane)
