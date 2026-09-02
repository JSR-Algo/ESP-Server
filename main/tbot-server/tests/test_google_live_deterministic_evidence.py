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


@pytest.mark.parametrize("outcome", ["errors", "failures", "skipped"])
@pytest.mark.parametrize("location", ["root_counter", "suite_counter", "root_child", "suite_child"])
def test_junit_rejects_non_testcase_outcomes(outcome: str, location: str) -> None:
    node = "tests/test_a.py::test_one"
    child = outcome.removesuffix("s") if outcome != "skipped" else "skipped"
    root_attrs = f' {outcome}="1"' if location == "root_counter" else ""
    suite_value = "1" if location == "suite_counter" else "0"
    root_child = f'<{child} message="session crashed" />' if location == "root_child" else ""
    suite_child = f'<{child} message="session crashed" />' if location == "suite_child" else ""
    xml = (
        f'<testsuites{root_attrs}>{root_child}'
        f'<testsuite tests="1" failures="{suite_value if outcome == "failures" else "0"}" '
        f'errors="{suite_value if outcome == "errors" else "0"}" '
        f'skipped="{suite_value if outcome == "skipped" else "0"}">{suite_child}'
        f'<testcase name="test_one"><properties><property name="google_live_nodeid" '
        f'value="{node}" /></properties></testcase></testsuite></testsuites>'
    ).encode()
    with pytest.raises(ValueError):
        deterministic.parse_passing_junit(xml, [node])
    with pytest.raises(ValueError):
        deterministic.canonicalize_junit_summary(xml)


@pytest.mark.parametrize(
    "xml",
    [
        b'<testsuites><testsuite tests="1" failures="x" errors="0" skipped="0" /></testsuites>',
        b'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"><testsuite><error /></testsuite></testsuite></testsuites>',
        b'<testsuites><testcase name="direct" /><testsuite tests="0" failures="0" errors="0" skipped="0" /></testsuites>',
        b'<testsuites><testsuite tests="0" failures="0" errors="0" skipped="0" /><testsuite tests="0" failures="0" errors="0" skipped="0" /></testsuites>',
        b'<ns:testsuites xmlns:ns="urn:hostile"><ns:testsuite /></ns:testsuites>',
    ],
)
def test_junit_rejects_malformed_namespace_nested_or_ambiguous_structure(xml: bytes) -> None:
    with pytest.raises(ValueError):
        deterministic.parse_passing_junit(xml, [])
    with pytest.raises(ValueError):
        deterministic.canonicalize_junit_summary(xml)


def test_junit_rejects_duplicate_property_names() -> None:
    node = "tests/test_a.py::test_one"
    xml = (
        '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase name="test_one"><properties>'
        f'<property name="google_live_nodeid" value="{node}" />'
        f'<property name="google_live_nodeid" value="{node}" />'
        '</properties></testcase></testsuite></testsuites>'
    ).encode()
    with pytest.raises(ValueError):
        deterministic.parse_passing_junit(xml, [node])


def test_canonicalizer_allows_only_clean_pytest9_test_count_inaccuracy() -> None:
    node = "tests/test_a.py::test_one"
    raw = _junit([node]).replace(
        b'<testsuite tests="1"', b'<testsuite tests="994"', 1
    )
    canonical = deterministic.canonicalize_junit_summary(raw)
    assert deterministic.parse_passing_junit(canonical, [node]) == {
        "tests": 1, "failures": 0, "errors": 0, "skipped": 0
    }


@pytest.mark.parametrize("location", ["root", "suite", "testcase", "property", "tail"])
def test_junit_rejects_non_whitespace_text_and_tail_without_leaking(location: str) -> None:
    node = "tests/test_a.py::test_one"
    xml = _junit([node]).decode()
    tag = {"root": "<testsuites ", "suite": "<testsuite ", "testcase": "<testcase"}.get(location)
    if tag is not None:
        start = xml.index(tag)
        end = xml.index(">", start) + 1
        xml = xml[:end] + "GOOGLE_API_KEY=secret" + xml[end:]
    elif location == "property":
        start = xml.index("<property")
        end = xml.index("/>", start) + 2
        xml = xml[: end - 2] + ">GOOGLE_API_KEY=secret</property>" + xml[end:]
    else:
        xml = xml.replace("</testcase>", "</testcase>GOOGLE_API_KEY=secret", 1)
    with pytest.raises(ValueError) as error:
        deterministic.parse_passing_junit(xml.encode(), [node])
    assert "secret" not in str(error.value).lower()


