"""Produce exact-candidate deterministic Google Live pytest evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import unicodedata
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
_XML_DECLARATIONS = (
    '<?xml version="1.0" encoding="utf-8"?>',
    "<?xml version='1.0' encoding='utf-8'?>",
)
_XML_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SENSITIVE_JUNIT_VALUE = re.compile(
    r"(?i)(?:\b(?:bearer|basic)\s+\S+|\bauthorization\s*[:=]|\b(?:set-)?cookie\s*[:=]|"
    r"\b(?:google[\s_-]*)?api[\s_-]*key\s*[:=]|\b(?:secret|token)\s*[:=]|"
    r"\b(?:credential|session[\s_-]*(?:id|handle|resumption[\s_-]*handle))\s*[:=]|"
    r"\bAIza[0-9A-Za-z_-]{20,}|\bsk-(?:proj-)?[0-9A-Za-z_-]{20,})"
)
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


def _decode_strict_junit_xml(content: bytes) -> str:
    if content.startswith(b"\xef\xbb\xbf"):
        raise ValueError("JUnit XML encoding is invalid")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("JUnit XML encoding is invalid") from exc
    if _XML_CONTROL_CHARACTERS.search(text) or any(
        unicodedata.category(character) in {"Cc", "Cf"}
        and character not in {"\t", "\n", "\r"}
        for character in text
    ):
        raise ValueError("JUnit XML contains invalid characters")
    remainder = text
    if remainder.startswith("<?xml"):
        declaration = next(
            (candidate for candidate in _XML_DECLARATIONS if remainder.startswith(candidate)),
            None,
        )
        if declaration is None:
            raise ValueError("JUnit XML declaration is invalid")
        remainder = remainder[len(declaration) :]
    if "<?" in remainder or "<!" in remainder:
        raise ValueError("JUnit XML contains unsupported markup")
    if _junit_value_is_sensitive(text):
        raise ValueError("JUnit XML violates the privacy contract")
    return text


def _junit_value_is_sensitive(value: str) -> bool:
    compatibility = unicodedata.normalize("NFKC", value).casefold()
    scan_value = "".join(
        character
        for character in unicodedata.normalize("NFKD", compatibility)
        if unicodedata.category(character) not in {"Mn", "Mc", "Me"}
    )
    return _SENSITIVE_JUNIT_VALUE.search(scan_value) is not None


def _parse_clean_junit(
    content: bytes,
    expected_nodes: Sequence[str] | None,
    *,
    allow_suite_test_count_mismatch: bool,
) -> tuple[ET.Element, ET.Element, list[str]]:
    text = _decode_strict_junit_xml(content)
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, UnicodeDecodeError) as exc:
        raise ValueError("JUnit XML is invalid") from exc
    elements = list(root.iter())
    allowed_tags = {"testsuites", "testsuite", "testcase", "properties", "property"}
    if root.tag != "testsuites" or any(element.tag not in allowed_tags for element in elements):
        raise ValueError("JUnit structure or namespace is invalid")
    if any(
        (element.text is not None and element.text.strip())
        or (element.tail is not None and element.tail.strip())
        for element in elements
    ):
        raise ValueError("JUnit XML contains unexpected text")
    allowed_attributes = {
        "testsuites": {"name", "tests", "failures", "errors", "skipped"},
        "testsuite": {
            "name", "tests", "failures", "errors", "skipped", "time",
            "timestamp", "hostname",
        },
        "testcase": {"classname", "name", "time"},
        "properties": set(),
        "property": {"name", "value"},
    }
    if any(set(element.attrib) - allowed_attributes[element.tag] for element in elements):
        raise ValueError("JUnit contains unknown or namespaced attributes")
    if any(
        _junit_value_is_sensitive(value)
        for element in elements
        for value in element.attrib.values()
    ):
        raise ValueError("JUnit XML violates the privacy contract")
    root_children = list(root)
    if len(root_children) != 1 or root_children[0].tag != "testsuite":
        raise ValueError("JUnit must contain exactly one direct test suite")
    suite = root_children[0]
    if any(element.tag in {"failure", "error", "skipped"} for element in elements):
        raise ValueError("JUnit contains an outcome outside a passing testcase set")
    for owner, require_counts in ((root, False), (suite, True)):
        for name in ("failures", "errors", "skipped"):
            raw = owner.get(name)
            if raw is None:
                if require_counts:
                    raise ValueError(f"JUnit {name} count is missing")
                continue
            if _strict_nonnegative_int(raw, name) != 0:
                raise ValueError("JUnit contains a nonzero outcome counter")
    suite_children = list(suite)
    if any(child.tag != "testcase" for child in suite_children):
        raise ValueError("JUnit suite contains an ambiguous child")
    testcases = [child for child in suite_children if child.tag == "testcase"]
    if len(testcases) != len(list(root.iter("testcase"))):
        raise ValueError("JUnit contains nested or mixed testcase locations")
    observed = []
    for testcase in testcases:
        children = list(testcase)
        if any(child.tag != "properties" for child in children):
            raise ValueError("JUnit testcase contains an ambiguous child")
        property_groups = [child for child in children if child.tag == "properties"]
        if len(property_groups) != 1:
            raise ValueError("each JUnit testcase must contain one property group")
        properties = list(property_groups[0])
        if len(properties) != 1 or any(
            prop.tag != "property" or list(prop) for prop in properties
        ):
            raise ValueError("JUnit property structure is invalid")
        property_names = [prop.get("name") for prop in properties]
        if any(name is None for name in property_names) or len(property_names) != len(set(property_names)):
            raise ValueError("JUnit testcase properties must have unique names")
        property_node = properties[0]
        if (
            property_node.get("name") != "google_live_nodeid"
            or set(property_node.attrib) != {"name", "value"}
            or type(property_node.get("value")) is not str
        ):
            raise ValueError("each JUnit testcase must contain exactly one node ID property")
        node = property_node.get("value")
        if testcase.get("name") != node.rsplit("::", 1)[-1]:
            raise ValueError("JUnit testcase name does not match its node ID")
        observed.append(node)
    if expected_nodes is not None:
        require_exact_nodes(observed, expected_nodes, label="JUnit")
    declared_tests = _strict_nonnegative_int(suite.get("tests"), "tests")
    if not allow_suite_test_count_mismatch and declared_tests != len(testcases):
        raise ValueError("JUnit summary counts do not match exact passing testcases")
    root_tests = root.get("tests")
    if root_tests is not None and _strict_nonnegative_int(root_tests, "tests") != len(testcases):
        raise ValueError("JUnit root count contradicts exact testcases")
    return root, suite, observed


def parse_passing_junit(content: bytes, expected_nodes: Sequence[str]) -> dict[str, int]:
    _root, _suite, observed = _parse_clean_junit(
        content, expected_nodes, allow_suite_test_count_mismatch=False
    )
    return {"tests": len(observed), "failures": 0, "errors": 0, "skipped": 0}


def canonicalize_junit_summary(content: bytes) -> bytes:
    """Replace pytest's session counters with counts derived from testcase XML."""
    root, suite, observed = _parse_clean_junit(
        content, None, allow_suite_test_count_mismatch=True
    )
    suite.set("tests", str(len(observed)))
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


