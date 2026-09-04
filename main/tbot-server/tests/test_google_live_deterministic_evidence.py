import hashlib
import json
import os
import py_compile
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import google_live_deterministic_evidence as deterministic

IDENTITY = {
    "gitSha": "a" * 40,
    "imageDigest": "sha256:" + "b" * 64,
    "firmwareIdentity": "firmware-1",
    "configFingerprint": "sha256:" + "c" * 64,
    "fixtureSha256": "d" * 64,
}
MODULE_ROOT = Path(__file__).parents[1].resolve()
PINNED_RUNTIME_MANIFEST = (
    MODULE_ROOT / "tests/fixtures/google_live_pytest_runtime_manifest.json"
).read_bytes()
PINNED_NODEID_PLUGIN = (
    MODULE_ROOT / "scripts/google_live_deterministic_nodeid_plugin.py"
).read_bytes()
REAL_LOAD_RUNTIME_MANIFEST = deterministic._load_trusted_pytest_runtime_manifest
REAL_LOAD_NODEID_PLUGIN = deterministic._load_trusted_nodeid_plugin


def _install_pytest_child(root: Path) -> None:
    scripts = root / "scripts"
    scripts.mkdir(exist_ok=True)
    for name in ("google_live_deterministic_evidence.py", "google_live_pytest_child.py"):
        (scripts / name).write_bytes((MODULE_ROOT / "scripts" / name).read_bytes())


@pytest.fixture(autouse=True)
def _pin_runtime_control_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        deterministic,
        "_load_trusted_pytest_runtime_manifest",
        lambda _repo_root, _git_sha: (
            PINNED_RUNTIME_MANIFEST,
            deterministic.parse_pytest_runtime_manifest(PINNED_RUNTIME_MANIFEST),
        ),
    )
    monkeypatch.setattr(
        deterministic,
        "_load_trusted_nodeid_plugin",
        lambda _repo_root, _git_sha, _manifest: PINNED_NODEID_PLUGIN,
    )


def _runtime_manifest_bytes(*, plugin_sha256: str = "e" * 64) -> bytes:
    distributions = []
    for name, packages in sorted(deterministic._PYTEST_DISTRIBUTION_PACKAGES.items()):
        files = [
            {"path": f"{package}/__init__.py", "sha256": hashlib.sha256(package.encode()).hexdigest()}
            for package in sorted(packages)
        ]
        distributions.append(
            {
                "files": files,
                "importNames": sorted(packages),
                "name": name,
                "packages": sorted(packages),
                "version": "1.0",
            }
        )
    value = {
        "distributions": distributions,
        "platformVariants": json.loads(PINNED_RUNTIME_MANIFEST)["platformVariants"],
        "plugin": {
            "path": "main/tbot-server/scripts/google_live_deterministic_nodeid_plugin.py",
            "sha256": plugin_sha256,
        },
        "pythonImplementation": "cpython",
        "pythonMajorMinor": "3.14",
        "schemaVersion": "google-live-pytest-runtime.v2",
    }
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _junit(nodes: list[str], *, status: str = "pass") -> bytes:
    cases = []
    for node in nodes:
        parts = node.split("::")
        classname = parts[0][:-3].replace("/", ".")
        if len(parts) > 2:
            classname += "." + ".".join(parts[1:-1])
        child = "" if status == "pass" else f"<{status} message=\"no details\" />"
        cases.append(
            f'<testcase classname="{classname}" name="{node.rsplit("::", 1)[-1]}" time="0.000">'
            f'<properties><property name="google_live_nodeid" value="{node}" />'
            f"</properties>{child}</testcase>"
        )
    failures = len(nodes) if status == "failure" else 0
    errors = len(nodes) if status == "error" else 0
    skipped = len(nodes) if status == "skipped" else 0
    return (
        '<testsuites name="pytest tests">'
        f'<testsuite name="pytest" tests="{len(nodes)}" failures="{failures}" errors="{errors}" skipped="{skipped}">'
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
    assert item.user_properties == [("google_live_nodeid", item.nodeid)]


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
        b'<testsuite name="pytest" tests="1"',
        b'<testsuite name="pytest" tests="994"',
        1,
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
    for secret in (
        "google Api Key = secret",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
        "GOOGLE&#95;API&#95;KEY=secret",
    ):
        xml = _junit([node]).replace(b'classname="tests.test_a"', f'classname="{secret}"'.encode())
        with pytest.raises(ValueError) as error:
            deterministic.parse_passing_junit(xml, [node])
        assert "secret" not in str(error.value).lower()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GOOGLE_API_KEY", "my-real-credential"),
        ("google api-key", "my-real-credential"),
        ("ＧＯＯＧＬＥ＿ＡＰＩ＿ＫＥＹ", "my-real-credential"),
        ("authorization", "Basic dXNlcjpwYXNz"),
        ("set-cookie", "sessionid=private"),
        ("session_resumption_handle", "private"),
        ("token", "private"),
        ("benign_property", "safe-value"),
    ],
)
def test_junit_allows_only_the_exact_nodeid_property_pair(name: str, value: str) -> None:
    node = "tests/test_a.py::test_one"
    extra = f'<property name="{name}" value="{value}" />'.encode()
    xml = _junit([node]).replace(b"</properties>", extra + b"</properties>")
    with pytest.raises(ValueError) as error:
        deterministic.parse_passing_junit(xml, [node])
    assert value.lower() not in str(error.value).lower()


def test_junit_rejects_character_reference_sensitive_property_name() -> None:
    node = "tests/test_a.py::test_one"
    extra = b'<property name="GOOGLE&#95;API&#95;KEY" value="private" />'
    xml = _junit([node]).replace(b"</properties>", extra + b"</properties>")
    with pytest.raises(ValueError):
        deterministic.parse_passing_junit(xml, [node])


def test_junit_rejects_suite_level_properties_even_when_benign() -> None:
    node = "tests/test_a.py::test_one"
    extra = b'<properties><property name="environment" value="test" /></properties>'
    xml = _junit([node]).replace(b"<testcase", extra + b"<testcase", 1)
    with pytest.raises(ValueError):
        deterministic.parse_passing_junit(xml, [node])


@pytest.mark.parametrize(
    "sensitive",
    [
        "ＧＯＯＧＬＥ＿ＡＰＩ＿ＫＥＹ＝secret",
        "ＡＵＴＨＯＲＩＺＡＴＩＯＮ：Ｂｅａｒｅｒ private",
        "ＳＥＣＲＥＴ＝private",
        "𝔾𝕆𝕆𝔾𝕃𝔼_𝔸ℙ𝕀_𝕂𝔼𝕐=private",
        "S\u0332ECRET=private",
        "token\u00a0=private",
    ],
)
def test_junit_privacy_normalizes_allowed_attribute_values(sensitive: str) -> None:
    node = "tests/test_a.py::test_one"
    xml = _junit([node]).replace(b'classname="tests.test_a"', f'classname="{sensitive}"'.encode())
    with pytest.raises(ValueError) as error:
        deterministic.parse_passing_junit(xml, [node])
    assert "private" not in str(error.value).lower()


