"""Produce exact-candidate deterministic Google Live pytest evidence."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import shutil
import site
import stat
import subprocess
import sys
import tempfile
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import google_live_deterministic_nodeid_plugin as nodeid_plugin
from scripts.google_live_reliability import SCHEMA_VERSION

MANIFEST_SCHEMA = "google-live-deterministic-nodes.v1"
PYTEST_RUNTIME_SCHEMA = "google-live-pytest-runtime.v1"
PYTEST_RUNTIME_MANIFEST_GIT_PATH = (
    "main/tbot-server/tests/fixtures/google_live_pytest_runtime_manifest.json"
)
NODEID_PLUGIN_GIT_PATH = (
    "main/tbot-server/scripts/google_live_deterministic_nodeid_plugin.py"
)
NODE_PATTERN = re.compile(r"tests/[A-Za-z0-9_./-]+\.py::[^\r\n]+")
_RUNTIME_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_RUNTIME_PACKAGE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RUNTIME_PATH = re.compile(r"[A-Za-z0-9_./-]+")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_XML_DECLARATIONS = (
    '<?xml version="1.0" encoding="utf-8"?>',
    "<?xml version='1.0' encoding='utf-8'?>",
)
_XML_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SENSITIVE_JUNIT_VALUE = re.compile(
    r"(?i)(?:\bauthorization\s*[:=]|\b(?:set-)?cookie\s*[:=]|"
    r"\b(?:google[\s_-]*)?api[\s_-]*key\s*[:=]|\b(?:secret|token)\s*[:=]|"
    r"\b(?:credential|session[\s_-]*(?:id|handle|resumption[\s_-]*handle))\s*[:=]|"
    r"\bAIza[0-9A-Za-z_-]{20,}|\bsk-(?:proj-)?[0-9A-Za-z_-]{20,})"
)
_BASIC_AUTH_PAYLOAD = re.compile(r"\bbasic\s+([a-z0-9+/]+={0,2})(?![a-z0-9+/=])", re.IGNORECASE)
_BEARER_AUTH_PAYLOAD = re.compile(r"\bbearer\s+([a-z0-9._~+/=-]+)", re.IGNORECASE)
_PYTEST_CHILD_ENV_ALLOWLIST = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TEMP",
    "TMP",
    "TMPDIR",
    "TZ",
)
_PYTEST_DISTRIBUTION_PACKAGES = {
    "pytest": {"pytest", "_pytest"},
    "pytest-asyncio": {"pytest_asyncio"},
    "pluggy": {"pluggy"},
    "iniconfig": {"iniconfig"},
    "packaging": {"packaging"},
    "pygments": {"pygments"},
}
_PYTEST_BOOTSTRAP = (
    "import importlib,json,sys;"
    "from importlib.util import module_from_spec,spec_from_file_location;"
    "p=json.loads(sys.argv.pop(1));"
    "exec(\"def audit():\\n import pathlib\\n roots=[pathlib.Path(x).resolve() for x in p['trusted']]\\n for name,module in tuple(sys.modules.items()):\\n  if not any(name==x or name.startswith(x+'.') for x in p['controlImportNames']):continue\\n  origin=getattr(module,'__file__',None)\\n  if not origin:raise RuntimeError('pytest runtime import origin invalid')\\n  resolved=pathlib.Path(origin).resolve()\\n  if not any(resolved==root or root in resolved.parents for root in roots):raise RuntimeError('pytest runtime import origin invalid')\",globals());"
    "sys.path[:0]=p['trusted']+p['dependencies'];"
    "pytest=importlib.import_module('pytest');"
    "a=importlib.import_module('pytest_asyncio.plugin');"
    "audit();"
    "sys.path[:]=p['trusted']+[p['repo']]+p['dependencies']+sys.path[len(p['trusted'])+len(p['dependencies']):];"
    "s=spec_from_file_location('_google_live_pinned_nodeid_plugin',p['plugin']);"
    "n=module_from_spec(s);s.loader.exec_module(n);"
    "audit();"
    "sys.argv[0]='pytest';"
    "rc=pytest.main(sys.argv[1:],plugins=[a,n]);audit();raise SystemExit(rc)"
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
    changed_ns: int
    mode: int
    links: int


class PrivatePytestRuntime(dict[str, Any]):
    bindings: dict[Path, BoundFile]
    directories: dict[Path, tuple[int, int, int, int, int]]
    manifest_content: bytes
    manifest: dict[str, Any]


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
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
        before.st_mode,
        before.st_nlink,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
        after.st_mode,
        after.st_nlink,
    ):
        raise RuntimeError("evidence file changed while being read")
    return BoundFile(
        content,
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
        after.st_mode,
        after.st_nlink,
    )


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


def parse_pytest_runtime_manifest(content: bytes) -> dict[str, Any]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("pytest runtime manifest encoding is invalid") from exc
    if not text.endswith("\n") or "\r" in text or _junit_value_is_sensitive(text):
        raise ValueError("pytest runtime manifest violates the canonical contract")
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("pytest runtime manifest is invalid") from exc
    canonical = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if canonical != content or not isinstance(value, dict):
        raise ValueError("pytest runtime manifest is not canonical")
    if set(value) != {
        "distributions",
        "platform",
        "plugin",
        "pythonImplementation",
        "pythonMajorMinor",
        "schemaVersion",
    }:
        raise ValueError("pytest runtime manifest structure is invalid")
    if (
        value.get("schemaVersion") != PYTEST_RUNTIME_SCHEMA
        or value.get("platform") != "any"
        or value.get("pythonImplementation") != "cpython"
        or type(value.get("pythonMajorMinor")) is not str
        or re.fullmatch(r"[0-9]+\.[0-9]+", value["pythonMajorMinor"]) is None
    ):
        raise ValueError("pytest runtime manifest constraints are invalid")
    distributions = value.get("distributions")
    if not isinstance(distributions, list) or not distributions:
        raise ValueError("pytest runtime manifest distributions are invalid")
    names = []
    for item in distributions:
        if not isinstance(item, dict) or set(item) != {
            "files",
            "importNames",
            "name",
            "packages",
            "version",
        }:
            raise ValueError("pytest runtime manifest distribution is invalid")
        name = item.get("name")
        version = item.get("version")
        packages = item.get("packages")
        import_names = item.get("importNames")
        files = item.get("files")
        if (
            type(name) is not str
            or _RUNTIME_NAME.fullmatch(name) is None
            or type(version) is not str
            or _RUNTIME_NAME.fullmatch(version) is None
            or not isinstance(packages, list)
            or not packages
            or packages != sorted(packages)
            or len(packages) != len(set(packages))
            or any(
                type(package) is not str or _RUNTIME_PACKAGE.fullmatch(package) is None
                for package in packages
            )
            or not isinstance(import_names, list)
            or not import_names
            or import_names != sorted(import_names)
            or len(import_names) != len(set(import_names))
            or any(
                type(import_name) is not str
                or _RUNTIME_PACKAGE.fullmatch(import_name) is None
                for import_name in import_names
            )
            or not isinstance(files, list)
            or not files
        ):
            raise ValueError("pytest runtime manifest distribution is invalid")
        paths = []
        for entry in files:
            if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
                raise ValueError("pytest runtime manifest file is invalid")
            path = entry.get("path")
            digest = entry.get("sha256")
            if (
                type(path) is not str
                or _RUNTIME_PATH.fullmatch(path) is None
                or path.startswith("/")
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or path.split("/", 1)[0].removesuffix(".py") not in import_names
                or type(digest) is not str
                or _SHA256_HEX.fullmatch(digest) is None
                or Path(path).suffix.lower() in {".so", ".dylib", ".dll", ".pyd"}
            ):
                raise ValueError("pytest runtime manifest file is invalid")
            paths.append(path)
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("pytest runtime manifest files are ambiguous")
        file_import_names = {
            path.split("/", 1)[0].removesuffix(".py") for path in paths
        }
        if not set(packages).issubset(file_import_names) or set(import_names) != file_import_names:
            raise ValueError("pytest runtime manifest package is incomplete")
        names.append(name)
    expected = sorted(_PYTEST_DISTRIBUTION_PACKAGES)
    if names != expected or len(names) != len(set(names)):
        raise ValueError("pytest runtime manifest distribution set is invalid")
    for item in distributions:
        if item["packages"] != sorted(_PYTEST_DISTRIBUTION_PACKAGES[item["name"]]):
            raise ValueError("pytest runtime manifest package set is invalid")
    plugin = value.get("plugin")
    if (
        not isinstance(plugin, dict)
        or set(plugin) != {"path", "sha256"}
        or plugin.get("path") != NODEID_PLUGIN_GIT_PATH
        or type(plugin.get("sha256")) is not str
        or _SHA256_HEX.fullmatch(plugin["sha256"]) is None
    ):
        raise ValueError("pytest runtime manifest plugin is invalid")
    return value


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


def _contains_percent_escape(value: str) -> bool | None:
    valid_escape = False
    index = 0
    while index < len(value):
        if value[index] != "%":
            index += 1
            continue
        candidate = value[index + 1 : index + 3]
        if len(candidate) == 2 and all(
            character in "0123456789abcdefABCDEF" for character in candidate
        ):
            valid_escape = True
            index += 3
            continue
        if index + 1 == len(value) or value[index + 1].isspace():
            index += 1
            continue
        return None
    return valid_escape


def _percent_decode_scan_stages(value: str) -> list[str] | None:
    current = unicodedata.normalize("NFKC", value)
    stages = []
    for _ in range(4):
        stages.append(current)
        escape_state = _contains_percent_escape(current)
        if escape_state is None:
            return None
        if not escape_state:
            return stages
        try:
            decoded = urllib.parse.unquote(current, errors="strict")
        except UnicodeDecodeError:
            return None
        if decoded == current:
            return stages
        current = unicodedata.normalize("NFKC", decoded)
    stages.append(current)
    if _contains_percent_escape(current) is not False:
        return None
    return stages


def _normalized_junit_scan_value(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if unicodedata.category(character) not in {"Mn", "Mc", "Me"}
    )


def _looks_like_basic_credential(scan_value: str) -> bool:
    for match in _BASIC_AUTH_PAYLOAD.finditer(scan_value):
        payload = match.group(1)
        if len(payload) < 12 or len(payload) % 4:
            continue
        try:
            decoded = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            continue
        if b":" in decoded:
            return True
    return False


def _looks_like_word_segment(segment: str) -> bool:
    return (
        re.fullmatch(r"[a-z]{2,20}", segment) is not None
        and re.search(r"[aeiouy]", segment) is not None
        and re.search(r"[^aeiouy]{5}", segment) is None
    )


def _looks_like_natural_slug(payload: str) -> bool:
    if payload != payload.casefold() or any(character in payload for character in "_~+/="):
        return False
    segments = re.split(r"[-.]", payload)
    if (
        segments[-1].isdigit() and len(segments[-1]) <= 4
    ) or re.fullmatch(r"v[0-9]+", segments[-1]):
        segments = segments[:-1]
    if len(segments) < 3:
        return False
    return all(_looks_like_word_segment(segment) for segment in segments)


def _looks_like_bearer_credential(scan_value: str) -> bool:
    for match in _BEARER_AUTH_PAYLOAD.finditer(scan_value):
        payload = match.group(1)
        if len(payload) < 20:
            continue
        segments = payload.split(".")
        if len(segments) == 3 and all(segments):
            try:
                header = base64.b64decode(
                    segments[0] + "=" * (-len(segments[0]) % 4),
                    altchars=b"-_",
                    validate=True,
                )
            except (binascii.Error, ValueError):
                pass
            else:
                try:
                    parsed_header = json.loads(header)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
                else:
                    if isinstance(parsed_header, dict):
                        return True
        if _looks_like_natural_slug(payload):
            continue
        if len(payload) >= 20 and re.fullmatch(r"[a-z0-9]+", payload, re.IGNORECASE):
            return True
        if re.fullmatch(
            r"(?:gh[pousr]_|github_pat_|glpat-|ya29\.)[a-z0-9._~-]{12,}",
            payload,
            re.IGNORECASE,
        ):
            return True
        if re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            payload,
            re.IGNORECASE,
        ):
            return True
        if len(payload) >= 20 and re.fullmatch(r"[0-9a-f]+", payload, re.IGNORECASE):
            return True
        if len(payload) >= 16 and (
            payload.endswith("=") or any(character in payload for character in "_+/")
        ):
            return True
        if (
            len(payload) >= 20
            and re.search(r"[a-z]", payload, re.IGNORECASE)
            and re.search(r"[0-9]", payload)
            and re.fullmatch(r"[a-z0-9._~+/-]+", payload, re.IGNORECASE)
        ):
            return True
        segments = re.split(r"[-.]", payload)
        if len(payload) >= 20 and 2 <= len(segments) <= 4 and any(
            not _looks_like_word_segment(segment) for segment in segments
        ):
            return True
    return False


def _normalized_junit_value_is_sensitive(scan_value: str) -> bool:
    if _SENSITIVE_JUNIT_VALUE.search(scan_value) is not None:
        return True
    if _looks_like_basic_credential(scan_value) or _looks_like_bearer_credential(scan_value):
        return True
    folded_scan_value = scan_value.casefold()
    direct = {
        "apikey", "authorization", "bearertoken", "clientsecret", "cookie",
        "credentials", "credential", "password", "secret", "setcookie",
        "sessionid", "sessionhandle", "sessionresumptionhandle", "token",
    }
    suffixes = (
        "token", "secret", "key", "credential", "credentials", "password", "cookie", "handle",
    )
    prefixes = (
        "api", "auth", "access", "refresh", "session", "client", "private", "public",
        "google", "xgoogle",
    )
    for match in re.finditer(r"[:=]", folded_scan_value):
        key_start = max(0, match.start() - 100)
        tokens = re.findall(r"[a-z0-9]+", folded_scan_value[key_start : match.start()])
        for index in range(len(tokens)):
            joined = "".join(tokens[index:])
            if joined in direct or (
                any(joined.endswith(suffix) for suffix in suffixes)
                and any(joined.startswith(prefix) for prefix in prefixes)
            ):
                return True
    return False


def _junit_value_is_sensitive(value: str) -> bool:
    stages = _percent_decode_scan_stages(value)
    if stages is None:
        return True
    return any(
        _normalized_junit_value_is_sensitive(_normalized_junit_scan_value(stage))
        for stage in stages
    )


def _expected_junit_classname(node: str) -> str:
    parts = node.split("::")
    classname = parts[0][:-3].replace("/", ".")
    if len(parts) > 2:
        classname += "." + ".".join(parts[1:-1])
    return classname


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
    if root.attrib != {"name": "pytest tests"}:
        raise ValueError("JUnit root metadata is invalid")
    if len(root_children) != 1 or root_children[0].tag != "testsuite":
        raise ValueError("JUnit must contain exactly one direct test suite")
    suite = root_children[0]
    canonical_suite_attributes = {"name", "tests", "failures", "errors", "skipped"}
    raw_suite_attributes = canonical_suite_attributes | {"time", "timestamp", "hostname"}
    suite_attribute_names = frozenset(suite.attrib)
    permitted_suite_attributes = {frozenset(canonical_suite_attributes)}
    if allow_suite_test_count_mismatch:
        permitted_suite_attributes.add(frozenset(raw_suite_attributes))
    if suite_attribute_names not in permitted_suite_attributes:
        raise ValueError("JUnit suite metadata is invalid")
    if suite.get("name") != "pytest":
        raise ValueError("JUnit suite metadata is invalid")
    if suite_attribute_names == frozenset(raw_suite_attributes):
        if (
            re.fullmatch(r"(?:0|[1-9][0-9]*)\.[0-9]{3}", suite.get("time", "")) is None
            or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[^\s]+", suite.get("timestamp", "")) is None
            or not suite.get("hostname")
        ):
            raise ValueError("JUnit suite metadata is invalid")
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
        if set(testcase.attrib) != {"classname", "name", "time"}:
            raise ValueError("JUnit testcase attributes are invalid")
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
        if (
            testcase.get("name") != node.rsplit("::", 1)[-1]
            or testcase.get("classname") != _expected_junit_classname(node)
        ):
            raise ValueError("JUnit testcase identity does not match its node ID")
        testcase_time = testcase.get("time")
        if re.fullmatch(r"(?:0|[1-9][0-9]*)\.[0-9]{3}", testcase_time) is None:
            raise ValueError("JUnit testcase time is invalid")
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
    for name in ("time", "timestamp", "hostname"):
        suite.attrib.pop(name, None)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def build_report(
    identity: Mapping[str, str],
    manifest: bytes,
    junit: bytes,
    pytest_runtime_manifest: bytes,
) -> dict[str, Any]:
    nodes = parse_manifest(manifest)
    totals = parse_passing_junit(junit, nodes)
    runtime = parse_pytest_runtime_manifest(pytest_runtime_manifest)
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
            "nodeidPluginSha256": runtime["plugin"]["sha256"],
            "pytestRuntimeManifestSha256": _sha256(pytest_runtime_manifest),
            "pytestRuntimeSchema": PYTEST_RUNTIME_SCHEMA,
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


def _pytest_child_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    inherited = os.environ if source is None else source
    child = {
        name: inherited[name]
        for name in _PYTEST_CHILD_ENV_ALLOWLIST
        if inherited.get(name)
    }
    child.update(
        {
            "PATH": os.defpath,
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return child


def _approved_package_roots() -> list[Path]:
    return [
        Path(path).resolve(strict=True)
        for path in (*site.getsitepackages(), site.getusersitepackages())
        if Path(path).is_dir()
    ]


def _write_private_snapshot_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        remaining = memoryview(content)
        while remaining:
            count = os.write(descriptor, remaining)
            if count <= 0:
                raise OSError("trusted pytest snapshot write failed")
            remaining = remaining[count:]
    finally:
        os.close(descriptor)


def _runtime_package_files(package_root: Path) -> list[Path]:
    if package_root.is_symlink() or not package_root.is_dir():
        raise RuntimeError("required pytest package path is invalid")
    files = []
    for candidate in package_root.rglob("*"):
        if candidate.is_symlink():
            raise RuntimeError("required pytest package path is invalid")
        if candidate.is_dir():
            continue
        if "__pycache__" in candidate.parts or candidate.suffix in {".pyc", ".pyo"}:
            continue
        files.append(candidate)
    return sorted(files)


def _distribution_owned_runtime_files(package_distribution: Any, root: Path) -> list[Path]:
    if package_distribution.files is None:
        raise RuntimeError("required pytest distribution file metadata is unavailable")
    files = []
    for owned_path in package_distribution.files:
        if "__pycache__" in owned_path.parts or owned_path.suffix in {".pyc", ".pyo"}:
            continue
        source = Path(package_distribution.locate_file(owned_path))
        try:
            resolved = source.resolve(strict=True)
        except OSError as exc:
            if source.absolute().is_relative_to(root):
                raise RuntimeError("required pytest package file is unavailable") from exc
            continue
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            continue
        if any(part.endswith(".dist-info") for part in relative.parts):
            continue
        files.append(source)
    return sorted(files)


def _copy_trusted_pytest_packages(destination: Path, manifest: Mapping[str, Any]) -> None:
    approved_roots = _approved_package_roots()
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    copied_packages = set()
    for expected_distribution in manifest["distributions"]:
        distribution_name = expected_distribution["name"]
        package_names = expected_distribution["packages"]
        try:
            package_distribution = distribution(distribution_name)
        except PackageNotFoundError as exc:
            raise RuntimeError("required pytest distribution is unavailable") from exc
        if package_distribution.version != expected_distribution["version"]:
            raise RuntimeError("required pytest distribution version does not match")
        distribution_root = Path(package_distribution.locate_file("")).resolve(strict=True)
        if distribution_root not in approved_roots:
            raise RuntimeError("required pytest distribution is outside approved package roots")
        expected_files = {
            entry["path"]: entry["sha256"] for entry in expected_distribution["files"]
        }
        observed_files = _distribution_owned_runtime_files(
            package_distribution,
            distribution_root,
        )
        package_directory_files = []
        for package_name in package_names:
            package_directory_files.extend(
                _runtime_package_files(distribution_root / package_name)
            )
        observed_relative = [path.relative_to(distribution_root).as_posix() for path in observed_files]
        package_directory_relative = {
            path.relative_to(distribution_root).as_posix()
            for path in package_directory_files
        }
        if (
            observed_relative != sorted(expected_files)
            or not package_directory_relative.issubset(expected_files)
        ):
            raise RuntimeError("required pytest package file set does not match")
        for source, relative in zip(observed_files, observed_relative, strict=True):
            try:
                resolved_source = source.resolve(strict=True)
                resolved_source.relative_to(distribution_root)
            except (OSError, ValueError) as exc:
                raise RuntimeError("required pytest package path is invalid") from exc
            if resolved_source != source.absolute():
                raise RuntimeError("required pytest package path is invalid")
            before = source.stat(follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise RuntimeError("required pytest package path is invalid")
            bound = read_bound_file(source)
            after = source.stat(follow_symlinks=False)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_nlink,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_nlink,
            ):
                raise RuntimeError("required pytest package changed while being read")
            if not secrets.compare_digest(_sha256(bound.content), expected_files[relative]):
                raise RuntimeError("required pytest package integrity check failed")
            _write_private_snapshot_file(destination / relative, bound.content)
            copied_packages.add(relative.split("/", 1)[0].removesuffix(".py"))
        observed_after = _distribution_owned_runtime_files(
            package_distribution,
            distribution_root,
        )
        if [path.relative_to(distribution_root).as_posix() for path in observed_after] != observed_relative:
            raise RuntimeError("required pytest package changed during snapshot")
    expected_packages = {
        import_name
        for expected_distribution in manifest["distributions"]
        for import_name in expected_distribution["importNames"]
    }
    if copied_packages != expected_packages:
        raise RuntimeError("trusted pytest package set is incomplete")


def _load_trusted_pytest_runtime_manifest(
    repo_root: Path,
    expected_git_sha: str,
) -> tuple[bytes, dict[str, Any]]:
    git_root = Path(
        _git_output(repo_root, "rev-parse", "--show-toplevel").decode().strip()
    ).resolve(strict=True)
    try:
        content = _git_output(
            git_root,
            "show",
            f"{expected_git_sha}:{PYTEST_RUNTIME_MANIFEST_GIT_PATH}",
        )
    except RuntimeError as exc:
        raise RuntimeError("trusted pytest runtime manifest is unavailable") from exc
    return content, parse_pytest_runtime_manifest(content)


def _load_trusted_nodeid_plugin(
    repo_root: Path,
    expected_git_sha: str,
    manifest: Mapping[str, Any],
) -> bytes:
    git_root = Path(
        _git_output(repo_root, "rev-parse", "--show-toplevel").decode().strip()
    ).resolve(strict=True)
    try:
        content = _git_output(
            git_root,
            "show",
            f"{expected_git_sha}:{manifest['plugin']['path']}",
        )
    except (KeyError, RuntimeError) as exc:
        raise RuntimeError("trusted pytest nodeid plugin is unavailable") from exc
    if not secrets.compare_digest(_sha256(content), manifest["plugin"]["sha256"]):
        raise RuntimeError("trusted pytest nodeid plugin integrity check failed")
    return content


def _runtime_directory_identity(path: Path) -> tuple[int, int, int, int, int]:
    opened = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(opened.st_mode) or path.is_symlink():
        raise RuntimeError("private pytest runtime directory is invalid")
    return (
        opened.st_dev,
        opened.st_ino,
        opened.st_mtime_ns,
        opened.st_ctime_ns,
        opened.st_mode,
    )


def _seal_private_pytest_runtime(runtime: PrivatePytestRuntime, root: Path) -> None:
    all_files = sorted(path for path in root.rglob("*") if path.is_file())
    for path in all_files:
        path.chmod(0o400)
    all_directories = sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for path in all_directories:
        path.chmod(0o500)
    root.chmod(0o500)
    runtime.bindings = {path: read_bound_file(path) for path in all_files}
    runtime.directories = {
        path: _runtime_directory_identity(path) for path in [root, *all_directories]
    }


def _verify_private_pytest_runtime(runtime: PrivatePytestRuntime) -> None:
    try:
        for path, binding in runtime.bindings.items():
            require_file_unchanged(path, binding)
        for path, identity in runtime.directories.items():
            if _runtime_directory_identity(path) != identity:
                raise RuntimeError
    except (OSError, RuntimeError) as exc:
        raise RuntimeError("private pytest runtime changed") from exc


def _make_runtime_writable(root: Path) -> None:
    if not root.exists():
        return
    for path in [root, *root.rglob("*")]:
        try:
            if path.is_dir():
                path.chmod(0o700)
            else:
                path.chmod(0o600)
        except OSError:
            pass


@contextmanager
def _private_pytest_runtime(
    repo_root: Path,
    expected_git_sha: str,
):
    temporary = Path(tempfile.mkdtemp(prefix="google-live-pytest-runtime-"))
    try:
        manifest_content, manifest = _load_trusted_pytest_runtime_manifest(
            repo_root,
            expected_git_sha,
        )
        if (
            manifest["pythonImplementation"] != sys.implementation.name
            or manifest["pythonMajorMinor"] != f"{sys.version_info.major}.{sys.version_info.minor}"
        ):
            raise RuntimeError("trusted pytest runtime Python constraint does not match")
        packages = temporary / "packages"
        _copy_trusted_pytest_packages(packages, manifest)
        plugin = temporary / "control" / "pinned_nodeid_plugin.py"
        plugin_content = _load_trusted_nodeid_plugin(
            repo_root,
            expected_git_sha,
            manifest,
        )
        _write_private_snapshot_file(
            plugin,
            plugin_content,
        )
        runtime = PrivatePytestRuntime({
            "controlImportNames": sorted(
                {
                    import_name
                    for expected_distribution in manifest["distributions"]
                    for import_name in expected_distribution["importNames"]
                }
            ),
            "trusted": [str(packages)],
            "repo": str(repo_root),
            "dependencies": [str(path) for path in _approved_package_roots()],
            "plugin": str(plugin),
        })
        runtime.manifest_content = manifest_content
        runtime.manifest = manifest
        _seal_private_pytest_runtime(runtime, temporary)
        _verify_private_pytest_runtime(runtime)
        yield runtime
        _verify_private_pytest_runtime(runtime)
    finally:
        _make_runtime_writable(temporary)
        shutil.rmtree(temporary, ignore_errors=True)


def _pytest_command(runtime: Mapping[str, Any], *arguments: str) -> list[str]:
    return [
        sys.executable,
        "-I",
        "-c",
        _PYTEST_BOOTSTRAP,
        json.dumps(runtime, separators=(",", ":")),
        *arguments,
    ]


def _git_output(repo_root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", *arguments], cwd=repo_root, check=False, capture_output=True
    )
    if completed.returncode != 0:
        raise RuntimeError("git repository verification failed")
    return completed.stdout


def _read_candidate_tracked_file(
    repo_root: Path,
    path: Path,
    expected_git_sha: str,
) -> BoundFile:
    git_root = Path(
        _git_output(repo_root, "rev-parse", "--show-toplevel").decode().strip()
    ).resolve(strict=True)
    try:
        relative = path.absolute().relative_to(git_root)
    except ValueError as exc:
        raise ValueError("candidate tracked file path is invalid") from exc
    ancestors = []
    current = path.parent
    while current != git_root:
        ancestors.append(current)
        if current == current.parent:
            raise ValueError("candidate tracked file path is invalid")
        current = current.parent
    file_stat = path.stat(follow_symlinks=False)
    if (
        path.is_symlink()
        or any(parent.is_symlink() for parent in ancestors)
        or not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_nlink != 1
    ):
        raise ValueError("candidate tracked file path is invalid")
    tracked = _git_output(
        git_root,
        "ls-files",
        "--error-unmatch",
        "--",
        relative.as_posix(),
    )
    if tracked != relative.as_posix().encode() + b"\n":
        raise ValueError("candidate tracked file path is invalid")
    bound = read_bound_file(path)
    committed = _git_output(
        git_root,
        "show",
        f"{expected_git_sha}:{relative.as_posix()}",
    )
    require_file_unchanged(path, bound)
    if bound.content != committed:
        raise ValueError("candidate tracked file differs from candidate Git object")
    return bound


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
    canonical_bound = (
        read_bound_file(canonical_path)
        if canonical_manifest_path is not None
        else _read_candidate_tracked_file(repo_root, canonical_path, identity["gitSha"])
    )
    if manifest_bound.content != canonical_bound.content:
        raise ValueError("manifest does not match the checked-in canonical manifest")
    nodes = parse_manifest(manifest_bound.content)
    verify_repository()
    child_environment = _pytest_child_environment()
    with _private_pytest_runtime(
        repo_root,
        identity["gitSha"],
    ) as pytest_runtime:
        runtime_manifest_content = pytest_runtime.manifest_content
        collect = run(
            _pytest_command(
                pytest_runtime,
                *approved_test_files,
                "--collect-only",
                "-qq",
            ),
            cwd=repo_root,
            env=child_environment,
        )
        if collect.returncode != 0:
            raise RuntimeError("pytest collection failed")
    verify_repository()
    collected = [
        line for line in collect.stdout.splitlines() if NODE_PATTERN.fullmatch(line)
    ]
    require_exact_nodes(collected, nodes, label="collection")
    output_parent_identity = snapshot_output_parent(junit_out)
    descriptor, temporary = tempfile.mkstemp(
        dir=junit_out.parent,
        prefix=".pytest.",
        suffix=".xml",
    )
    os.close(descriptor)
    temporary_path = Path(temporary)
    temporary_path.unlink()
    published_junit: BoundFile | None = None
    completed_successfully = False
    try:
        with _private_pytest_runtime(
            repo_root,
            identity["gitSha"],
        ) as pytest_runtime:
            if not secrets.compare_digest(
                runtime_manifest_content,
                pytest_runtime.manifest_content,
            ):
                raise RuntimeError("trusted pytest runtime manifest changed")
            completed = run(
                _pytest_command(
                    pytest_runtime,
                    *nodes,
                    f"--junitxml={temporary_path}",
                    "-q",
                ),
                cwd=repo_root,
                env=child_environment,
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
        report = build_report(
            identity,
            manifest_bound.content,
            published_junit.content,
            runtime_manifest_content,
        )
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