@pytest.mark.parametrize(
    "mutation",
    [
        "internal_doctype", "external_doctype", "pi", "comment", "cdata",
        "bom", "utf16", "declaration_case", "control",
    ],
)
def test_junit_rejects_dtd_entities_markup_and_encoding_ambiguity(mutation: str) -> None:
    xml = _junit(["tests/test_a.py::test_one"])
    if mutation == "internal_doctype":
        payload = b'<!DOCTYPE testsuites [<!ENTITY leak "GOOGLE_API_KEY=secret">]>' + xml
    elif mutation == "external_doctype":
        payload = b'<!DOCTYPE testsuites SYSTEM "file:///etc/passwd">' + xml
    elif mutation == "pi":
        payload = b'<?probe value="secret"?>' + xml
    elif mutation == "comment":
        payload = xml.replace(b'<testsuites ', b'<testsuites ', 1).replace(b'>', b'><!-- secret -->', 1)
    elif mutation == "cdata":
        payload = xml.replace(b'>', b'><![CDATA[GOOGLE_API_KEY=secret]]>', 1)
    elif mutation == "bom":
        payload = b"\xef\xbb\xbf" + xml
    elif mutation == "utf16":
        payload = ('<?xml version="1.0" encoding="UTF-16"?>' + xml.decode()).encode("utf-16")
    elif mutation == "declaration_case":
        payload = b'<?XML version="1.0" encoding="utf-8"?>' + xml
    else:
        payload = xml.replace(b'>', b'>\x01', 1)
    with pytest.raises(ValueError):
        deterministic.canonicalize_junit_summary(payload)


def test_junit_privacy_scan_catches_case_whitespace_and_character_reference_obfuscation() -> None:
    node = "tests/test_a.py::test_one"
    for secret in ("google Api Key = secret", "Bearer abc123", "GOOGLE&#95;API&#95;KEY=secret"):
        xml = _junit([node]).replace(b'classname="suite"', f'classname="{secret}"'.encode())
        with pytest.raises(ValueError) as error:
            deterministic.parse_passing_junit(xml, [node])
        assert "secret" not in str(error.value).lower()


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


@pytest.mark.parametrize("alias_kind", ["direct", "symlink", "hardlink"])
def test_exclusive_publish_loses_alias_race_without_overwrite(
    tmp_path: Path, monkeypatch, alias_kind: str
) -> None:
    target = tmp_path / "report.json"
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(b"evidence")

    def raced_link(source, destination, *args, **kwargs):
        del source, destination, args, kwargs
        destination = target
        if alias_kind == "direct":
            destination.write_bytes(b"winner")
        elif alias_kind == "symlink":
            destination.symlink_to(evidence)
        else:
            os.link(evidence, destination)
        raise FileExistsError

    monkeypatch.setattr(deterministic, "_link_exclusive", raced_link)
    with pytest.raises(FileExistsError):
        deterministic.atomic_write_exclusive(target, b"candidate")
    assert evidence.read_bytes() == b"evidence"
    assert not list(tmp_path.glob(".report.json.*.tmp"))


def test_exclusive_publish_rejects_parent_inode_swap_without_touching_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    parent = tmp_path / "out"
    parent.mkdir()
    evidence_parent = tmp_path / "evidence"
    evidence_parent.mkdir()
    evidence = evidence_parent / "report.json"
    evidence.write_bytes(b"evidence")
    real_match = deterministic._pinned_parent_path_matches
    swapped = False

    def swap_then_match(path, ancestry):
        nonlocal swapped
        if not swapped:
            swapped = True
            parent.rename(tmp_path / "moved-out")
            parent.symlink_to(evidence_parent, target_is_directory=True)
        return real_match(path, ancestry)

    monkeypatch.setattr(deterministic, "_pinned_parent_path_matches", swap_then_match)
    with pytest.raises(RuntimeError, match="parent changed"):
        deterministic.atomic_write_exclusive(parent / "report.json", b"candidate")
    assert evidence.read_bytes() == b"evidence"
    assert not (tmp_path / "moved-out" / "report.json").exists()


@pytest.mark.parametrize("boundary", ["parent_match", "post_publish", "directory_fsync"])
def test_exclusive_publish_rechecks_hardlinks_at_final_boundary(
    tmp_path: Path, monkeypatch, boundary: str
) -> None:
    target = tmp_path / "report.json"
    external_alias = tmp_path / "external-alias.json"
    real_match = deterministic._pinned_parent_path_matches
    real_fsync = deterministic.os.fsync
    fsync_calls = 0

    def inject_alias() -> None:
        if target.exists() and not external_alias.exists():
            os.link(target, external_alias)

    def match(path, ancestry):
        result = real_match(path, ancestry)
        if boundary == "parent_match":
            inject_alias()
        return result

    def post_publish() -> None:
        if boundary == "post_publish":
            inject_alias()

    def fsync(descriptor):
        nonlocal fsync_calls
        fsync_calls += 1
        if boundary == "directory_fsync" and fsync_calls == 2:
            inject_alias()
        return real_fsync(descriptor)

    monkeypatch.setattr(deterministic, "_pinned_parent_path_matches", match)
    monkeypatch.setattr(deterministic.os, "fsync", fsync)
    with pytest.raises(RuntimeError, match="alias"):
        deterministic.atomic_write_exclusive(
            target, b"candidate", post_publish=post_publish
        )
    assert not target.exists()
    assert external_alias.read_bytes() == b"candidate"
    assert external_alias.stat().st_nlink == 1


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


