from __future__ import annotations

import ast
import asyncio
import json
import re
import tokenize
from io import BytesIO
from pathlib import Path

from core.lesson.global_generation_status import GlobalGenerationStatus


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_FILES = [
    ROOT / "app.py",
    ROOT / "core/http_server.py",
    ROOT / "core/lesson/global_generation_poller.py",
    ROOT / "core/lesson/global_generation_sync.py",
    ROOT / "core/lesson/global_generation_status.py",
    ROOT / "core/lesson/global_generation_store.py",
    ROOT / "core/lesson/global_generation_sessions.py",
]
FORBIDDEN_IMPORTS = {
    "resolve_device_identity",
    "post_lesson_sd_sync_result",
}
FORBIDDEN_CALLS = {
    "resolve_device_identity",
    "post_lesson_sd_sync_result",
}
FORBIDDEN_IDENTIFIER_RE = re.compile(
    r"(?:claim.*token|token.*claim|auth.*callback|callback.*auth|admin.*key|authorization.*bearer)",
    re.IGNORECASE,
)
FORBIDDEN_STRING_RE = re.compile(
    r"(?:resolve_device_identity|post_lesson_sd_sync_result|claim[_-]?token|auth[_-]?callback|admin[_-]?key|authorization:\s*bearer)",
    re.IGNORECASE,
)
PRIVATE_RESPONSE_RE = re.compile(
    r"\b(?:mac|uuid|token|callback|private[_-]?url|internal|redis|secret|authorization|admin[_-]?key|claim(?:[_-]?identity)?)\b"
    r"|(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)
CHECKSUM = "a" * 64


def _tokens_without_comments(source: bytes) -> list[tokenize.TokenInfo]:
    return [
        token
        for token in tokenize.tokenize(BytesIO(source).readline)
        if token.type not in {tokenize.COMMENT, tokenize.ENCODING, tokenize.NL}
    ]


def _string_literals(tree: ast.AST) -> list[str]:
    values: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.append(node.value)
    return values


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def test_global_generation_source_uses_closed_public_file_list_without_identity_dependencies():
    missing = [path for path in PRODUCTION_FILES if not path.is_file()]
    assert missing == []

    violations: list[str] = []
    for path in PRODUCTION_FILES:
        source = path.read_bytes()
        tree = ast.parse(source, filename=str(path))
        tokens = _tokens_without_comments(source)
        identifiers = [token.string for token in tokens if token.type == tokenize.NAME]

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported = {alias.name for alias in node.names}
                if imported & FORBIDDEN_IMPORTS:
                    violations.append(f"{path.name}: forbidden import {sorted(imported & FORBIDDEN_IMPORTS)}")
            elif isinstance(node, ast.Import):
                imported = {alias.name.rsplit(".", 1)[-1] for alias in node.names}
                if imported & FORBIDDEN_IMPORTS:
                    violations.append(f"{path.name}: forbidden import {sorted(imported & FORBIDDEN_IMPORTS)}")
            elif isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in FORBIDDEN_CALLS or (name and name.lower() == "mint"):
                    violations.append(f"{path.name}: forbidden call {name}")

        for identifier in identifiers:
            if identifier.lower() == "mint" or FORBIDDEN_IDENTIFIER_RE.search(identifier):
                violations.append(f"{path.name}: forbidden identifier {identifier}")
        for literal in _string_literals(tree):
            if FORBIDDEN_STRING_RE.search(literal):
                violations.append(f"{path.name}: forbidden literal {literal[:80]}")

    assert violations == []


class _Store:
    async def snapshot(self):
        return {
            "acceptedGeneration": 12,
            "acceptedIndexChecksum": CHECKSUM,
            "materializationState": "ready",
            "lastPollAt": "2026-07-25T01:00:00Z",
            "lastMaterializedAt": "2026-07-25T01:00:02Z",
            "lastErrorCode": None,
        }


class _Sessions:
    async def aggregate(self, generation):
        assert generation == 12
        return {"connected": 5, "current": 3, "retrying": 1, "failed": 1}


def test_public_generation_status_matches_cms_identity_and_contains_only_aggregate_public_fields():
    cms_generation = 12
    cms_checksum = CHECKSUM

    status = asyncio.run(GlobalGenerationStatus(_Store(), _Sessions()).snapshot())

    assert status["acceptedGeneration"] == cms_generation
    assert status["indexChecksum"] == cms_checksum
    assert status["materializationState"] == "ready"
    assert status["connections"] == {"connected": 5, "current": 3, "retrying": 1, "failed": 1}
    assert sum(
        status["connections"][key] for key in ("current", "retrying", "failed")
    ) == status["connections"]["connected"]
    assert set(status) == {
        "acceptedGeneration",
        "indexChecksum",
        "materializationState",
        "connections",
        "lastPollAt",
        "lastMaterializedAt",
        "lastErrorCode",
    }
    rendered = json.dumps({"data": status}, sort_keys=True)
    assert PRIVATE_RESPONSE_RE.search(rendered) is None
