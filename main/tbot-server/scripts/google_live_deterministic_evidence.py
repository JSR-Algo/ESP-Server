"""Produce exact-candidate deterministic Google Live pytest evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import google_live_deterministic_nodeid_plugin as nodeid_plugin
from scripts.google_live_reliability import SCHEMA_VERSION

MANIFEST_SCHEMA = "google-live-deterministic-nodes.v1"
NODE_PATTERN = re.compile(r"tests/[A-Za-z0-9_./-]+\.py::[^\r\n]+")
APPROVED_TEST_FILES = (
    "tests/test_google_live_lifecycle_e2e.py",
    "tests/test_google_live_client.py",
    "tests/test_google_live_reconnect.py",
    "tests/test_google_live_provider_edges.py",
    "tests/test_google_live_audio_bridge_edges.py",
    "tests/test_google_live_bargein.py",
    "tests/test_google_live_event_mapping.py",
    "tests/test_google_live_tool_calls.py",
    "tests/test_google_live_lesson_conversation.py",
    "tests/test_connection_voice_provider_routing.py",
    "tests/test_connection_edges.py",
    "tests/test_audio_rate_controller_edges.py",
    "tests/test_receive_audio_handle.py",
    "tests/test_lesson_voice_output_discipline.py",
)


@dataclass(frozen=True)
class BoundFile:
    content: bytes
    device: int
    inode: int
    size: int
    modified_ns: int


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_bound_file(path: Path) -> BoundFile:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        content = b""
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            content += chunk
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise RuntimeError("evidence file changed while being read")
    return BoundFile(content, after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)


def require_file_unchanged(path: Path, bound: BoundFile) -> None:
    current = read_bound_file(path)
    if current != bound:
        raise RuntimeError("evidence file changed after validation")


def parse_manifest(content: bytes) -> list[str]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("manifest must be UTF-8") from exc
    if not text.endswith("\n") or "\r" in text:
        raise ValueError("manifest must use canonical newline termination")
    nodes = text[:-1].split("\n")
    if not nodes or any(not node or NODE_PATTERN.fullmatch(node) is None for node in nodes):
        raise ValueError("manifest contains an invalid node ID")
    if len(nodes) != len(set(nodes)):
        raise ValueError("manifest contains duplicate node IDs")
    return nodes


def require_exact_nodes(observed: Sequence[str], expected: Sequence[str], *, label: str) -> None:
    if list(observed) != list(expected) or len(observed) != len(set(observed)):
        raise ValueError(f"{label} does not match the exact ordered manifest")


def _strict_nonnegative_int(value: str | None, label: str) -> int:
    if value is None or re.fullmatch(r"0|[1-9][0-9]*", value) is None:
        raise ValueError(f"JUnit {label} count is invalid")
    return int(value)


def parse_passing_junit(content: bytes, expected_nodes: Sequence[str]) -> dict[str, int]:
    try:
        root = ET.fromstring(content)
    except (ET.ParseError, UnicodeDecodeError) as exc:
        raise ValueError("JUnit XML is invalid") from exc
    if root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("JUnit root is invalid")
    testcases = list(root.iter("testcase"))
    observed = []
    for testcase in testcases:
        properties = [
            prop.get("value")
            for prop in testcase.findall("./properties/property")
            if prop.get("name") == "google_live_nodeid"
        ]
        if len(properties) != 1 or type(properties[0]) is not str:
            raise ValueError("each JUnit testcase must contain exactly one node ID property")
        node = properties[0]
        if testcase.get("name") != node.rsplit("::", 1)[-1]:
            raise ValueError("JUnit testcase name does not match its node ID")
        if any(testcase.find(child) is not None for child in ("failure", "error", "skipped")):
            raise ValueError("JUnit contains a non-passing testcase")
        observed.append(node)
    require_exact_nodes(observed, expected_nodes, label="JUnit")
    declared_suites = [root] if root.tag == "testsuite" else list(root.findall("./testsuite"))
    if not declared_suites:
        raise ValueError("JUnit contains no test suite")
    totals = {name: 0 for name in ("tests", "failures", "errors", "skipped")}
    for suite in declared_suites:
        for name in totals:
            totals[name] += _strict_nonnegative_int(suite.get(name), name)
    if totals != {"tests": len(testcases), "failures": 0, "errors": 0, "skipped": 0}:
        raise ValueError("JUnit summary counts do not match exact passing testcases")
    return totals


def canonicalize_junit_summary(content: bytes) -> bytes:
    """Replace pytest's session counters with counts derived from testcase XML."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError("JUnit XML is invalid") from exc
    suites = [root] if root.tag == "testsuite" else list(root.findall("./testsuite"))
    if not suites:
        raise ValueError("JUnit contains no test suite")
    for suite in suites:
        cases = list(suite.findall("./testcase"))
        suite.set("tests", str(len(cases)))
        for child, attribute in (("failure", "failures"), ("error", "errors"), ("skipped", "skipped")):
            suite.set(attribute, str(sum(case.find(child) is not None for case in cases)))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def build_report(identity: Mapping[str, str], manifest: bytes, junit: bytes) -> dict[str, Any]:
    nodes = parse_manifest(manifest)
    totals = parse_passing_junit(junit, nodes)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "name": "deterministic",
        "status": "PASS",
        "candidateIdentity": dict(identity),
        "coverageProof": {
            "manifestSchema": MANIFEST_SCHEMA,
            "manifestSha256": _sha256(manifest),
            "manifestNodeCount": len(nodes),
            "executedNodeCount": len(nodes),
            "junitSha256": _sha256(junit),
        },
        "testVerdict": {
            "status": "PASS",
            "total": totals["tests"],
            "failed": totals["failures"],
            "skipped": totals["skipped"],
            "errors": totals["errors"],
            "failures": [],
        },
        "failures": [],
    }