def _untracked_paths(status: bytes) -> frozenset[str]:
    result = set()
    for record in status.split(b"\0"):
        if not record:
            continue
        try:
            decoded = record.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("worktree status is invalid") from exc
        if decoded.startswith("?? "):
            result.add(decoded[3:])
    return frozenset(result)


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


def _open_pinned_parent(path: Path) -> tuple[Path, int, tuple[tuple[int, int], ...]]:
    absolute = path if path.is_absolute() else Path.cwd() / path
    parts = absolute.parts
    if not parts or any(part in {"", ".", ".."} for part in parts[1:]):
        raise ValueError("evidence output path is invalid")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(parts[0], flags)
    ancestry = []
    try:
        opened = os.fstat(directory_fd)
        ancestry.append((opened.st_dev, opened.st_ino))
        for component in parts[1:-1]:
            try:
                next_fd = os.open(component, flags, dir_fd=directory_fd)
            except FileNotFoundError:
                os.mkdir(component, 0o755, dir_fd=directory_fd)
                next_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
            opened = os.fstat(directory_fd)
            ancestry.append((opened.st_dev, opened.st_ino))
        return absolute, directory_fd, tuple(ancestry)
    except BaseException:
        os.close(directory_fd)
        raise


def _pinned_parent_path_matches(path: Path, ancestry: Sequence[tuple[int, int]]) -> bool:
    parts = path.parts
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(parts[0], flags)
    except OSError:
        return False
    try:
        observed = []
        opened = os.fstat(directory_fd)
        observed.append((opened.st_dev, opened.st_ino))
        for component in parts[1:]:
            next_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
            opened = os.fstat(directory_fd)
            observed.append((opened.st_dev, opened.st_ino))
        return tuple(observed) == tuple(ancestry)
    except OSError:
        return False
    finally:
        os.close(directory_fd)