@pytest.mark.parametrize("invisible", ["\u200b", "\u200e", "\u202e", "\u2066", "\u0085"])
def test_junit_rejects_invisible_bidi_and_unicode_controls(invisible: str) -> None:
    node = "tests/test_a.py::test_one"
    xml = _junit([node]).replace(b'classname="tests.test_a"', f'classname="safe{invisible}value"'.encode())
    with pytest.raises(ValueError):
        deterministic.parse_passing_junit(xml, [node])


def test_junit_allows_benign_non_ascii_attribute_value() -> None:
    node = "tests/test_a.py::test_one"
    xml = _junit([node])
    assert deterministic.parse_passing_junit(xml, [node])["tests"] == 1


@pytest.mark.parametrize(
    "sensitive",
    [
        "credentials=private", "access_token=private", "refresh-token:private",
        "SESSION TOKEN = private", "auth_token=private", "client_secret=private",
        "public key=private", "private-key:private", "password=private",
        "cookie=sessionid", "session_handle=private", "ＡＣＣＥＳＳ＿ＴＯＫＥＮ＝private",
    ],
)
def test_normalized_sensitive_key_grammar_covers_compound_credentials(sensitive: str) -> None:
    assert deterministic._junit_value_is_sensitive(sensitive)


@pytest.mark.parametrize(
    "benign", ["tokenCount", "secretary", "public-keynote", "cookiejar", "handleCount"]
)
def test_normalized_sensitive_key_grammar_avoids_benign_near_misses(benign: str) -> None:
    assert not deterministic._junit_value_is_sensitive(benign)


@pytest.mark.parametrize(
    "sensitive",
    [
        "https://example.test/path?access_token=private",
        "safe client_secret=private",
        "prefix refresh_token=private",
        "host?credentials=private",
        "safe=1&session_token=private",
        "path;auth-token:private",
        "https://x/#access%5Ftoken=private",
        "safe=1＆ＡＣＣＥＳＳ＿ＴＯＫＥＮ＝private",
    ],
)
def test_sensitive_key_grammar_scans_every_embedded_pair(sensitive: str) -> None:
    assert deterministic._junit_value_is_sensitive(sensitive)


@pytest.mark.parametrize(
    "benign",
    ["https://example.test/?count=1", "safe client count=2", "path;refresh-rate=60"],
)
def test_sensitive_key_grammar_allows_benign_embedded_pairs(benign: str) -> None:
    assert not deterministic._junit_value_is_sensitive(benign)


@pytest.mark.parametrize(
    "benign",
    [
        "basic geometry",
        "basic-auth concepts",
        "bearer plants",
        "bearer plants.are.beautiful",
        "bearer extraordinarily-beautiful-wildflowers",
        "Bearer plants-are-beautiful-in-spring-2026",
        "Bearer release-candidate-conversation-number-12",
        "Bearer release-candidate-ready-v2",
        "Bearer abc123",
        "Bearer short-opaque-7",
        "Bearer abcdefghijklmnopqrs",
        "coverage is 100% complete",
        "coverage reached 100%",
        "literal percent % text",
        "basic%20geometry",
        "count%253D1",
    ],
)
def test_sensitive_scheme_grammar_allows_benign_language(benign: str) -> None:
    assert not deterministic._junit_value_is_sensitive(benign)


@pytest.mark.parametrize(
    "sensitive",
    [
        "Authorization: Basic dXNlcjpwYXNzd29yZA==",
        "Authorization: Basic ordinarywords",
        "authorization = Basic dXNlcjpwYXNzd29yZA==",
        "Basic dXNlcjpwYXNzd29yZA==",
        "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
        "Ｂｅａｒｅｒ　eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
        "Bearer abcdefghijklmnopqrstuvwxyz0123456789_-",
        "Bearer abcdefghijklmnop1234",
        "Bearer abcdefghijklmnopqrst",
        "Bearer ABCDEFGHIJKLMNOPQRST",
        "Bearer AbCdEfGhIjKlMnOpQrSt",
        "Bearer extraordinarilybeautifulwildflowers",
        "Bearer 0123456789abcdef0123456789abcdef",
        "Bearer 123e4567-e89b-12d3-a456-426614174000",
        "Bearer dXNlcjpwYXNzd29yZA==",
        "Bearer ghp_0123456789abcdef",
        "Bearer AIza0123456789abcdefghij",
        "Bearer qzmxncbv-asdfghjkl-qwertyuiop123",
        "Bearer qzmxncbv-asdfghjkl-qwertyuiop",
        "Bearer qzmxncbvasdf-ghjklqwertyuiop",
        "Ｂｅａｒｅｒ　ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐ１２３４",
        "Bearer%20abcdefghijklmnop1234",
        "access%255Ftoken%253Dprivate",
        "access%25255Ftoken%25253Dprivate",
    ],
)
def test_sensitive_scheme_and_encoded_credential_grammar(sensitive: str) -> None:
    assert deterministic._junit_value_is_sensitive(sensitive)


@pytest.mark.parametrize(
    "invalid",
    ["bad%2", "bad%GG", "%C3%28", "%252525255F", "%25252525GG"],
)
def test_sensitive_grammar_fails_closed_for_invalid_or_nonconvergent_percent_encoding(
    invalid: str,
) -> None:
    assert deterministic._junit_value_is_sensitive(invalid)


@pytest.mark.parametrize(
    "topic",
    [
        "basic geometry",
        "basic-auth concepts",
        "bearer plants",
        "Bearer plants-are-beautiful-in-spring-2026",
        "Bearer release-candidate-conversation-number-12",
        "Bearer release-candidate-ready-v2",
    ],
)
def test_junit_allows_benign_parametrized_nodeid_language(topic: str) -> None:
    node = f"tests/test_a.py::test_topic[{topic}]"
    assert deterministic.parse_passing_junit(_junit([node]), [node])["tests"] == 1