def _same_file(left: Path, right: Path) -> bool:
    if left.resolve(strict=False) == right.resolve(strict=False):
        return True
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def validate_distinct_paths(paths: Sequence[Path]) -> None:
    for path in paths:
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise ValueError("evidence path alias detected")
    for index, left in enumerate(paths):
        if any(_same_file(left, right) for right in paths[index + 1 :]):
            raise ValueError("evidence path alias detected")


def validate_porcelain_status(status: bytes, repo_root: Path, allowed_root: Path) -> None:
    allowed = allowed_root.resolve(strict=False)
    for record in status.split(b"\0"):
        if not record:
            continue
        try:
            decoded = record.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("worktree status is invalid") from exc
        if not decoded.startswith("?? "):
            raise ValueError("worktree contains tracked or staged modifications")
        candidate = (repo_root / decoded[3:]).resolve(strict=False)
        if candidate != allowed and allowed not in candidate.parents:
            raise ValueError("worktree contains unowned untracked files")


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def atomic_write_exclusive(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary_path, path, follow_symlinks=False)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _default_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, capture_output=True, **kwargs)


def _git_output(repo_root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", *arguments], cwd=repo_root, check=False, capture_output=True
    )
    if completed.returncode != 0:
        raise RuntimeError("git repository verification failed")
    return completed.stdout


