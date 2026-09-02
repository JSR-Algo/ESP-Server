import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts import google_live_deterministic_evidence as deterministic


IDENTITY = {
    "gitSha": "a" * 40,
    "imageDigest": "sha256:" + "b" * 64,
    "firmwareIdentity": "firmware-1",
    "configFingerprint": "sha256:" + "c" * 64,
    "fixtureSha256": "d" * 64,
}


def _junit(nodes: list[str], *, status: str = "pass") -> bytes:
    cases = []
    for node in nodes:
        child = "" if status == "pass" else f"<{status} message=\"no details\" />"
        cases.append(
            f'<testcase classname="suite" name="{node.rsplit("::", 1)[-1]}">'
            f'<properties><property name="google_live_nodeid" value="{node}" />'
            f"</properties>{child}</testcase>"
        )
    failures = len(nodes) if status == "failure" else 0
    errors = len(nodes) if status == "error" else 0
    skipped = len(nodes) if status == "skipped" else 0
    return (
        f'<testsuites tests="{len(nodes)}" failures="{failures}" errors="{errors}" skipped="{skipped}">'
        f'<testsuite tests="{len(nodes)}" failures="{failures}" errors="{errors}" skipped="{skipped}">'
        + "".join(cases)
        + "</testsuite></testsuites>"
    ).encode()


def test_parse_manifest_preserves_exact_order_and_rejects_missing_duplicate_extra() -> None:
    nodes = ["tests/test_a.py::test_one", "tests/test_b.py::TestB::test_two"]
    assert deterministic.parse_manifest(("\n".join(nodes) + "\n").encode()) == nodes
    for malformed in [b"", b"tests/test_a.py::test_one\n\n", b"tests/test_a.py::test_one\ntests/test_a.py::test_one\n", b"other.py::test_x\n"]:
        with pytest.raises(ValueError):
            deterministic.parse_manifest(malformed)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra"])
def test_exact_collection_rejects_node_drift(mutation: str) -> None:
    expected = ["tests/test_a.py::test_one", "tests/test_b.py::test_two"]
    observed = list(expected)
    if mutation == "missing":
        observed.pop()
    elif mutation == "duplicate":
        observed.append(observed[-1])
    else:
        observed.append("tests/test_c.py::test_extra")
    with pytest.raises(ValueError, match="collection"):
        deterministic.require_exact_nodes(observed, expected, label="collection")


def test_plugin_adds_exactly_one_nodeid_property() -> None:
    class Item:
        nodeid = "tests/test_a.py::test_one"
        user_properties = [("existing", "safe")]

    item = Item()
    deterministic.nodeid_plugin.pytest_collection_modifyitems(None, [item])
    assert item.user_properties == [
        ("existing", "safe"),
        ("google_live_nodeid", item.nodeid),
    ]


@pytest.mark.parametrize("mutation", ["missing_property", "duplicate_property", "name_mismatch", "count_mismatch", "skip", "failure", "error"])
def test_junit_parser_fails_closed_for_ambiguous_or_nonpassing_results(mutation: str) -> None:
    nodes = ["tests/test_a.py::test_one"]
    xml = _junit(nodes)
    if mutation == "missing_property":
        xml = xml.replace(b'<property name="google_live_nodeid" value="tests/test_a.py::test_one" />', b"")
    elif mutation == "duplicate_property":
        prop = b'<property name="google_live_nodeid" value="tests/test_a.py::test_one" />'
        xml = xml.replace(prop, prop + prop)
    elif mutation == "name_mismatch":
        xml = xml.replace(b'name="test_one"', b'name="test_other"')
    elif mutation == "count_mismatch":
        xml = xml.replace(b'tests="1"', b'tests="2"')
    else:
        xml = _junit(nodes, status={"skip": "skipped", "failure": "failure", "error": "error"}[mutation])
    with pytest.raises(ValueError):
        deterministic.parse_passing_junit(xml, nodes)


def test_build_report_binds_manifest_and_junit_hashes_and_exact_counts() -> None:
    nodes = ["tests/test_a.py::test_one", "tests/test_b.py::test_two"]
    manifest = ("\n".join(nodes) + "\n").encode()
    junit = _junit(nodes)
    report = deterministic.build_report(IDENTITY, manifest, junit)
    assert report["coverageProof"] == {
        "manifestSchema": "google-live-deterministic-nodes.v1",
        "manifestSha256": hashlib.sha256(manifest).hexdigest(),
        "manifestNodeCount": 2,
        "executedNodeCount": 2,
        "junitSha256": hashlib.sha256(junit).hexdigest(),
    }
    assert report["testVerdict"] == {
        "status": "PASS", "total": 2, "failed": 0, "skipped": 0, "errors": 0, "failures": []
    }


def test_report_rejects_manifest_or_junit_changed_after_validation(tmp_path: Path) -> None:
    manifest = tmp_path / "nodes.txt"
    junit = tmp_path / "pytest.xml"
    manifest.write_text("tests/test_a.py::test_one\n", encoding="utf-8")
    junit.write_bytes(_junit(["tests/test_a.py::test_one"]))
    opened = deterministic.read_bound_file(manifest)
    manifest.write_text("tests/test_b.py::test_two\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed"):
        deterministic.require_file_unchanged(manifest, opened)
    opened = deterministic.read_bound_file(junit)
    junit.write_bytes(_junit(["tests/test_b.py::test_two"]))
    with pytest.raises(RuntimeError, match="changed"):
        deterministic.require_file_unchanged(junit, opened)