def _link_exclusive(source: str, destination: str, directory_fd: int) -> None:
    os.link(
        source,
        destination,
        src_dir_fd=directory_fd,
        dst_dir_fd=directory_fd,
        follow_symlinks=False,
    )


def snapshot_output_parent(path: Path) -> tuple[int, int]:
    absolute, directory_fd, ancestry = _open_pinned_parent(path)
    try:
        if not _pinned_parent_path_matches(absolute.parent, ancestry):
            raise RuntimeError("evidence output parent changed")
        opened = os.fstat(directory_fd)
        return opened.st_dev, opened.st_ino
    finally:
        os.close(directory_fd)


def atomic_write_exclusive(
    path: Path,
    content: bytes,
    *,
    pre_publish: Callable[[Path], None] | None = None,
    post_publish: Callable[[], None] | None = None,
    expected_parent_identity: tuple[int, int] | None = None,
) -> None:
    absolute, directory_fd, ancestry = _open_pinned_parent(path)
    temporary_name = f".{absolute.name}.{secrets.token_hex(12)}.tmp"
    descriptor = None
    written_stat = None
    linked = False
    published = False
    try:
        opened_parent = os.fstat(directory_fd)
        if expected_parent_identity is not None and (
            opened_parent.st_dev,
            opened_parent.st_ino,
        ) != expected_parent_identity:
            raise RuntimeError("evidence output parent changed")
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        remaining = memoryview(content)
        while remaining:
            count = os.write(descriptor, remaining)
            if count <= 0:
                raise OSError("evidence output write failed")
            remaining = remaining[count:]
        os.fsync(descriptor)
        written_stat = os.fstat(descriptor)
        if pre_publish is not None:
            pre_publish(absolute.parent / temporary_name)
        _link_exclusive(temporary_name, absolute.name, directory_fd)
        linked = True
        target_stat = os.stat(absolute.name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            (target_stat.st_dev, target_stat.st_ino)
            != (written_stat.st_dev, written_stat.st_ino)
            or target_stat.st_nlink != 2
        ):
            raise RuntimeError("evidence output identity changed")
        os.unlink(temporary_name, dir_fd=directory_fd)
        temporary_name = ""
        final_stat = os.stat(absolute.name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            (final_stat.st_dev, final_stat.st_ino)
            != (written_stat.st_dev, written_stat.st_ino)
            or final_stat.st_nlink != 1
        ):
            raise RuntimeError("evidence output alias detected")
        if not _pinned_parent_path_matches(absolute.parent, ancestry):
            raise RuntimeError("evidence output parent changed")
        if post_publish is not None:
            post_publish()
        os.fsync(directory_fd)
        if not _pinned_parent_path_matches(absolute.parent, ancestry):
            raise RuntimeError("evidence output parent changed")
        # Last observable boundary: no caller hook runs after this. POSIX offers no
        # portable lock against an unrelated process linking the inode after stat.
        committed = os.stat(
            absolute.name, dir_fd=directory_fd, follow_symlinks=False
        )
        if (
            not stat.S_ISREG(committed.st_mode)
            or (committed.st_dev, committed.st_ino)
            != (written_stat.st_dev, written_stat.st_ino)
            or committed.st_nlink != 1
        ):
            raise RuntimeError("evidence output alias detected")
        published = True
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        if linked and not published:
            try:
                current = os.stat(absolute.name, dir_fd=directory_fd, follow_symlinks=False)
                if written_stat is not None and (current.st_dev, current.st_ino) == (
                    written_stat.st_dev,
                    written_stat.st_ino,
                ):
                    os.unlink(absolute.name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)