def test_producer_rejects_secret_junit_without_publishing_or_leaking(
    tmp_path: Path,
) -> None:
    node = "tests/test_a.py::test_one"
    manifest = tmp_path / "node-manifest.txt"
    manifest.write_text(node + "\n", encoding="utf-8")
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    junit = evidence_root / "deterministic" / "pytest.xml"
    report = junit.with_name("report.json")

    def run(command, **kwargs):
        if "--collect-only" in command:
            return subprocess.CompletedProcess(command, 0, stdout=node + "\n", stderr="")
        junit_arg = next(value for value in command if value.startswith("--junitxml="))
        malicious = _junit([node]).replace(b'<testsuites ', b'<testsuites >GOOGLE_API_KEY=secret<', 1)
        Path(junit_arg.split("=", 1)[1]).write_bytes(malicious)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with pytest.raises(ValueError) as error:
        deterministic.produce(
            manifest_path=manifest, junit_out=junit, report_path=report,
            identity=IDENTITY, repo_root=tmp_path, run=run,
            git_status=lambda: b"", git_head=lambda: IDENTITY["gitSha"],
            approved_test_files=("tests/test_a.py",), canonical_manifest_path=manifest,
        )
    assert "secret" not in str(error.value).lower()
    assert not junit.exists()
    assert not report.exists()


@pytest.mark.parametrize("drift", ["tracked", "staged", "untracked", "owned_root_untracked", "head"])
@pytest.mark.parametrize("phase", ["collect", "run"])
def test_producer_revalidates_repository_after_each_execution_boundary(
    tmp_path: Path, drift: str, phase: str
) -> None:
    node = "tests/test_a.py::test_one"
    manifest = tmp_path / "node-manifest.txt"
    manifest.write_text(node + "\n", encoding="utf-8")
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    junit = evidence_root / "deterministic" / "pytest.xml"
    report = junit.with_name("report.json")
    state = {"status": b"", "head": IDENTITY["gitSha"]}

    def run(command, **kwargs):
        is_collect = "--collect-only" in command
        if (phase == "collect" and is_collect) or (phase == "run" and not is_collect):
            if drift == "head":
                state["head"] = "e" * 40
            else:
                state["status"] = {
                    "tracked": b" M scripts/a.py\0",
                    "staged": b"M  scripts/a.py\0",
                    "untracked": b"?? unexpected.txt\0",
                    "owned_root_untracked": b"?? evidence/evil.txt\0",
                }[drift]
        if is_collect:
            return subprocess.CompletedProcess(command, 0, stdout=node + "\n", stderr="")
        junit_arg = next(value for value in command if value.startswith("--junitxml="))
        Path(junit_arg.split("=", 1)[1]).write_bytes(_junit([node]))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with pytest.raises(ValueError, match="worktree|HEAD"):
        deterministic.produce(
            manifest_path=manifest, junit_out=junit, report_path=report,
            identity=IDENTITY, repo_root=tmp_path, run=run,
            git_status=lambda: state["status"], git_head=lambda: state["head"],
            approved_test_files=("tests/test_a.py",), canonical_manifest_path=manifest,
        )
    assert not report.exists()
    assert not junit.exists()


def test_producer_rolls_back_owned_junit_when_final_publication_guard_fails(
    tmp_path: Path, monkeypatch
) -> None:
    node = "tests/test_a.py::test_one"
    manifest = tmp_path / "node-manifest.txt"
    manifest.write_text(node + "\n", encoding="utf-8")
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    junit = evidence_root / "deterministic" / "pytest.xml"
    report = junit.with_name("report.json")
    state = {"status": b""}
    real_publish = deterministic.atomic_write_exclusive

    def publish(path, content, **kwargs):
        if Path(path) == report:
            state["status"] = b" M scripts/changed.py\0"
        return real_publish(path, content, **kwargs)

    def run(command, **kwargs):
        if "--collect-only" in command:
            return subprocess.CompletedProcess(command, 0, stdout=node + "\n", stderr="")
        junit_arg = next(value for value in command if value.startswith("--junitxml="))
        Path(junit_arg.split("=", 1)[1]).write_bytes(_junit([node]))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(deterministic, "atomic_write_exclusive", publish)
    with pytest.raises(ValueError, match="worktree"):
        deterministic.produce(
            manifest_path=manifest, junit_out=junit, report_path=report,
            identity=IDENTITY, repo_root=tmp_path, run=run,
            git_status=lambda: state["status"], git_head=lambda: IDENTITY["gitSha"],
            approved_test_files=("tests/test_a.py",), canonical_manifest_path=manifest,
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