def test_canonicalizer_strips_freeform_pytest_suite_metadata() -> None:
    node = "tests/test_a.py::test_one"
    raw = _junit([node]).replace(
        b'<testsuite name="pytest" tests=',
        b'<testsuite name="pytest" time="1.000" timestamp="2026-09-02T10:00:00+00:00" hostname="builder" tests=',
        1,
    )
    canonical = deterministic.canonicalize_junit_summary(raw)
    root = ET.fromstring(canonical)
    suite = root.find("testsuite")
    assert root.attrib == {"name": "pytest tests"}
    assert suite is not None
    assert suite.attrib == {
        "name": "pytest",
        "tests": "1",
        "failures": "0",
        "errors": "0",
        "skipped": "0",
    }


def test_junit_binds_classname_to_exact_nodeid_module_and_class() -> None:
    node = "tests/test_a.py::TestA::test_one"
    xml = _junit([node])
    assert deterministic.parse_passing_junit(xml, [node])["tests"] == 1
    with pytest.raises(ValueError):
        deterministic.parse_passing_junit(
            xml.replace(b'tests.test_a.TestA', b'credentials=private'), [node]
        )


@pytest.mark.parametrize(
    "bad_time",
    [
        "", "0", "00.1", "01.000", "1.0", "1.0000", "nan", "inf", "-1.000",
        "+1.000", "1e9", " 1.000", "credentials=private",
    ],
)
def test_junit_requires_canonical_nonnegative_testcase_time(bad_time: str) -> None:
    node = "tests/test_a.py::test_one"
    xml = _junit([node])
    xml = re.sub(rb'time="[^"]*"', f'time="{bad_time}"'.encode(), xml, count=1)
    with pytest.raises(ValueError):
        deterministic.parse_passing_junit(xml, [node])


@pytest.mark.parametrize("mutation", ["missing_time", "extra_attr"])
def test_junit_requires_exact_testcase_attribute_set(mutation: str) -> None:
    node = "tests/test_a.py::test_one"
    xml = _junit([node])
    if mutation == "missing_time":
        xml = xml.replace(b' time="0.000"', b"")
    else:
        xml = xml.replace(b'<testcase ', b'<testcase file="hidden" ', 1)
    with pytest.raises(ValueError):
        deterministic.parse_passing_junit(xml, [node])


def test_build_report_binds_manifest_and_junit_hashes_and_exact_counts() -> None:
    nodes = ["tests/test_a.py::test_one", "tests/test_b.py::test_two"]
    manifest = ("\n".join(nodes) + "\n").encode()
    junit = _junit(nodes)
    report = deterministic.build_report(IDENTITY, manifest, junit, PINNED_RUNTIME_MANIFEST)
    runtime = deterministic.parse_pytest_runtime_manifest(PINNED_RUNTIME_MANIFEST)
    assert report["coverageProof"] == {
        "manifestSchema": "google-live-deterministic-nodes.v1",
        "manifestSha256": hashlib.sha256(manifest).hexdigest(),
        "manifestNodeCount": 2,
        "executedNodeCount": 2,
        "junitSha256": hashlib.sha256(junit).hexdigest(),
        "nodeidPluginSha256": runtime["plugin"]["sha256"],
        "pytestRuntimeManifestSha256": hashlib.sha256(PINNED_RUNTIME_MANIFEST).hexdigest(),
        "pytestRuntimeSchema": "google-live-pytest-runtime.v2",
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


def test_producer_uses_isolated_allowlisted_pytest_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = ["tests/test_a.py::test_one"]
    manifest = tmp_path / "node-manifest.txt"
    manifest.write_text(nodes[0] + "\n", encoding="utf-8")
    junit = tmp_path / "evidence" / "deterministic" / "pytest.xml"
    junit.parent.parent.mkdir()
    report = junit.with_name("report.json")
    calls = []
    sentinel = "must-never-reach-child"
    for name in (
        "GOOGLE_API_KEY",
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "SESSION_TOKEN",
    ):
        monkeypatch.setenv(name, sentinel)
    monkeypatch.setenv("PATH", f"/attacker/{sentinel}")

    def run(command, **kwargs):
        calls.append((command, kwargs))
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
    assert all(isinstance(command, list) for command, _kwargs in calls)
    for command, kwargs in calls:
        assert command[:4] == [sys.executable, "-I", "-B", "-X"]
        assert command[4].startswith("pycache_prefix=")
        assert command[5].endswith("scripts/google_live_pytest_child.py")
        assert "-c" not in command[:6]
        runtime = json.loads(command[6])
        assert runtime["repo"] == str(tmp_path.resolve())
        assert len(runtime["trusted"]) == 1
        assert runtime["trusted"][0] not in report.read_text(encoding="utf-8")
        assert runtime["plugin"].endswith("/control/pinned_nodeid_plugin.py")
        assert kwargs["cwd"] == tmp_path.resolve()
        assert kwargs["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
        assert kwargs["env"]["PYTHONNOUSERSITE"] == "1"
        assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
        assert sentinel not in json.dumps(kwargs["env"])
        assert kwargs["env"]["PATH"] == os.defpath
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "PASS"
    assert sentinel not in report.read_text(encoding="utf-8")
    assert deterministic.parse_passing_junit(junit.read_bytes(), nodes)["tests"] == 1
    assert len({json.loads(command[6])["trusted"][0] for command, _ in calls}) == 2
    assert all(not Path(json.loads(command[6])["trusted"][0]).exists() for command, _ in calls)


def test_producer_writes_runner_bound_output_descriptors(tmp_path: Path) -> None:
    node = "tests/test_a.py::test_one"
    canonical_manifest = tmp_path / "canonical-nodes.txt"
    canonical_manifest.write_text(node + "\n", encoding="utf-8")
    deterministic_dir = tmp_path / "evidence" / "deterministic"
    deterministic_dir.mkdir(parents=True)
    outputs = {
        name: deterministic_dir / name
        for name in ("node-manifest.txt", "pytest.xml", "report.json")
    }
    descriptors = {
        name: os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        for name, path in outputs.items()
    }
    identities = {name: os.fstat(fd).st_ino for name, fd in descriptors.items()}

    def run(command, **_kwargs):
        if "--collect-only" in command:
            return subprocess.CompletedProcess(command, 0, stdout=node + "\n", stderr="")
        junit_arg = next(value for value in command if value.startswith("--junitxml="))
        Path(junit_arg.split("=", 1)[1]).write_bytes(_junit([node]))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    try:
        report = deterministic.produce(
            manifest_path=Path(f"/dev/fd/{descriptors['node-manifest.txt']}"),
            junit_out=Path(f"/dev/fd/{descriptors['pytest.xml']}"),
            report_path=Path(f"/dev/fd/{descriptors['report.json']}"),
            identity=IDENTITY,
            repo_root=tmp_path,
            run=run,
            git_status=lambda: b"",
            git_head=lambda: IDENTITY["gitSha"],
            approved_test_files=("tests/test_a.py",),
            canonical_manifest_path=canonical_manifest,
        )
    finally:
        for descriptor in descriptors.values():
            os.close(descriptor)

    assert report["status"] == "PASS"
    assert outputs["node-manifest.txt"].read_text(encoding="utf-8") == node + "\n"
    assert deterministic.parse_passing_junit(
        outputs["pytest.xml"].read_bytes(), [node]
    )["tests"] == 1
    assert json.loads(outputs["report.json"].read_text(encoding="utf-8"))["status"] == "PASS"
    assert {name: path.stat().st_ino for name, path in outputs.items()} == identities


def test_isolated_pytest_process_ignores_environment_injection(tmp_path: Path) -> None:
    sentinel = "must-never-reach-child"
    injection = tmp_path / "injection"
    injection.mkdir()
    (injection / "sitecustomize.py").write_text(
        "raise RuntimeError('sitecustomize loaded')\n",
        encoding="utf-8",
    )
    (injection / "evil_plugin.py").write_text(
        "raise RuntimeError('plugin loaded')\n",
        encoding="utf-8",
    )
    test_file = tmp_path / "test_isolated_child.py"
    test_file.write_text(
        "import os\n"
        "def test_child_is_clean():\n"
        f"    assert {sentinel!r} not in repr(dict(os.environ))\n",
        encoding="utf-8",
    )
    source = {
        "GOOGLE_API_KEY": sentinel,
        "PATH": str(injection),
        "PYTEST_ADDOPTS": "--collect-only",
        "PYTEST_PLUGINS": "evil_plugin",
        "PYTHONPATH": str(injection),
        "PYTHONSTARTUP": str(injection / "sitecustomize.py"),
    }
    repo_root = Path(__file__).parents[1].resolve()

    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    with deterministic._private_pytest_runtime(repo_root, git_sha) as runtime:
        completed = subprocess.run(
            deterministic._pytest_command(runtime, str(test_file), "-q"),
            cwd=repo_root,
            env=deterministic._pytest_child_environment(source),
            text=True,
            capture_output=True,
            check=False,
        )

    assert completed.returncode == 0
    assert "1 passed" in completed.stdout
    assert sentinel not in completed.stdout + completed.stderr
    assert "PytestAssertRewriteWarning" not in completed.stderr


def test_isolated_pytest_process_removes_live_repo_from_effective_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_repo = Path(__file__).parents[1].resolve()
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    _install_pytest_child(candidate)
    test_file = candidate / "test_no_live_path.py"
    test_file.write_text(
        "import pathlib, sys\n"
        f"LIVE = pathlib.Path({str(live_repo)!r}).resolve()\n"
        "def test_no_live_path():\n"
        "    paths = [pathlib.Path(value).resolve() for value in sys.path if value]\n"
        "    assert not any(path == LIVE or LIVE in path.parents for path in paths)\n",
        encoding="utf-8",
    )
    approved_roots = deterministic._approved_package_roots()
    monkeypatch.setattr(
        deterministic,
        "_approved_package_roots",
        lambda: [*approved_roots, live_repo],
    )
    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=live_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    with deterministic._private_pytest_runtime(
        live_repo, git_sha, candidate_root=candidate
    ) as runtime:
        completed = subprocess.run(
            deterministic._pytest_command(runtime, str(test_file), "-q"),
            cwd=candidate,
            env=deterministic._pytest_child_environment(),
            text=True,
            capture_output=True,
            check=False,
        )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "1 passed" in completed.stdout


def _committed_candidate_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    module = repo / "main/tbot-server"
    tests = module / "tests"
    tests.mkdir(parents=True)
    (module / ".gitignore").write_text(
        "__pycache__/\n*.pyc\n*.so\nsitecustomize.py\n", encoding="utf-8"
    )
    (module / "victim.py").write_text("VALUE = 'trusted'\n", encoding="utf-8")
    (module / "data").mkdir()
    (module / "data/tracked.json").write_text(
        '{"value":"trusted"}\n', encoding="utf-8"
    )
    (tests / "test_candidate.py").write_text(
        "from victim import VALUE\ndef test_value(): assert VALUE == 'trusted'\n",
        encoding="utf-8",
    )
    environment = {
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "HOME": str(tmp_path),
        "PATH": os.defpath,
    }
    subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True, env=environment)
    subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "add", "main/tbot-server"],
        check=True,
        env=environment,
    )
    subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "commit", "-qm", "candidate"],
        check=True,
        env=environment,
    )
    sha = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout.strip()
    return repo, module, sha