def produce(
    *,
    manifest_path: Path,
    junit_out: Path,
    report_path: Path,
    identity: Mapping[str, str],
    repo_root: Path,
    run: Callable[..., subprocess.CompletedProcess[str]] = _default_run,
    git_status: Callable[[], bytes] | None = None,
    git_head: Callable[[], str] | None = None,
    approved_test_files: Sequence[str] = APPROVED_TEST_FILES,
    canonical_manifest_path: Path | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve(strict=True)
    paths = [manifest_path, junit_out, report_path]
    validate_distinct_paths(paths)
    if junit_out.exists() or report_path.exists():
        raise ValueError("deterministic outputs must not already exist")
    if junit_out.parent.resolve(strict=False) != report_path.parent.resolve(strict=False):
        raise ValueError("deterministic outputs must share one owned directory")
    evidence_root = junit_out.parent.parent
    if (
        not evidence_root.is_dir()
        or evidence_root.is_symlink()
        or evidence_root == repo_root
        or repo_root not in evidence_root.resolve(strict=True).parents
    ):
        raise ValueError("evidence root must be a preexisting runner-owned directory")
    status = git_status() if git_status else _git_output(repo_root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    validate_porcelain_status(status, repo_root, evidence_root)
    head = git_head() if git_head else _git_output(repo_root, "rev-parse", "HEAD").decode().strip()
    if head != identity.get("gitSha"):
        raise ValueError("candidate git SHA does not match repository HEAD")
    manifest_bound = read_bound_file(manifest_path)
    canonical_path = canonical_manifest_path or (
        repo_root / "tests" / "fixtures" / "google_live_deterministic_nodes.txt"
    )
    canonical_bound = read_bound_file(canonical_path)
    if manifest_bound.content != canonical_bound.content:
        raise ValueError("manifest does not match the checked-in canonical manifest")
    nodes = parse_manifest(manifest_bound.content)
    collect = run(
        [sys.executable, "-m", "pytest", *approved_test_files, "--collect-only", "-qq"],
        cwd=repo_root,
    )
    if collect.returncode != 0:
        raise RuntimeError("pytest collection failed")
    collected = [line for line in collect.stdout.splitlines() if NODE_PATTERN.fullmatch(line)]
    require_exact_nodes(collected, nodes, label="collection")
    junit_out.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=junit_out.parent, prefix=".pytest.", suffix=".xml")
    os.close(descriptor)
    temporary_path = Path(temporary)
    temporary_path.unlink()
    try:
        completed = run(
            [
                sys.executable,
                "-m",
                "pytest",
                *nodes,
                "-p",
                "scripts.google_live_deterministic_nodeid_plugin",
                f"--junitxml={temporary_path}",
                "-q",
            ],
            cwd=repo_root,
        )
        if completed.returncode != 0:
            raise RuntimeError("pytest failed; deterministic evidence was not published")
        normalized = canonicalize_junit_summary(read_bound_file(temporary_path).content)
        atomic_write(temporary_path, normalized)
        junit_bound = read_bound_file(temporary_path)
        parse_passing_junit(junit_bound.content, nodes)
        require_file_unchanged(manifest_path, manifest_bound)
        require_file_unchanged(canonical_path, canonical_bound)
        os.link(temporary_path, junit_out, follow_symlinks=False)
        temporary_path.unlink()
        published_junit = read_bound_file(junit_out)
        report = build_report(identity, manifest_bound.content, published_junit.content)
        require_file_unchanged(junit_out, published_junit)
        atomic_write_exclusive(
            report_path, (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
        )
        return report
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--junit-out", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--candidate-git-sha", required=True)
    parser.add_argument("--candidate-image-digest", required=True)
    parser.add_argument("--firmware-identity", required=True)
    parser.add_argument("--config-fingerprint", required=True)
    parser.add_argument("--fixture-sha256", required=True)
    args = parser.parse_args(argv)
    identity = {
        "gitSha": args.candidate_git_sha,
        "imageDigest": args.candidate_image_digest,
        "firmwareIdentity": args.firmware_identity,
        "configFingerprint": args.config_fingerprint,
        "fixtureSha256": args.fixture_sha256,
    }
    try:
        produce(
            manifest_path=args.manifest,
            junit_out=args.junit_out,
            report_path=args.report,
            identity=identity,
            repo_root=Path(__file__).resolve().parents[1],
        )
    except (OSError, UnicodeError, ValueError, RuntimeError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