def test_worktree_rejects_tracked_and_staged_changes_and_only_allows_owned_root(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    deterministic.validate_porcelain_status(b"?? evidence/deterministic/pytest.xml\0", tmp_path, root)
    for status in [b" M scripts/a.py\0", b"M  scripts/a.py\0", b"?? elsewhere.txt\0"]:
        with pytest.raises(ValueError, match="worktree"):
            deterministic.validate_porcelain_status(status, tmp_path, root)


@pytest.mark.parametrize("kind", ["direct", "symlink", "hardlink"])
def test_output_paths_cannot_alias_manifest_or_each_other(tmp_path: Path, kind: str) -> None:
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("tests/test_a.py::test_one\n", encoding="utf-8")
    junit = tmp_path / "pytest.xml"
    report = tmp_path / "report.json"
    if kind == "direct":
        report = manifest
    elif kind == "symlink":
        report.symlink_to(manifest)
    else:
        os.link(manifest, report)
    with pytest.raises(ValueError, match="alias"):
        deterministic.validate_distinct_paths([manifest, junit, report])


def test_atomic_report_write_preserves_old_file_on_replace_failure(tmp_path: Path, monkeypatch) -> None:
    report = tmp_path / "report.json"
    report.write_text("old", encoding="utf-8")
    monkeypatch.setattr(deterministic.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        deterministic.atomic_write(report, b"new")
    assert report.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(".report.json.*.tmp"))


def test_exclusive_publish_loses_create_race_without_overwrite(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "report.json"

    def raced_link(source, destination, **kwargs):
        Path(destination).write_bytes(b"winner")
        raise FileExistsError

    monkeypatch.setattr(deterministic.os, "link", raced_link)
    with pytest.raises(FileExistsError):
        deterministic.atomic_write_exclusive(target, b"candidate")
    assert target.read_bytes() == b"winner"
    assert not list(tmp_path.glob(".report.json.*.tmp"))


def test_producer_uses_argument_vector_and_publishes_only_verified_pass(tmp_path: Path) -> None:
    nodes = ["tests/test_a.py::test_one"]
    manifest = tmp_path / "node-manifest.txt"
    manifest.write_text(nodes[0] + "\n", encoding="utf-8")
    junit = tmp_path / "evidence" / "deterministic" / "pytest.xml"
    junit.parent.parent.mkdir()
    report = junit.with_name("report.json")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "--collect-only" in command:
            return subprocess.CompletedProcess(command, 0, stdout=nodes[0] + "\n", stderr="")
        junit_arg = next(value for value in command if value.startswith("--junitxml="))
        Path(junit_arg.split("=", 1)[1]).write_bytes(_junit(nodes))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    deterministic.produce(
        manifest_path=manifest,
        junit_out=junit,
        report_path=report,
        identity=IDENTITY,
        repo_root=tmp_path,
        run=run,
        git_status=lambda: b"",
        git_head=lambda: IDENTITY["gitSha"],
        approved_test_files=("tests/test_a.py",),
        canonical_manifest_path=manifest,
    )
    assert all(isinstance(command, list) for command in calls)
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "PASS"
    assert deterministic.parse_passing_junit(junit.read_bytes(), nodes)["tests"] == 1


def test_producer_does_not_publish_report_for_failed_pytest(tmp_path: Path) -> None:
    manifest = tmp_path / "node-manifest.txt"
    manifest.write_text("tests/test_a.py::test_one\n", encoding="utf-8")
    junit = tmp_path / "evidence" / "deterministic" / "pytest.xml"
    junit.parent.parent.mkdir()
    report = junit.with_name("report.json")

    def run(command, **kwargs):
        if "--collect-only" in command:
            return subprocess.CompletedProcess(command, 0, stdout="tests/test_a.py::test_one\n", stderr="")
        return subprocess.CompletedProcess(command, 1, stdout="secret output", stderr="private exception")

    with pytest.raises(RuntimeError, match="pytest failed"):
        deterministic.produce(
            manifest_path=manifest, junit_out=junit, report_path=report,
            identity=IDENTITY, repo_root=tmp_path, run=run,
            git_status=lambda: b"", git_head=lambda: IDENTITY["gitSha"],
            approved_test_files=("tests/test_a.py",),
            canonical_manifest_path=manifest,
        )
    assert not report.exists()
    assert not junit.exists()


def test_producer_requires_preexisting_runner_owned_evidence_root(tmp_path: Path) -> None:
    manifest = tmp_path / "node-manifest.txt"
    manifest.write_text("tests/test_a.py::test_one\n", encoding="utf-8")
    with pytest.raises(ValueError, match="evidence root"):
        deterministic.produce(
            manifest_path=manifest,
            junit_out=tmp_path / "missing" / "deterministic" / "pytest.xml",
            report_path=tmp_path / "missing" / "deterministic" / "report.json",
            identity=IDENTITY,
            repo_root=tmp_path,
            git_status=lambda: b"",
            git_head=lambda: IDENTITY["gitSha"],
            approved_test_files=("tests/test_a.py",),
            canonical_manifest_path=manifest,
        )