def test_candidate_snapshot_uses_only_exact_git_bytes_and_ignores_live_artifacts(
    tmp_path: Path,
) -> None:
    repo, module, sha = _committed_candidate_repo(tmp_path)
    source = module / "victim.py"
    original = source.stat()
    source.write_text("VALUE = 'hostile'\n", encoding="utf-8")
    os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
    py_compile.compile(str(source), cfile=str(module / "__pycache__/victim.pyc"))
    (module / "rogue.pyc").write_bytes(b"sourceless")
    (module / "ignored.so").write_bytes(b"native")
    (module / "sitecustomize.py").write_text(
        "raise RuntimeError('live sitecustomize imported')\n", encoding="utf-8"
    )

    with deterministic._private_candidate_snapshot(
        repo, sha, Path("main/tbot-server")
    ) as snapshot:
        assert (snapshot / "victim.py").read_text(encoding="utf-8") == "VALUE = 'trusted'\n"
        assert not (snapshot / "__pycache__").exists()
        assert not (snapshot / "rogue.pyc").exists()
        assert not (snapshot / "ignored.so").exists()
        assert not (snapshot / "sitecustomize.py").exists()


def test_candidate_snapshot_detects_test_mutation(tmp_path: Path) -> None:
    repo, _module, sha = _committed_candidate_repo(tmp_path)

    with pytest.raises(RuntimeError, match="candidate snapshot changed"):
        with deterministic._private_candidate_snapshot(
            repo, sha, Path("main/tbot-server")
        ) as snapshot:
            target = snapshot / "victim.py"
            target.chmod(0o600)
            target.write_text("VALUE = 'mutated'\n", encoding="utf-8")


def test_candidate_snapshot_detects_tracked_scratch_file_mutation(tmp_path: Path) -> None:
    repo, _module, sha = _committed_candidate_repo(tmp_path)

    with pytest.raises(RuntimeError, match="candidate snapshot changed"):
        with deterministic._private_candidate_snapshot(
            repo, sha, Path("main/tbot-server")
        ) as snapshot:
            target = snapshot / "data/tracked.json"
            target.chmod(0o600)
            target.write_text('{"value":"mutated"}\n', encoding="utf-8")


