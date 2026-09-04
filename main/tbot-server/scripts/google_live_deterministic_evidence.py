"""Produce exact-candidate deterministic Google Live pytest evidence."""

from __future__ import annotations

import argparse
import base64
import binascii
import fcntl
import hashlib
import io
import json
import os
import platform
import re
import secrets
import shutil
import site
import stat
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import google_live_deterministic_nodeid_plugin as nodeid_plugin
from scripts.google_live_reliability import SCHEMA_VERSION
from scripts.google_live_trusted_git import (
    git_output as _trusted_git_output,
)
from scripts.google_live_trusted_git import (
    trusted_git_session,
)

MANIFEST_SCHEMA = "google-live-deterministic-nodes.v1"
PYTEST_RUNTIME_SCHEMA = "google-live-pytest-runtime.v2"
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
_BOUND_DESCRIPTOR_PATH = re.compile(r"/(?:dev/fd|proc/self/fd)/([0-9]+)")
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
    "opuslib-next": {"opuslib_next"},
    "pytest": {"pytest", "_pytest"},
    "pytest-asyncio": {"pytest_asyncio"},
    "pluggy": {"pluggy"},
    "iniconfig": {"iniconfig"},
    "packaging": {"packaging"},
    "pygments": {"pygments"},
}
_PYTEST_BOOTSTRAP = (
    "import ctypes.util,importlib,json,os,pathlib,sys;"
    "from importlib.util import module_from_spec,spec_from_file_location;"
    "p=json.loads(sys.argv.pop(1));"
    "exec(\"def audit():\\n import pathlib\\n roots=[pathlib.Path(x).resolve() for x in p['trusted']]\\n for name,module in tuple(sys.modules.items()):\\n  if not any(name==x or name.startswith(x+'.') for x in p['controlImportNames']):continue\\n  origin=getattr(module,'__file__',None)\\n  if not origin:raise RuntimeError('pytest runtime import origin invalid')\\n  resolved=pathlib.Path(origin).resolve()\\n  if not any(resolved==root or root in resolved.parents for root in roots):raise RuntimeError('pytest runtime import origin invalid')\",globals());"
    "exec(\"def audit_candidate():\\n import pathlib\\n root=pathlib.Path(p['repo']).resolve();live=pathlib.Path(p['liveRepo']).resolve();cache=pathlib.Path(p['pycache']).resolve()\\n if cache.exists():raise RuntimeError('bytecode cache invalid')\\n for name,module in tuple(sys.modules.items()):\\n  cached=getattr(module,'__cached__',None)\\n  if cached and pathlib.Path(cached).exists():raise RuntimeError('bytecode cache invalid')\\n  origin=getattr(module,'__file__',None)\\n  if not origin:continue\\n  resolved=pathlib.Path(origin).resolve();top=name.split('.',1)[0]\\n  if live!=root and (resolved==live or live in resolved.parents):raise RuntimeError('candidate import origin invalid')\\n  if top in p['candidateImportNames'] and top not in p['controlImportNames'] and not (resolved==root or root in resolved.parents):raise RuntimeError('candidate import origin invalid')\",globals());"
    "exec(\"for name in tuple(sys.modules):\\n if any(name==x or name.startswith(x+'.') for x in p['controlImportNames']):sys.modules.pop(name,None)\",globals());"
    "sys.path[:0]=p['trusted']+p['dependencies'];"
    "pytest=importlib.import_module('pytest');"
    "a=importlib.import_module('pytest_asyncio.plugin');"
    "l=importlib.import_module('loguru');la=l.logger.add;"
    "exec(\"def sync_log_add(*args,**kwargs):\\n kwargs['enqueue']=False\\n return la(*args,**kwargs)\",globals());"
    "l.logger.add=sync_log_add;"
    "audit();"
    "os.environ['OPUS_LIB_PATH']=p['opusLibrary'];"
    "f=ctypes.util.find_library;ctypes.util.find_library=lambda name:p['opusLibrary'] if name=='opus' else f(name);"
    "exec(\"def safe_path(value):\\n resolved=pathlib.Path(value).resolve();root=pathlib.Path(p['repo']).resolve();live=pathlib.Path(p['liveRepo']).resolve()\\n return live==root or resolved==root or root in resolved.parents or not (resolved==live or live in resolved.parents)\",globals());"
    "sys.path[:]=[str(pathlib.Path(x).resolve()) for x in p['trusted']+[p['repo']]+p['dependencies']+sys.path[len(p['trusted'])+len(p['dependencies']):] if x and safe_path(x)];"
    "s=spec_from_file_location('_google_live_pinned_nodeid_plugin',p['plugin']);"
    "n=module_from_spec(s);s.loader.exec_module(n);"
    "audit();"
    "sys.argv[0]='pytest';"
    "sys.argv[1:1]=['-p','no:cacheprovider'];"
    "rc=pytest.main(sys.argv[1:],plugins=[a,n]);audit();audit_candidate();raise SystemExit(rc)"
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
    external_bindings: dict[Path, BoundFile]


@dataclass
class PrivateCandidateSnapshot:
    root: Path
    bindings: dict[Path, BoundFile]
    directories: dict[Path, tuple[int, int, int, int, int]]
    scratch: tuple[Path, ...]


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
        "platformVariants",
        "plugin",
        "pythonImplementation",
        "pythonMajorMinor",
        "schemaVersion",
    }:
        raise ValueError("pytest runtime manifest structure is invalid")
    if (
        value.get("schemaVersion") != PYTEST_RUNTIME_SCHEMA
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
    variants = value.get("platformVariants")
    if not isinstance(variants, list) or not variants:
        raise ValueError("pytest runtime manifest platform variants are invalid")
    keys = []
    for variant in variants:
        if not isinstance(variant, dict) or set(variant) != {"key", "machine", "nativeLibraries", "pythonAbi", "system"}:
            raise ValueError("pytest runtime manifest platform variant is invalid")
        libraries = variant.get("nativeLibraries")
        if (
            any(type(variant.get(key)) is not str or not variant[key] for key in ("key", "machine", "pythonAbi", "system"))
            or not isinstance(libraries, list)
            or len(libraries) != 1
        ):
            raise ValueError("pytest runtime manifest platform variant is invalid")
        if variant["key"] != f"{variant['system']}-{variant['machine']}-{variant['pythonAbi']}":
            raise ValueError("pytest runtime manifest platform variant is invalid")
        native = libraries[0]
        if (
            not isinstance(native, dict)
            or set(native) != {"basename", "format", "name", "sha256", "size"}
            or native.get("name") != "opus"
            or type(native.get("basename")) is not str
            or native.get("format") not in {"mach-o", "elf"}
            or type(native.get("sha256")) is not str
            or _SHA256_HEX.fullmatch(native["sha256"]) is None
            or type(native.get("size")) is not int
            or native["size"] <= 0
        ):
            raise ValueError("pytest runtime manifest native library is invalid")
        keys.append(variant["key"])
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise ValueError("pytest runtime manifest platform variants are ambiguous")
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


def _bound_descriptor_target(path: Path) -> Path | None:
    match = _BOUND_DESCRIPTOR_PATH.fullmatch(path.as_posix())
    if match is None:
        return None
    descriptor = os.open(path, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ValueError("bound evidence output is invalid")
        if hasattr(fcntl, "F_GETPATH"):
            raw_target = fcntl.fcntl(descriptor, fcntl.F_GETPATH, b"\0" * 1024)
            target = Path(raw_target.split(b"\0", 1)[0].decode()).resolve(strict=True)
        else:
            target = Path(os.readlink(f"/proc/self/fd/{descriptor}")).resolve(strict=True)
        target_stat = target.stat(follow_symlinks=False)
        if target.is_symlink() or (
            target_stat.st_dev,
            target_stat.st_ino,
        ) != (opened.st_dev, opened.st_ino):
            raise ValueError("bound evidence output is invalid")
        return target
    except (OSError, UnicodeError) as exc:
        raise ValueError("bound evidence output is invalid") from exc
    finally:
        os.close(descriptor)


def _runner_bound_output_targets(paths: Sequence[Path]) -> tuple[Path, ...] | None:
    targets = tuple(_bound_descriptor_target(path) for path in paths)
    if all(target is None for target in targets):
        return None
    if any(target is None for target in targets):
        raise ValueError("bound evidence outputs must be supplied together")
    resolved = tuple(target for target in targets if target is not None)
    validate_distinct_paths(resolved)
    return resolved


def _write_runner_bound_output(path: Path, target: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
    try:
        before = os.fstat(descriptor)
        target_before = target.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or target.is_symlink()
            or (before.st_dev, before.st_ino)
            != (target_before.st_dev, target_before.st_ino)
        ):
            raise RuntimeError("bound evidence output changed")
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        remaining = memoryview(content)
        while remaining:
            count = os.write(descriptor, remaining)
            if count <= 0:
                raise OSError("bound evidence output write failed")
            remaining = remaining[count:]
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        target_after = target.stat(follow_symlinks=False)
        if (
            (after.st_dev, after.st_ino, after.st_nlink, after.st_size)
            != (before.st_dev, before.st_ino, 1, len(content))
            or (target_after.st_dev, target_after.st_ino)
            != (before.st_dev, before.st_ino)
        ):
            raise RuntimeError("bound evidence output changed")
        os.lseek(descriptor, 0, os.SEEK_SET)
    finally:
        os.close(descriptor)


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
            "PYTHONDONTWRITEBYTECODE": "1",
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


def _copy_trusted_pytest_packages(
    destination: Path,
    manifest: Mapping[str, Any],
    *,
    approved_package_roots: Sequence[str] = (),
) -> None:
    approved_roots = (
        [Path(path).resolve(strict=True) for path in approved_package_roots]
        if approved_package_roots
        else _approved_package_roots()
    )
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
        if Path(runtime["pycache"]).exists():
            raise RuntimeError
        for path, binding in runtime.bindings.items():
            require_file_unchanged(path, binding)
        for path, binding in runtime.external_bindings.items():
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


def _python_abi() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def _runtime_platform_variant(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    key = f"{system}-{machine}-{_python_abi()}"
    matches = [item for item in manifest["platformVariants"] if item["key"] == key]
    if len(matches) != 1:
        raise RuntimeError("trusted pytest runtime platform is unsupported")
    return matches[0]


def _native_library_candidates(system: str, machine: str, name: str) -> tuple[Path, ...]:
    if (system, machine, name) == ("darwin", "arm64", "opus"):
        return (
            Path("/opt/homebrew/lib/libopus.dylib"),
            Path("/opt/homebrew/opt/opus/lib/libopus.dylib"),
            Path("/usr/local/lib/libopus.dylib"),
        )
    if (system, machine, name) == ("linux", "x86_64", "opus"):
        return (Path("/usr/lib/x86_64-linux-gnu/libopus.so.0"), Path("/usr/local/lib/libopus.so.0"))
    return ()


def _native_format_matches(content: bytes, format_name: str, machine: str) -> bool:
    if format_name == "mach-o" and machine == "arm64":
        return len(content) >= 8 and content[:4] == b"\xcf\xfa\xed\xfe" and int.from_bytes(content[4:8], "little") == 0x0100000C
    if format_name == "elf" and machine == "x86_64":
        return len(content) >= 20 and content[:5] == b"\x7fELF\x02" and int.from_bytes(content[18:20], "little") == 62
    return False


def _load_native_library(variant: Mapping[str, Any]) -> tuple[Path, BoundFile]:
    expected = variant["nativeLibraries"][0]
    for candidate in _native_library_candidates(
        variant["system"], variant["machine"], expected["name"]
    ):
        try:
            source = candidate.resolve(strict=True)
            opened = source.stat(follow_symlinks=False)
            parent = source.parent.stat(follow_symlinks=False)
            if (
                source.is_symlink()
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or source.name != expected["basename"]
                or stat.S_IMODE(opened.st_mode) & 0o022
                or stat.S_IMODE(parent.st_mode) & 0o022
                or opened.st_uid != parent.st_uid
            ):
                continue
            bound = read_bound_file(source)
        except OSError:
            continue
        if (
            len(bound.content) == expected["size"]
            and secrets.compare_digest(_sha256(bound.content), expected["sha256"])
            and _native_format_matches(bound.content, expected["format"], variant["machine"])
        ):
            return source, bound
    raise RuntimeError("required native library is unavailable")


def _git_blob_digest(content: bytes, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    digest.update(f"blob {len(content)}\0".encode("ascii"))
    digest.update(content)
    return digest.hexdigest()


def _candidate_filesystem_component_key(component: str) -> str:
    # Deterministic evidence targets the conservative default macOS profile,
    # independent of whether the runner happens to use a case-sensitive volume.
    return unicodedata.normalize("NFC", component).casefold()


def _write_candidate_snapshot_file(root: Path, relative: Path, content: bytes) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root, os.O_RDONLY | directory_flag | nofollow_flag)
    try:
        for component in relative.parts[:-1]:
            try:
                os.mkdir(component, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(
                component,
                os.O_RDONLY | directory_flag | nofollow_flag,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        file_descriptor = os.open(
            relative.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow_flag,
            0o600,
            dir_fd=descriptor,
        )
        try:
            remaining = memoryview(content)
            while remaining:
                count = os.write(file_descriptor, remaining)
                if count <= 0:
                    raise OSError("candidate snapshot write failed")
                remaining = remaining[count:]
        finally:
            os.close(file_descriptor)
    finally:
        os.close(descriptor)


def _candidate_tree_entries(
    repo_root: Path,
    expected_git_sha: str,
    module_path: Path,
) -> tuple[str, dict[str, tuple[str, int]]]:
    prefix = module_path.as_posix().rstrip("/")
    object_format = _git_output(
        repo_root, "rev-parse", "--show-object-format"
    ).decode("ascii").strip()
    if object_format not in {"sha1", "sha256"}:
        raise RuntimeError("candidate Git object format is invalid")
    listing = _git_output(
        repo_root,
        "ls-tree",
        "-rz",
        "--full-tree",
        expected_git_sha,
        "--",
        prefix,
    )
    entries = {}
    filesystem_prefixes: dict[tuple[str, ...], tuple[tuple[str, ...], str]] = {}
    for record in listing.split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise RuntimeError("candidate Git tree is invalid")
        mode, object_type, object_id = fields
        try:
            path = raw_path.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("candidate Git tree path is invalid") from exc
        if path == f"{prefix}/.venv311" or path.startswith(f"{prefix}/.venv311/"):
            continue
        if (
            mode not in {b"100644", b"100755"}
            or object_type != b"blob"
            or not path.startswith(prefix + "/")
            or path in entries
            or any(part in {"", ".", ".."} for part in Path(path).parts)
        ):
            raise RuntimeError("candidate Git tree is invalid")
        relative = Path(path).relative_to(module_path).as_posix()
        relative_parts = tuple(relative.split("/"))
        for index in range(1, len(relative_parts)):
            original = relative_parts[:index]
            key = tuple(_candidate_filesystem_component_key(part) for part in original)
            existing = filesystem_prefixes.get(key)
            if existing is not None and (existing[0] != original or existing[1] != "directory"):
                raise RuntimeError("candidate Git tree is invalid")
            filesystem_prefixes[key] = (original, "directory")
        file_key = tuple(
            _candidate_filesystem_component_key(part) for part in relative_parts
        )
        if file_key in filesystem_prefixes:
            raise RuntimeError("candidate Git tree is invalid")
        filesystem_prefixes[file_key] = (relative_parts, "file")
        if Path(relative).suffix.casefold() in {".pyc", ".pyo", ".so", ".dylib", ".dll", ".pyd"}:
            raise RuntimeError("candidate Git tree contains executable artifacts")
        entries[path] = (object_id.decode("ascii"), int(mode, 8))
    if not entries:
        raise RuntimeError("candidate Git tree is empty")
    return object_format, entries


def _seal_candidate_snapshot(root: Path) -> PrivateCandidateSnapshot:
    scratch = tuple(root / name for name in ("tmp", "data"))
    for path in scratch:
        path.mkdir(mode=0o700, exist_ok=True)
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
    )
    directories = sorted(
        [
            root,
            *(path for path in root.rglob("*") if path.is_dir() and path not in scratch),
        ],
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for path in files:
        path.chmod(0o400)
    for path in directories:
        path.chmod(0o500)
    return PrivateCandidateSnapshot(
        root,
        {path: read_bound_file(path) for path in files},
        {path: _runtime_directory_identity(path) for path in directories},
        scratch,
    )


def _verify_candidate_snapshot(snapshot: PrivateCandidateSnapshot) -> None:
    try:
        observed_files = {
            path
            for path in snapshot.root.rglob("*")
            if path.is_file()
        }
        observed_directories = {
            snapshot.root,
            *(
                path
                for path in snapshot.root.rglob("*")
                if path.is_dir() and path not in snapshot.scratch
            ),
        }
        unexpected_files = observed_files - set(snapshot.bindings)
        unexpected_directories = observed_directories - set(snapshot.directories)
        if (
            set(snapshot.bindings) - observed_files
            or set(snapshot.directories) - observed_directories
            or any(
                not any(item == path or item in path.parents for item in snapshot.scratch)
                for path in unexpected_files | unexpected_directories
            )
        ):
            raise RuntimeError
        for path, bound in snapshot.bindings.items():
            require_file_unchanged(path, bound)
        for path, identity in snapshot.directories.items():
            if _runtime_directory_identity(path) != identity:
                raise RuntimeError
    except (OSError, RuntimeError) as exc:
        raise RuntimeError("candidate snapshot changed") from exc


@contextmanager
def _private_candidate_snapshot(
    repo_root: Path,
    expected_git_sha: str,
    module_path: Path,
):
    temporary = Path(tempfile.mkdtemp(prefix="google-live-candidate-"))
    snapshot_root = temporary / "candidate"
    try:
        snapshot_root.mkdir(mode=0o700)
        object_format, entries = _candidate_tree_entries(
            repo_root, expected_git_sha, module_path
        )
        prefix = module_path.as_posix().rstrip("/")
        archive = _git_output(
            repo_root,
            "archive",
            "--format=tar",
            expected_git_sha,
            prefix,
            f":(exclude){prefix}/.venv311",
        )
        extracted = set()
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
            for member in stream.getmembers():
                if member.isdir():
                    continue
                if not member.isfile() or member.name not in entries:
                    raise RuntimeError("candidate Git archive is invalid")
                source = stream.extractfile(member)
                if source is None:
                    raise RuntimeError("candidate Git archive is invalid")
                content = source.read()
                object_id, _mode = entries[member.name]
                if (
                    _git_blob_digest(content, object_format) != object_id
                    or content.startswith(b"version https://git-lfs.github.com/spec/v1\n")
                ):
                    raise RuntimeError("candidate Git archive content is invalid")
                relative = Path(member.name).relative_to(module_path)
                _write_candidate_snapshot_file(snapshot_root, relative, content)
                extracted.add(member.name)
        if extracted != set(entries):
            raise RuntimeError("candidate Git archive is incomplete")
        snapshot = _seal_candidate_snapshot(snapshot_root)
        _verify_candidate_snapshot(snapshot)
        yield snapshot_root
        _verify_candidate_snapshot(snapshot)
    finally:
        _make_runtime_writable(temporary)
        shutil.rmtree(temporary, ignore_errors=True)


@contextmanager
def _private_pytest_runtime(
    repo_root: Path,
    expected_git_sha: str,
    *,
    candidate_root: Path | None = None,
    snapshot_control_root: Path | None = None,
    approved_package_roots: Sequence[str] = (),
):
    temporary = Path(tempfile.mkdtemp(prefix="google-live-pytest-runtime-"))
    try:
        candidate = candidate_root or repo_root
        candidate_import_names = set()
        for source in candidate.rglob("*.py"):
            relative = source.relative_to(candidate)
            if "__pycache__" in relative.parts:
                continue
            candidate_import_names.add(
                relative.stem if len(relative.parts) == 1 else relative.parts[0]
            )
        manifest_path = None
        manifest_bound = None
        if snapshot_control_root is None:
            manifest_content, manifest = _load_trusted_pytest_runtime_manifest(
                repo_root,
                expected_git_sha,
            )
        else:
            manifest_path = (
                snapshot_control_root
                / "tests/fixtures/google_live_pytest_runtime_manifest.json"
            )
            manifest_bound = read_bound_file(manifest_path)
            manifest_content = manifest_bound.content
            manifest = parse_pytest_runtime_manifest(manifest_content)
        if (
            manifest["pythonImplementation"] != sys.implementation.name
            or manifest["pythonMajorMinor"] != f"{sys.version_info.major}.{sys.version_info.minor}"
        ):
            raise RuntimeError("trusted pytest runtime Python constraint does not match")
        variant = _runtime_platform_variant(manifest)
        packages = temporary / "packages"
        _copy_trusted_pytest_packages(
            packages,
            manifest,
            approved_package_roots=approved_package_roots,
        )
        native_source, native_bound = _load_native_library(variant)
        private_native = temporary / "native" / variant["nativeLibraries"][0]["basename"]
        _write_private_snapshot_file(private_native, native_bound.content)
        plugin = temporary / "control" / "pinned_nodeid_plugin.py"
        plugin_path = None
        plugin_bound = None
        if snapshot_control_root is None:
            plugin_content = _load_trusted_nodeid_plugin(
                repo_root,
                expected_git_sha,
                manifest,
            )
        else:
            plugin_path = snapshot_control_root / "scripts/google_live_deterministic_nodeid_plugin.py"
            plugin_bound = read_bound_file(plugin_path)
            plugin_content = plugin_bound.content
            if not secrets.compare_digest(
                _sha256(plugin_content), manifest["plugin"]["sha256"]
            ):
                raise RuntimeError("trusted pytest nodeid plugin integrity check failed")
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
            "repo": str(candidate),
            "liveRepo": str(repo_root),
            "candidateImportNames": sorted(candidate_import_names),
            "dependencies": (
                list(approved_package_roots)
                if approved_package_roots
                else [str(path) for path in _approved_package_roots()]
            ),
            "plugin": str(plugin),
            "opusLibrary": str(private_native),
            "pycache": str(temporary / "blocked-pycache"),
        })
        runtime.manifest_content = manifest_content
        runtime.manifest = manifest
        runtime.external_bindings = {native_source: native_bound}
        _seal_private_pytest_runtime(runtime, temporary)
        _verify_private_pytest_runtime(runtime)
        yield runtime
        _verify_private_pytest_runtime(runtime)
        if manifest_path is not None and manifest_bound is not None:
            require_file_unchanged(manifest_path, manifest_bound)
        if plugin_path is not None and plugin_bound is not None:
            require_file_unchanged(plugin_path, plugin_bound)
    finally:
        _make_runtime_writable(temporary)
        shutil.rmtree(temporary, ignore_errors=True)


def _pytest_command(runtime: Mapping[str, Any], *arguments: str) -> list[str]:
    return [
        sys.executable,
        "-I",
        "-B",
        "-X",
        f"pycache_prefix={runtime['pycache']}",
        str(Path(runtime["repo"]) / "scripts/google_live_pytest_child.py"),
        json.dumps(runtime, separators=(",", ":")),
        *arguments,
    ]


def _snapshot_pytest_arguments(candidate_root: Path, arguments: Sequence[str]) -> list[str]:
    resolved = []
    for argument in arguments:
        test_path, separator, node_suffix = argument.partition("::")
        if test_path.startswith("tests/"):
            argument = str(candidate_root.joinpath(*test_path.split("/")))
            if separator:
                argument += separator + node_suffix
        resolved.append(argument)
    return resolved


def _git_output(repo_root: Path, *arguments: str) -> bytes:
    return _trusted_git_output(repo_root, *arguments)


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


def _produce(
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
    outer_execution_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve(strict=True)
    if outer_execution_context is not None:
        if (
            set(outer_execution_context)
            != {
                "dependencyRoots",
                "evidenceRoot",
                "gitSha",
                "projectRoot",
                "sourceRoot",
            }
            or outer_execution_context["gitSha"] != identity.get("gitSha")
            or not isinstance(outer_execution_context["dependencyRoots"], tuple)
            or any(
                type(path) is not str or not Path(path).is_absolute()
                for path in outer_execution_context["dependencyRoots"]
            )
            or Path(outer_execution_context["sourceRoot"]).resolve(strict=True)
            != repo_root
        ):
            raise ValueError("outer candidate execution context is invalid")
        outer_evidence_root = Path(
            outer_execution_context["evidenceRoot"]
        ).resolve(strict=True)
    else:
        outer_evidence_root = None
    outer_dependency_roots = (
        outer_execution_context["dependencyRoots"]
        if outer_execution_context is not None
        else ()
    )
    paths = [manifest_path, junit_out, report_path]
    bound_targets = _runner_bound_output_targets(paths)
    logical_manifest, logical_junit, logical_report = (
        bound_targets if bound_targets is not None else tuple(paths)
    )
    validate_distinct_paths((logical_manifest, logical_junit, logical_report))
    if bound_targets is None and (junit_out.exists() or report_path.exists()):
        raise ValueError("deterministic outputs must not already exist")
    if bound_targets is not None and any(path.stat().st_size != 0 for path in bound_targets):
        raise ValueError("bound deterministic outputs must be empty")
    if logical_junit.parent.resolve(strict=False) != logical_report.parent.resolve(strict=False):
        raise ValueError("deterministic outputs must share one owned directory")
    evidence_root = logical_junit.parent.parent
    if (
        not evidence_root.is_dir()
        or evidence_root.is_symlink()
        or evidence_root == repo_root
        or (
            outer_evidence_root is None
            and repo_root not in evidence_root.resolve(strict=True).parents
        )
        or (
            outer_evidence_root is not None
            and evidence_root.resolve(strict=True) != outer_evidence_root
        )
    ):
        raise ValueError("evidence root must be a preexisting runner-owned directory")
    if outer_execution_context is None:
        initial_status = git_status() if git_status else _git_output(
            repo_root, "status", "--porcelain=v1", "-z", "--untracked-files=all"
        )
        validate_porcelain_status(initial_status, repo_root, evidence_root)
        baseline_untracked = _untracked_paths(initial_status)
    else:
        baseline_untracked = frozenset()

    def verify_repository(*allowed_generated: Path) -> None:
        if outer_execution_context is not None:
            return
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
    canonical_path = canonical_manifest_path or (
        repo_root / "tests" / "fixtures" / "google_live_deterministic_nodes.txt"
    )
    canonical_bound = (
        read_bound_file(canonical_path)
        if canonical_manifest_path is not None or outer_execution_context is not None
        else _read_candidate_tracked_file(repo_root, canonical_path, identity["gitSha"])
    )
    if bound_targets is not None:
        _write_runner_bound_output(
            manifest_path,
            logical_manifest,
            canonical_bound.content,
        )
    manifest_bound = read_bound_file(logical_manifest)
    if manifest_bound.content != canonical_bound.content:
        raise ValueError("manifest does not match the checked-in canonical manifest")
    nodes = parse_manifest(manifest_bound.content)
    verify_repository()
    child_environment = _pytest_child_environment()
    if outer_execution_context is not None:
        git_root = repo_root
        module_path = Path(".")
    elif run is _default_run:
        git_root = Path(
            _git_output(repo_root, "rev-parse", "--show-toplevel").decode().strip()
        ).resolve(strict=True)
        module_path = repo_root.relative_to(git_root)
    else:
        git_root = repo_root
        module_path = Path(".")

    def candidate_snapshot():
        if outer_execution_context is not None:
            return nullcontext(repo_root)
        if run is _default_run:
            return _private_candidate_snapshot(git_root, identity["gitSha"], module_path)
        return nullcontext(repo_root)

    with candidate_snapshot() as candidate_root:
        pytest_arguments = (
            _snapshot_pytest_arguments(candidate_root, approved_test_files)
            if outer_execution_context is not None
            else list(approved_test_files)
        )
        working_directory = (
            tempfile.TemporaryDirectory(prefix="google-live-pytest-work-")
            if outer_execution_context is not None
            else nullcontext(str(candidate_root))
        )
        with working_directory as pytest_cwd:
            with _private_pytest_runtime(
                repo_root,
                identity["gitSha"],
                candidate_root=candidate_root,
                snapshot_control_root=(
                    repo_root if outer_execution_context is not None else None
                ),
                approved_package_roots=outer_dependency_roots,
            ) as pytest_runtime:
                runtime_manifest_content = pytest_runtime.manifest_content
                collect = run(
                    _pytest_command(
                        pytest_runtime,
                        *pytest_arguments,
                        "--collect-only",
                        "-qq",
                    ),
                    cwd=Path(pytest_cwd),
                    env=child_environment,
                )
                if collect.returncode != 0:
                    raise RuntimeError("pytest collection failed")
    verify_repository()
    collected = [
        line for line in collect.stdout.splitlines() if NODE_PATTERN.fullmatch(line)
    ]
    require_exact_nodes(collected, nodes, label="collection")
    output_parent_identity = snapshot_output_parent(logical_junit)
    descriptor, temporary = tempfile.mkstemp(
        dir=logical_junit.parent,
        prefix=".pytest.",
        suffix=".xml",
    )
    os.close(descriptor)
    temporary_path = Path(temporary)
    temporary_path.unlink()
    published_junit: BoundFile | None = None
    completed_successfully = False
    try:
        with candidate_snapshot() as candidate_root:
            pytest_arguments = (
                _snapshot_pytest_arguments(candidate_root, nodes)
                if outer_execution_context is not None
                else list(nodes)
            )
            working_directory = (
                tempfile.TemporaryDirectory(prefix="google-live-pytest-work-")
                if outer_execution_context is not None
                else nullcontext(str(candidate_root))
            )
            with working_directory as pytest_cwd:
                with _private_pytest_runtime(
                    repo_root,
                    identity["gitSha"],
                    candidate_root=candidate_root,
                    snapshot_control_root=(
                        repo_root if outer_execution_context is not None else None
                    ),
                    approved_package_roots=outer_dependency_roots,
                ) as pytest_runtime:
                    if not secrets.compare_digest(
                        runtime_manifest_content,
                        pytest_runtime.manifest_content,
                    ):
                        raise RuntimeError("trusted pytest runtime manifest changed")
                    completed = run(
                        _pytest_command(
                            pytest_runtime,
                            *pytest_arguments,
                            f"--junitxml={temporary_path}",
                            "-q",
                        ),
                        cwd=Path(pytest_cwd),
                        env=child_environment,
                    )
        if completed.returncode != 0:
            raise RuntimeError("pytest failed; deterministic evidence was not published")
        normalized = canonicalize_junit_summary(read_bound_file(temporary_path).content)
        parse_passing_junit(normalized, nodes)
        verify_repository(temporary_path)
        require_file_unchanged(logical_manifest, manifest_bound)
        require_file_unchanged(canonical_path, canonical_bound)
        verify_repository(temporary_path)
        if bound_targets is None:
            atomic_write_exclusive(
                junit_out,
                normalized,
                pre_publish=lambda publish_temp: verify_repository(
                    temporary_path, publish_temp
                ),
                post_publish=lambda: verify_repository(temporary_path, junit_out),
                expected_parent_identity=output_parent_identity,
            )
        else:
            verify_repository(temporary_path, logical_junit)
            _write_runner_bound_output(junit_out, logical_junit, normalized)
            verify_repository(temporary_path, logical_junit)
        temporary_path.unlink()
        published_junit = read_bound_file(logical_junit)
        report = build_report(
            identity,
            manifest_bound.content,
            published_junit.content,
            runtime_manifest_content,
        )
        require_file_unchanged(logical_junit, published_junit)
        report_content = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
        if bound_targets is None:
            atomic_write_exclusive(
                report_path,
                report_content,
                pre_publish=lambda publish_temp: verify_repository(
                    junit_out, publish_temp
                ),
                post_publish=lambda: verify_repository(junit_out, report_path),
                expected_parent_identity=output_parent_identity,
            )
        else:
            verify_repository(logical_junit, logical_report)
            _write_runner_bound_output(report_path, logical_report, report_content)
            verify_repository(logical_junit, logical_report)
        completed_successfully = True
        return report
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
        if (
            bound_targets is None
            and not completed_successfully
            and published_junit is not None
        ):
            _unlink_if_bound(junit_out, published_junit)


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
    outer_execution_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if outer_execution_context is not None:
        return _produce(
            manifest_path=manifest_path,
            junit_out=junit_out,
            report_path=report_path,
            identity=identity,
            repo_root=repo_root,
            run=run,
            git_status=git_status,
            git_head=git_head,
            approved_test_files=approved_test_files,
            canonical_manifest_path=canonical_manifest_path,
            outer_execution_context=outer_execution_context,
        )
    with trusted_git_session():
        return _produce(
            manifest_path=manifest_path,
            junit_out=junit_out,
            report_path=report_path,
            identity=identity,
            repo_root=repo_root,
            run=run,
            git_status=git_status,
            git_head=git_head,
            approved_test_files=approved_test_files,
            canonical_manifest_path=canonical_manifest_path,
            outer_execution_context=None,
        )


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
    outer_execution_context = globals().get("__google_live_execution_context__")
    try:
        produce(
            manifest_path=args.manifest,
            junit_out=args.junit_out,
            report_path=args.report,
            identity=identity,
            repo_root=Path(__file__).resolve().parents[1],
            outer_execution_context=outer_execution_context,
        )
    except (OSError, UnicodeError, ValueError, RuntimeError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