def _unlink_if_bound(path: Path, bound: BoundFile) -> None:
    try:
        absolute, directory_fd, _ancestry = _open_pinned_parent(path)
    except OSError:
        return
    try:
        current = os.stat(absolute.name, dir_fd=directory_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) == (
            bound.device,
            bound.inode,
            bound.size,
            bound.modified_ns,
        ):
            os.unlink(absolute.name, dir_fd=directory_fd)
    except FileNotFoundError:
        pass
    finally:
        os.close(directory_fd)


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
    initial_status = git_status() if git_status else _git_output(
        repo_root, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    )
    validate_porcelain_status(initial_status, repo_root, evidence_root)
    baseline_untracked = _untracked_paths(initial_status)

    def verify_repository(*allowed_generated: Path) -> None:
        status = git_status() if git_status else _git_output(
            repo_root, "status", "--porcelain=v1", "-z", "--untracked-files=all"
        )
        validate_porcelain_status(status, repo_root, evidence_root)
        generated = {
            str(path.resolve(strict=False).relative_to(repo_root))
            for path in allowed_generated
        }
        if _untracked_paths(status) - generated != baseline_untracked:
            raise ValueError("worktree contains test-created untracked files")
        head = git_head() if git_head else _git_output(
            repo_root, "rev-parse", "HEAD"
        ).decode().strip()
        if head != identity.get("gitSha"):
            raise ValueError("candidate git SHA does not match repository HEAD")

    verify_repository()
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
    verify_repository()
    collected = [line for line in collect.stdout.splitlines() if NODE_PATTERN.fullmatch(line)]
    require_exact_nodes(collected, nodes, label="collection")
    output_parent_identity = snapshot_output_parent(junit_out)
    descriptor, temporary = tempfile.mkstemp(dir=junit_out.parent, prefix=".pytest.", suffix=".xml")
    os.close(descriptor)
    temporary_path = Path(temporary)
    temporary_path.unlink()
    published_junit: BoundFile | None = None
    completed_successfully = False
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
        parse_passing_junit(normalized, nodes)
        verify_repository(temporary_path)
        require_file_unchanged(manifest_path, manifest_bound)
        require_file_unchanged(canonical_path, canonical_bound)
        verify_repository(temporary_path)
        atomic_write_exclusive(
            junit_out,
            normalized,
            pre_publish=lambda publish_temp: verify_repository(
                temporary_path, publish_temp
            ),
            post_publish=lambda: verify_repository(temporary_path, junit_out),
            expected_parent_identity=output_parent_identity,
        )
        temporary_path.unlink()
        published_junit = read_bound_file(junit_out)
        report = build_report(identity, manifest_bound.content, published_junit.content)
        require_file_unchanged(junit_out, published_junit)
        atomic_write_exclusive(
            report_path,
            (json.dumps(report, indent=2, sort_keys=True) + "\n").encode(),
            pre_publish=lambda publish_temp: verify_repository(
                junit_out, publish_temp
            ),
            post_publish=lambda: verify_repository(junit_out, report_path),
            expected_parent_identity=output_parent_identity,
        )
        completed_successfully = True
        return report
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
        if not completed_successfully and published_junit is not None:
            _unlink_if_bound(junit_out, published_junit)


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