@pytest.mark.parametrize("suffix", [".PYC", ".So", ".pYD"])
def test_candidate_tree_rejects_mixed_case_executable_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    listing = f"100644 blob {'a' * 40}\tmain/tbot-server/hostile{suffix}\0".encode()
    monkeypatch.setattr(
        deterministic,
        "_git_output",
        lambda _root, *args: b"sha1\n" if args[0] == "rev-parse" else listing,
    )
    with pytest.raises(RuntimeError, match="executable artifacts"):
        deterministic._candidate_tree_entries(tmp_path, "b" * 40, Path("main/tbot-server"))


@pytest.mark.parametrize(
    "paths",
    [
        ("Module.py", "module.py"),
        ("caf\u00e9.py", "cafe\u0301.py"),
    ],
)
def test_candidate_tree_rejects_case_or_unicode_filesystem_collisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, paths: tuple[str, str]
) -> None:
    listing = b"".join(
        f"100644 blob {index:040x}\tmain/tbot-server/{path}\0".encode()
        for index, path in enumerate(paths, 1)
    )
    monkeypatch.setattr(
        deterministic,
        "_git_output",
        lambda _root, *args: b"sha1\n" if args[0] == "rev-parse" else listing,
    )
    with pytest.raises(RuntimeError, match="Git tree"):
        deterministic._candidate_tree_entries(tmp_path, "b" * 40, Path("main/tbot-server"))


@pytest.mark.parametrize(
    "paths",
    [
        ("Dir/a.py", "dir/b.py"),
        ("caf\u00e9/a.py", "cafe\u0301/b.py"),
        ("x", "X/a.py"),
    ],
)
def test_candidate_tree_rejects_parent_or_file_directory_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, paths: tuple[str, str]
) -> None:
    listing = b"".join(
        f"100644 blob {index:040x}\tmain/tbot-server/{path}\0".encode()
        for index, path in enumerate(paths, 1)
    )
    monkeypatch.setattr(
        deterministic,
        "_git_output",
        lambda _root, *args: b"sha1\n" if args[0] == "rev-parse" else listing,
    )
    with pytest.raises(RuntimeError, match="Git tree"):
        deterministic._candidate_tree_entries(tmp_path, "b" * 40, Path("main/tbot-server"))


def test_candidate_tree_allows_multiple_files_under_same_canonical_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing = b"".join(
        f"100644 blob {index:040x}\tmain/tbot-server/Dir/{name}\0".encode()
        for index, name in enumerate(("a.py", "b.py"), 1)
    )
    monkeypatch.setattr(
        deterministic,
        "_git_output",
        lambda _root, *args: b"sha1\n" if args[0] == "rev-parse" else listing,
    )

    _format, entries = deterministic._candidate_tree_entries(
        tmp_path, "b" * 40, Path("main/tbot-server")
    )

    assert len(entries) == 2


def test_candidate_tree_rejects_exact_duplicate_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = f"100644 blob {'a' * 40}\tmain/tbot-server/Dir/a.py\0".encode()
    monkeypatch.setattr(
        deterministic,
        "_git_output",
        lambda _root, *args: b"sha1\n" if args[0] == "rev-parse" else record + record,
    )

    with pytest.raises(RuntimeError, match="Git tree"):
        deterministic._candidate_tree_entries(tmp_path, "b" * 40, Path("main/tbot-server"))


def test_private_pytest_runtime_rejects_record_hash_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = tmp_path / "site-packages"
    package_file = package_root / "pytest" / "__init__.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_bytes(b"tampered")
    fake_hash = SimpleNamespace(mode="sha256", value="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    package_path = SimpleNamespace(
        parts=("pytest", "__init__.py"),
        hash=fake_hash,
        suffix=".py",
        __fspath__=lambda: "pytest/__init__.py",
    )
    fake_distribution = SimpleNamespace(
        files=[package_path],
        version="1.0",
        locate_file=lambda path: package_root if path == "" else package_root / Path(*path.parts),
    )
    monkeypatch.setattr(deterministic, "_PYTEST_DISTRIBUTION_PACKAGES", {"pytest": {"pytest"}})
    monkeypatch.setattr(deterministic, "_approved_package_roots", lambda: [package_root])
    monkeypatch.setattr(deterministic, "distribution", lambda _name: fake_distribution)

    manifest = json.loads(_runtime_manifest_bytes())
    manifest["distributions"] = [
        {
            "files": [
                {
                    "path": "pytest/__init__.py",
                    "sha256": hashlib.sha256(b"expected").hexdigest(),
                }
            ],
            "importNames": ["pytest"],
            "name": "pytest",
            "packages": ["pytest"],
            "version": "1.0",
        }
    ]
    with pytest.raises(RuntimeError, match="integrity"):
        deterministic._copy_trusted_pytest_packages(tmp_path / "snapshot", manifest)


@pytest.mark.parametrize(
    "mutation",
    ["duplicate_file", "duplicate_package", "traversal", "noncanonical", "privacy"],
)
def test_pytest_runtime_manifest_rejects_ambiguous_or_unsafe_content(mutation: str) -> None:
    value = json.loads(_runtime_manifest_bytes())
    if mutation == "duplicate_file":
        value["distributions"][0]["files"].append(value["distributions"][0]["files"][0])
    elif mutation == "duplicate_package":
        value["distributions"][0]["packages"].append(value["distributions"][0]["packages"][0])
    elif mutation == "traversal":
        value["distributions"][0]["files"][0]["path"] = "../pytest.py"
    elif mutation == "privacy":
        value["plugin"]["path"] = "Authorization=Bearer secret"
    content = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if mutation == "noncanonical":
        content = json.dumps(value, indent=2).encode()

    with pytest.raises(ValueError, match="runtime manifest") as error:
        deterministic.parse_pytest_runtime_manifest(content)

    assert "secret" not in str(error.value).lower()


def test_pytest_runtime_manifest_loader_uses_exact_candidate_sha_across_head_aba(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_sha = "a" * 40
    other_sha = "b" * 40
    canonical = _runtime_manifest_bytes()
    objects = []

    def git_output(_repo_root: Path, *arguments: str) -> bytes:
        if arguments[:2] == ("rev-parse", "--show-toplevel"):
            return str(tmp_path).encode() + b"\n"
        if arguments[0] == "show":
            objects.append(arguments[1])
            if arguments[1].startswith("HEAD:") or arguments[1].startswith(f"{other_sha}:"):
                return b"{}\n"
            return canonical
        raise AssertionError(arguments)

    monkeypatch.setattr(deterministic, "_git_output", git_output)

    content, manifest = REAL_LOAD_RUNTIME_MANIFEST(
        tmp_path,
        candidate_sha,
    )

    assert content == canonical
    assert manifest["schemaVersion"] == "google-live-pytest-runtime.v2"
    assert objects == [
        f"{candidate_sha}:main/tbot-server/tests/fixtures/google_live_pytest_runtime_manifest.json"
    ]


def test_checked_in_runtime_manifest_and_plugin_are_bound_to_current_git_object() -> None:
    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=MODULE_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    content, manifest = REAL_LOAD_RUNTIME_MANIFEST(MODULE_ROOT, git_sha)
    plugin = REAL_LOAD_NODEID_PLUGIN(MODULE_ROOT, git_sha, manifest)

    assert content == PINNED_RUNTIME_MANIFEST
    assert plugin == PINNED_NODEID_PLUGIN


def test_checked_in_runtime_manifest_pins_pytest_top_level_py_module() -> None:
    manifest = deterministic.parse_pytest_runtime_manifest(PINNED_RUNTIME_MANIFEST)
    pytest_distribution = next(
        item for item in manifest["distributions"] if item["name"] == "pytest"
    )

    assert "py" in pytest_distribution["importNames"]
    assert any(entry["path"] == "py.py" for entry in pytest_distribution["files"])


def test_checked_in_runtime_manifest_pins_opus_python_and_native_runtime() -> None:
    manifest = deterministic.parse_pytest_runtime_manifest(PINNED_RUNTIME_MANIFEST)
    opus = next(item for item in manifest["distributions"] if item["name"] == "opuslib-next")

    assert opus["importNames"] == ["opuslib_next"]
    assert any(entry["path"] == "opuslib_next/api/__init__.py" for entry in opus["files"])
    variant = deterministic._runtime_platform_variant(manifest)
    assert variant["key"] == "darwin-arm64-cp314"
    assert variant["nativeLibraries"][0]["name"] == "opus"
    serialized = json.dumps(manifest["platformVariants"])
    assert all(field not in serialized for field in ('"path"', '"device"', '"inode"', '"uid"'))


def test_runtime_manifest_rejects_missing_current_platform_variant() -> None:
    manifest = json.loads(PINNED_RUNTIME_MANIFEST)
    variant = manifest["platformVariants"][0]
    variant.update(key="linux-x86_64-cp314", system="linux", machine="x86_64")
    parsed = deterministic.parse_pytest_runtime_manifest(
        (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    )

    with pytest.raises(RuntimeError, match="platform is unsupported"):
        deterministic._runtime_platform_variant(parsed)


def test_native_discovery_accepts_identical_binary_at_alternate_trusted_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    variant = deterministic._runtime_platform_variant(
        deterministic.parse_pytest_runtime_manifest(PINNED_RUNTIME_MANIFEST)
    )
    _source, installed = deterministic._load_native_library(variant)
    alternate = tmp_path / variant["nativeLibraries"][0]["basename"]
    alternate.write_bytes(installed.content)
    alternate.chmod(0o444)
    monkeypatch.setattr(
        deterministic,
        "_native_library_candidates",
        lambda *_args: (alternate,),
    )

    discovered, bound = deterministic._load_native_library(variant)

    assert discovered == alternate
    assert bound.content == installed.content


@pytest.mark.parametrize("mutation", ["tampered", "wrong_arch", "writable_parent"])
def test_native_discovery_rejects_untrusted_or_invalid_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    manifest = deterministic.parse_pytest_runtime_manifest(PINNED_RUNTIME_MANIFEST)
    variant = json.loads(json.dumps(deterministic._runtime_platform_variant(manifest)))
    directory = tmp_path / "native"
    directory.mkdir()
    source = directory / variant["nativeLibraries"][0]["basename"]
    _source, installed = deterministic._load_native_library(variant)
    content = installed.content
    if mutation == "tampered":
        content = content[:-1] + bytes([content[-1] ^ 1])
    elif mutation == "wrong_arch":
        content = b"not-a-mach-o" + content[12:]
        variant["nativeLibraries"][0]["sha256"] = hashlib.sha256(content).hexdigest()
    source.write_bytes(content)
    source.chmod(0o444)
    if mutation == "writable_parent":
        directory.chmod(0o777)
    monkeypatch.setattr(deterministic, "_native_library_candidates", lambda *_args: (source,))

    with pytest.raises(RuntimeError, match="native library"):
        deterministic._load_native_library(variant)


def test_private_runtime_uses_pinned_opus_origin_and_nonexistent_pycache(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    shim = candidate / "opuslib_next/__init__.py"
    shim.parent.mkdir(parents=True)
    shim.write_bytes((MODULE_ROOT / "opuslib_next/__init__.py").read_bytes())
    test_file = candidate / "test_origins.py"
    test_file.write_text(
        "from pathlib import Path\nimport opuslib_next\nimport opuslib_next.api\n"
        "def test_origins():\n"
        " assert 'google-live-pytest-runtime-' in str(Path(opuslib_next.__file__))\n"
        " assert 'google-live-pytest-runtime-' in str(Path(opuslib_next.api.libopus._name))\n",
        encoding="utf-8",
    )
    _install_pytest_child(candidate)
    with deterministic._private_pytest_runtime(
        MODULE_ROOT, "unused", candidate_root=candidate
    ) as runtime:
        assert not Path(runtime["pycache"]).exists()
        completed = subprocess.run(
            deterministic._pytest_command(runtime, str(test_file), "-q"),
            cwd=candidate,
            env=deterministic._pytest_child_environment(),
            text=True,
            capture_output=True,
            check=False,
        )
        assert not Path(runtime["pycache"]).exists()

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_private_runtime_detects_attempted_pycache_creation(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="private pytest runtime changed"):
        with deterministic._private_pytest_runtime(MODULE_ROOT, "unused") as runtime:
            pycache = Path(runtime["pycache"])
            pycache.parent.chmod(0o700)
            pycache.mkdir()
            (pycache / "injected.pyc").write_bytes(b"hostile")


def test_child_rejects_existing_module_cached_artifact(tmp_path: Path) -> None:
    _install_pytest_child(tmp_path)
    test_file = tmp_path / "test_cached.py"
    test_file.write_text(
        "import sys\n"
        "def test_cached():\n"
        " sys.modules[__name__].__cached__ = __file__\n",
        encoding="utf-8",
    )
    with deterministic._private_pytest_runtime(
        MODULE_ROOT, "unused", candidate_root=tmp_path
    ) as runtime:
        completed = subprocess.run(
            deterministic._pytest_command(runtime, str(test_file), "-q"),
            cwd=tmp_path,
            env=deterministic._pytest_child_environment(),
            text=True,
            capture_output=True,
            check=False,
        )

    assert completed.returncode != 0
    assert "hostile" not in completed.stdout + completed.stderr


def test_private_runtime_rejects_native_platform_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deterministic.platform, "machine", lambda: "wrong-architecture")
    with pytest.raises(RuntimeError, match="platform is unsupported"):
        with deterministic._private_pytest_runtime(MODULE_ROOT, "unused"):
            pass


def test_private_runtime_shadows_injected_live_top_level_py_module(tmp_path: Path) -> None:
    _install_pytest_child(tmp_path)
    sentinel = "live py.py injection executed"
    injection = tmp_path / "injection"
    injection.mkdir()
    (injection / "py.py").write_text(f"raise RuntimeError({sentinel!r})\n", encoding="utf-8")
    test_file = tmp_path / "test_py_origin.py"
    test_file.write_text(
        "from pathlib import Path\n"
        "import py\n"
        "def test_py_is_private():\n"
        "    assert 'google-live-pytest-runtime-' in str(Path(py.__file__))\n",
        encoding="utf-8",
    )

    with deterministic._private_pytest_runtime(tmp_path, IDENTITY["gitSha"]) as runtime:
        runtime["dependencies"].insert(0, str(injection))
        completed = subprocess.run(
            deterministic._pytest_command(runtime, str(test_file), "-q"),
            cwd=tmp_path,
            env=deterministic._pytest_child_environment(),
            text=True,
            capture_output=True,
            check=False,
        )

    assert completed.returncode == 0
    assert "1 passed" in completed.stdout
    assert sentinel not in completed.stdout + completed.stderr


def test_private_runtime_origin_audit_rejects_control_module_outside_snapshot(
    tmp_path: Path,
) -> None:
    _install_pytest_child(tmp_path)
    test_file = tmp_path / "test_origin_escape.py"
    test_file.write_text(
        "import py\n"
        "def test_escape():\n"
        "    py.__file__ = '/attacker/live-site-packages/py.py'\n",
        encoding="utf-8",
    )

    with deterministic._private_pytest_runtime(tmp_path, IDENTITY["gitSha"]) as runtime:
        completed = subprocess.run(
            deterministic._pytest_command(runtime, str(test_file), "-q"),
            cwd=tmp_path,
            env=deterministic._pytest_child_environment(),
            text=True,
            capture_output=True,
            check=False,
        )

    assert completed.returncode != 0
    assert "attacker" not in completed.stdout + completed.stderr


def test_runtime_source_rejects_package_and_rewritten_record_against_git_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = tmp_path / "site-packages"
    package_file = package_root / "pytest" / "__init__.py"
    package_file.parent.mkdir(parents=True)
    original = b"trusted"
    package_file.write_bytes(original)
    manifest = json.loads(_runtime_manifest_bytes())
    manifest["distributions"] = [
        {
            "files": [
                {"path": "pytest/__init__.py", "sha256": hashlib.sha256(original).hexdigest()}
            ],
            "importNames": ["pytest"],
            "name": "pytest",
            "packages": ["pytest"],
            "version": "1.0",
        }
    ]
    package_file.write_bytes(b"attacker")
    rewritten_record_hash = SimpleNamespace(
        mode="sha256",
        value="ignored-because-record-is-not-a-trust-anchor",
    )
    package_path = SimpleNamespace(
        parts=("pytest", "__init__.py"),
        hash=rewritten_record_hash,
        suffix=".py",
    )
    fake_distribution = SimpleNamespace(
        files=[package_path],
        version="1.0",
        locate_file=lambda path: package_root if path == "" else package_root / Path(*path.parts),
    )
    monkeypatch.setattr(deterministic, "_approved_package_roots", lambda: [package_root])
    monkeypatch.setattr(deterministic, "distribution", lambda _name: fake_distribution)

    with pytest.raises(RuntimeError, match="integrity"):
        deterministic._copy_trusted_pytest_packages(tmp_path / "snapshot", manifest)


@pytest.mark.parametrize("mutation", ["missing", "tampered"])
def test_nodeid_plugin_loader_fails_closed_on_missing_or_tampered_git_blob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    candidate_sha = "a" * 40
    plugin = b"def pytest_collection_modifyitems(session, items):\n    pass\n"
    manifest = json.loads(_runtime_manifest_bytes(plugin_sha256=hashlib.sha256(plugin).hexdigest()))
    objects = []

    def git_output(_repo_root: Path, *arguments: str) -> bytes:
        if arguments[:2] == ("rev-parse", "--show-toplevel"):
            return str(tmp_path).encode() + b"\n"
        assert arguments[0] == "show"
        objects.append(arguments[1])
        if mutation == "missing":
            raise RuntimeError("missing")
        return plugin + b"# attacker\n"

    monkeypatch.setattr(deterministic, "_git_output", git_output)

    with pytest.raises(RuntimeError, match="plugin"):
        REAL_LOAD_NODEID_PLUGIN(tmp_path, candidate_sha, manifest)

    assert objects == [
        f"{candidate_sha}:main/tbot-server/scripts/google_live_deterministic_nodeid_plugin.py"
    ]


def test_private_pytest_runtime_preimports_trusted_packages_before_candidate_repo(
    tmp_path: Path,
) -> None:
    _install_pytest_child(tmp_path)
    sentinel = "candidate pytest shadow executed"
    (tmp_path / "pytest.py").write_text(f"raise RuntimeError({sentinel!r})\n", encoding="utf-8")
    shadow_plugin = tmp_path / "pytest_asyncio" / "plugin.py"
    shadow_plugin.parent.mkdir()
    shadow_plugin.write_text(f"raise RuntimeError({sentinel!r})\n", encoding="utf-8")
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "google_live_deterministic_nodeid_plugin.py").write_text(
        f"raise RuntimeError({sentinel!r})\n",
        encoding="utf-8",
    )
    test_file = tmp_path / "test_candidate.py"
    test_file.write_text("def test_candidate():\n    assert True\n", encoding="utf-8")

    with deterministic._private_pytest_runtime(tmp_path, IDENTITY["gitSha"]) as runtime:
        completed = subprocess.run(
            deterministic._pytest_command(runtime, str(test_file), "-q"),
            cwd=tmp_path,
            env=deterministic._pytest_child_environment(),
            text=True,
            capture_output=True,
            check=False,
        )

    assert completed.returncode == 0
    assert "1 passed" in completed.stdout
    assert sentinel not in completed.stdout + completed.stderr


@pytest.mark.parametrize("phase", ["collect", "run"])
def test_producer_rejects_private_runtime_mutation_even_when_candidate_restores_bytes(
    tmp_path: Path,
    phase: str,
) -> None:
    node = "tests/test_a.py::test_one"
    manifest = tmp_path / "node-manifest.txt"
    manifest.write_text(node + "\n", encoding="utf-8")
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    junit = evidence_root / "deterministic" / "pytest.xml"
    report = junit.with_name("report.json")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        is_collect = "--collect-only" in command
        if (phase == "collect" and is_collect) or (phase == "run" and not is_collect):
            runtime = json.loads(command[6])
            target = Path(runtime["trusted"][0]) / "pytest" / "__init__.py"
            original = target.read_bytes()
            target.chmod(0o600)
            target.write_bytes(b"raise RuntimeError('candidate mutation')\n")
            target.write_bytes(original)
            target.chmod(0o400)
        if is_collect:
            return subprocess.CompletedProcess(command, 0, stdout=node + "\n", stderr="")
        junit_arg = next(value for value in command if value.startswith("--junitxml="))
        Path(junit_arg.split("=", 1)[1]).write_bytes(_junit([node]))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with pytest.raises(RuntimeError, match="private pytest runtime") as error:
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

    assert "candidate mutation" not in str(error.value)
    assert len(calls) == (1 if phase == "collect" else 2)
    assert not junit.exists()
    assert not report.exists()


def test_verified_snapshot_is_unchanged_when_live_package_changes_after_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = tmp_path / "site-packages"
    package_file = package_root / "pytest" / "__init__.py"
    package_file.parent.mkdir(parents=True)
    trusted = b"trusted runtime\n"
    package_file.write_bytes(trusted)
    manifest = json.loads(_runtime_manifest_bytes())
    manifest["distributions"] = [
        {
            "files": [
                {"path": "pytest/__init__.py", "sha256": hashlib.sha256(trusted).hexdigest()}
            ],
            "importNames": ["pytest"],
            "name": "pytest",
            "packages": ["pytest"],
            "version": "1.0",
        }
    ]
    fake_distribution = SimpleNamespace(
        files=[SimpleNamespace(parts=("pytest", "__init__.py"), suffix=".py")],
        version="1.0",
        locate_file=lambda path: package_root if path == "" else package_root / Path(*path.parts),
    )
    monkeypatch.setattr(deterministic, "_approved_package_roots", lambda: [package_root])
    monkeypatch.setattr(deterministic, "distribution", lambda _name: fake_distribution)
    snapshot = tmp_path / "snapshot"

    deterministic._copy_trusted_pytest_packages(snapshot, manifest)
    package_file.write_bytes(b"attacker replacement\n")

    assert (snapshot / "pytest" / "__init__.py").read_bytes() == trusted


@pytest.mark.parametrize("mutation", ["missing", "extra_python", "extra_native"])
def test_runtime_source_rejects_missing_or_extra_executable_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    package_root = tmp_path / "site-packages"
    package_file = package_root / "pytest" / "__init__.py"
    package_file.parent.mkdir(parents=True)
    trusted = b"trusted runtime\n"
    package_file.write_bytes(trusted)
    manifest = json.loads(_runtime_manifest_bytes())
    manifest["distributions"] = [
        {
            "files": [
                {"path": "pytest/__init__.py", "sha256": hashlib.sha256(trusted).hexdigest()}
            ],
            "importNames": ["pytest"],
            "name": "pytest",
            "packages": ["pytest"],
            "version": "1.0",
        }
    ]
    if mutation == "missing":
        package_file.unlink()
        owned_files = [SimpleNamespace(parts=("pytest", "__init__.py"), suffix=".py")]
    else:
        suffix = ".py" if mutation == "extra_python" else ".so"
        package_file.with_name("attacker" + suffix).write_bytes(b"attacker")
        owned_files = [
            SimpleNamespace(parts=("pytest", "__init__.py"), suffix=".py"),
            SimpleNamespace(parts=("pytest", "attacker" + suffix), suffix=suffix),
        ]
    fake_distribution = SimpleNamespace(
        files=owned_files,
        version="1.0",
        locate_file=lambda path: package_root if path == "" else package_root / Path(*path.parts),
    )
    monkeypatch.setattr(deterministic, "_approved_package_roots", lambda: [package_root])
    monkeypatch.setattr(deterministic, "distribution", lambda _name: fake_distribution)

    with pytest.raises(RuntimeError, match="file set|unavailable"):
        deterministic._copy_trusted_pytest_packages(tmp_path / "snapshot", manifest)


def test_candidate_canonical_manifest_uses_immutable_sha_across_head_aba(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_sha = "a" * 40
    other_sha = "b" * 40
    repo_root = tmp_path / "main" / "tbot-server"
    fixture = repo_root / "tests" / "fixtures" / "google_live_deterministic_nodes.txt"
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(b"tests/test_a.py::test_one\n")
    manifest = repo_root / "node-manifest.txt"
    manifest.write_bytes(fixture.read_bytes())
    evidence_root = repo_root / "evidence"
    evidence_root.mkdir()
    junit = evidence_root / "deterministic" / "pytest.xml"
    report = junit.with_name("report.json")
    show_objects = []

    def git_output(_repo_root: Path, *arguments: str) -> bytes:
        if arguments[:2] == ("rev-parse", "--show-toplevel"):
            return str(tmp_path).encode() + b"\n"
        if arguments[0] == "ls-files":
            return b"main/tbot-server/tests/fixtures/google_live_deterministic_nodes.txt\n"
        if arguments[0] == "show":
            show_objects.append(arguments[1])
            if arguments[1].startswith(f"{other_sha}:") or arguments[1].startswith("HEAD:"):
                return b"tests/test_b.py::test_other\n"
            return fixture.read_bytes()
        raise AssertionError(arguments)

    monkeypatch.setattr(deterministic, "_git_output", git_output)

    def run(command, **kwargs):
        if "--collect-only" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="tests/test_a.py::test_one\n",
                stderr="",
            )
        junit_arg = next(value for value in command if value.startswith("--junitxml="))
        Path(junit_arg.split("=", 1)[1]).write_bytes(_junit(["tests/test_a.py::test_one"]))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    deterministic.produce(
        manifest_path=manifest,
        junit_out=junit,
        report_path=report,
        identity=IDENTITY,
        repo_root=repo_root,
        run=run,
        git_status=lambda: b"",
        git_head=lambda: candidate_sha,
        approved_test_files=("tests/test_a.py",),
    )

    assert report.exists()
    assert show_objects == [
        f"{candidate_sha}:main/tbot-server/tests/fixtures/google_live_deterministic_nodes.txt"
    ]


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
