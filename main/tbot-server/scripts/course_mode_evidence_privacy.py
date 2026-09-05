#!/usr/bin/env python3
"""Bounded signatures for audio content forbidden in redacted evidence."""

from __future__ import annotations

import base64
import binascii
import io
import json
import re
import stat
import zipfile
import zlib
from pathlib import PurePosixPath

AUDIO_MAGIC = (b"RIFF", b"ID3", b"OggS", b"fLaC", b"ADIF")
ISO_AUDIO_BRANDS = {b"M4A ", b"M4B ", b"M4P ", b"F4A ", b"F4B "}
ISO_AUDIO_SAMPLE_ENTRIES = (b"mp4a", b"enca", b"alac", b"ac-3", b"ec-3", b"Opus", b"fLaC")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SANITIZED_CONTAINER_KEYS = {"entries", "files", "schemaVersion"}
SANITIZED_ENTRY_KEYS = {"path", "sha256", "bytes", "classification", "action", "reason", "timestamp"}
SAFE_LABEL = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
SANITIZED_REASONS = {
    "raw capture excluded", "private content removed", "unsupported artifact",
    "superseded artifact", "quarantined before audit",
}
RFC3339_UTC = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$")
MAX_ARCHIVE_MEMBERS = 4096
MAX_ARCHIVE_MEMBER_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 2
MAX_BASE64_DEPTH = 3
MAX_BASE64_BLOCKS = 64
MAX_BASE64_DECODED_BYTES = 8 * 1024 * 1024
MAX_SECRET_JSON_DEPTH = 64
MAX_SECRET_JSON_NODES = 10_000
SUPPORTED_ZIP_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
ARCHIVE_SUFFIXES = {".zip", ".tar", ".tgz", ".gz", ".bz2", ".xz", ".7z", ".rar"}
SECRET_KEY_TOKENS = frozenset(
    {
        "authorization", "cookie", "setcookie", "session", "sessionid", "password", "secret",
        "token", "authtoken", "accesstoken", "refreshtoken", "apikey",
    }
)
SECRET_TEXT = re.compile(
    rb"(?i)(?:authorization|cookie|set-cookie|session(?:id)?|password|secret|token)"
    rb"\s*[:=]\s*[\"']?(?!\s*(?:null|none|redacted|<redacted>)(?:[\"']|\s|$))[^\s\"',;}]{3,}"
)
BEARER_VALUE = re.compile(rb"(?i)\bbearer\s+[a-z0-9._~+/=-]{4,}")
TRANSCRIPT_TEXT = re.compile(rb"(?i)\b(?:child[-_ ]?)?transcript\s*[:=]")
PRIVATE_PATH = re.compile(r"(?i)(?:^|[-_.\/])(?:audio|transcript|utterance|raw[-_]?speech)(?:[-_.\/]|$)")
# MIME-wrapped and URL-safe encodings are common in exported evidence payloads.
MIN_BASE64_CHARS = 8
BASE64_BLOCK = re.compile(
    rb"(?<![A-Za-z0-9+/_=-])(?:[A-Za-z0-9+/_=-]{8,}|"
    rb"[A-Za-z0-9+/_=-]{4}(?:[ \t]+[A-Za-z0-9+/_=-]{4})+|"
    rb"[A-Za-z0-9+/_=-]{4,}(?:[ \t]*\r?\n[ \t]*[A-Za-z0-9+/_=-]{4,})+)"
    rb"(?![A-Za-z0-9+/_=-])"
)
PLAYWRIGHT_MARKERS = (b"playwright html report", b"playwright-report", b"trace.network", b"trace.trace")
PEM_PRIVATE_KEY = re.compile(
    rb"-----BEGIN (?P<label>(?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY)-----.*?"
    rb"-----END (?P=label)-----",
    re.DOTALL,
)
PRIVATE_MEDIA_SUFFIXES = {
    ".aac", ".avi", ".flac", ".gif", ".jpeg", ".jpg", ".m4a", ".m4v", ".mov",
    ".mp3", ".mp4", ".mpeg", ".mpg", ".ogg", ".png", ".trgb", ".wav", ".webm", ".webp",
}
PRIVATE_MEDIA_MAGIC = (
    b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF", b"OggS",
    b"fLaC", b"ID3", b"\x1a\x45\xdf\xa3",
)


def _bounded_box(data: bytes, type_offset: int) -> tuple[int, int] | None:
    if type_offset < 4:
        return None
    start = type_offset - 4
    size = int.from_bytes(data[start:type_offset], "big")
    if size < 8 or size > len(data) - start:
        return None
    return start, start + size


def _iso_bmff_audio(data: bytes) -> bool:
    ftyp = data.find(b"ftyp", 4)
    box = _bounded_box(data, ftyp)
    if box is None:
        return False
    start, end = box
    brands = {data[offset : offset + 4] for offset in range(start + 8, end - 3, 4)}
    if brands & ISO_AUDIO_BRANDS:
        return True
    search_from = end
    for marker in ISO_AUDIO_SAMPLE_ENTRIES:
        offset = data.find(marker, search_from)
        while offset >= 0:
            if _bounded_box(data, offset) is not None:
                return True
            offset = data.find(marker, offset + 1)
    offset = data.find(b"hdlr", search_from)
    while offset >= 0:
        handler_box = _bounded_box(data, offset)
        if handler_box is not None and offset + 16 <= handler_box[1] and data[offset + 12 : offset + 16] == b"soun":
            return True
        offset = data.find(b"hdlr", offset + 1)
    return False


def contains_audio(data: bytes) -> bool:
    return (
        data.startswith(AUDIO_MAGIC)
        or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)
        or (len(data) >= 2 and data[0] == 0x56 and data[1] & 0xE0 == 0xE0)
        or _iso_bmff_audio(data)
    )


def is_sanitized_manifest(data: bytes) -> bool:
    """Recognize path/hash-only summaries without treating labels as private payload."""
    try:
        document = json.loads(data, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        return False
    if not isinstance(document, dict) or not set(document) <= SANITIZED_CONTAINER_KEYS:
        return False
    if type(document.get("schemaVersion")) is not int or document.get("schemaVersion") != 1:
        return False
    groups = [document[key] for key in ("entries", "files") if key in document]
    if len(groups) != 1 or not isinstance(groups[0], list):
        return False
    for entry in groups[0]:
        if not isinstance(entry, dict) or set(entry) != SANITIZED_ENTRY_KEYS:
            return False
        path = entry.get("path")
        parsed = PurePosixPath(path) if isinstance(path, str) else None
        if parsed is None or parsed.is_absolute() or ".." in parsed.parts:
            return False
        if "sha256" in entry and (
            not isinstance(entry["sha256"], str) or SHA256_RE.fullmatch(entry["sha256"]) is None
        ):
            return False
        if "bytes" in entry and (type(entry["bytes"]) is not int or entry["bytes"] < 0):
            return False
        if (
            not isinstance(entry["action"], str)
            or SAFE_LABEL.fullmatch(entry["action"]) is None
            or not isinstance(entry["classification"], str)
            or SAFE_LABEL.fullmatch(entry["classification"]) is None
            or not isinstance(entry["reason"], str)
            or entry["reason"] not in SANITIZED_REASONS
            or "\n" in entry["reason"]
            or not isinstance(entry["timestamp"], str)
            or RFC3339_UTC.fullmatch(entry["timestamp"]) is None
        ):
            return False
    return not (SECRET_TEXT.search(data) or BEARER_VALUE.search(data))


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _has_secret_json(value: object) -> bool:
    pending: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if depth > MAX_SECRET_JSON_DEPTH or nodes > MAX_SECRET_JSON_NODES:
            return True
        if isinstance(current, dict):
            for key, child in current.items():
                normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold()) if isinstance(key, str) else ""
                if normalized_key in SECRET_KEY_TOKENS and child not in (None, "", "redacted", "<redacted>", False):
                    return True
                pending.append((child, depth + 1))
        elif isinstance(current, list):
            pending.extend((child, depth + 1) for child in current)
    return False


def _contains_secret(data: bytes) -> bool:
    if SECRET_TEXT.search(data) or BEARER_VALUE.search(data):
        return True
    try:
        return _has_secret_json(json.loads(data, object_pairs_hook=_reject_duplicate_keys))
    except RecursionError:
        return True
    except (UnicodeError, json.JSONDecodeError, ValueError):
        return False


def _contains_private_key(data: bytes) -> bool:
    return bool(PEM_PRIVATE_KEY.search(data) or re.search(rb"-----BEGIN [^-\r\n]{0,64}PRIVATE KEY-----", data))


def _base64_payloads(data: bytes):
    for match in BASE64_BLOCK.finditer(data):
        payload = re.sub(rb"[ \t\r\n]", b"", match.group(0))
        if len(payload) < MIN_BASE64_CHARS or len(payload) % 4 == 1:
            continue
        unpadded = payload.rstrip(b"=")
        if b"=" in unpadded or payload.count(b"=") > 2:
            continue
        if len(payload) % 4:
            payload += b"=" * (-len(payload) % 4)
        yield payload


def _decode_base64_payload(payload: bytes) -> bytes | None:
    try:
        return base64.b64decode(payload, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        return None


def _is_plausible_decoded_payload(data: bytes) -> bool:
    """Ignore hash/path-like tokens that decode to opaque binary noise."""
    if not data:
        return False
    if (
        (contains_audio(data) and len(data) >= 32)
        or data.startswith(PRIVATE_MEDIA_MAGIC)
        or data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x01\x02"))
        or TRANSCRIPT_TEXT.search(data)
        or _contains_secret(data)
        or _contains_private_key(data)
        or _looks_like_playwright(data, "")
        or any(_base64_payloads(data))
    ):
        return True
    printable = sum(byte in b"\t\r\n" or 32 <= byte <= 126 for byte in data)
    return printable / len(data) >= 0.75


def _looks_like_playwright(data: bytes, name: str) -> bool:
    lowered = data.lower()
    lowered_name = name.lower().replace("\\", "/")
    return any(marker in lowered for marker in PLAYWRIGHT_MARKERS) or any(
        segment in lowered_name
        for segment in ("playwright-report/", "test-results/", "blob-report/", "trace.zip")
    )


def scan_evidence_payload(
    data: bytes,
    name: str,
    *,
    archive_depth: int = 0,
    _budget: dict[str, int] | None = None,
    _base64_state: dict[str, int | bool] | None = None,
    _base64_depth: int = 0,
    _scan_base64: bool = True,
) -> tuple[set[str], int]:
    """Scan one file and bounded nested ZIP members without returning payload bytes."""
    findings: set[str] = set()
    budget = _budget or {"members": 0, "expanded": 0}
    base64_state = _base64_state or {"blocks": 0, "decoded": 0, "limited": False}
    starting_members = budget["members"]
    lowered_name = name.lower()
    suffix = PurePosixPath(lowered_name).suffix
    if contains_audio(data):
        findings.add("content.audio")
    if suffix in PRIVATE_MEDIA_SUFFIXES or data.startswith(PRIVATE_MEDIA_MAGIC):
        findings.add("content.binary_media")
    if PRIVATE_PATH.search(lowered_name) or TRANSCRIPT_TEXT.search(data):
        findings.add("content.transcript")
    if _contains_secret(data):
        findings.add("content.secret")
    if _contains_private_key(data):
        findings.add("content.private_key")
    if _looks_like_playwright(data, name):
        findings.add("content.raw_playwright")
    if _scan_base64:
        for payload in _base64_payloads(data):
            if base64_state["limited"]:
                break
            if _base64_depth >= MAX_BASE64_DEPTH:
                findings.add("content.base64_limit")
                base64_state["limited"] = True
                break
            if int(base64_state["blocks"]) >= MAX_BASE64_BLOCKS:
                findings.add("content.base64_limit")
                base64_state["limited"] = True
                break
            estimated_size = (len(payload) // 4) * 3 - payload.count(b"=")
            if int(base64_state["decoded"]) + estimated_size > MAX_BASE64_DECODED_BYTES:
                findings.add("content.base64_limit")
                base64_state["limited"] = True
                break
            decoded = _decode_base64_payload(payload)
            if decoded is None or not _is_plausible_decoded_payload(decoded):
                continue
            base64_state["blocks"] = int(base64_state["blocks"]) + 1
            base64_state["decoded"] = int(base64_state["decoded"]) + len(decoded)
            decoded_findings, _ = scan_evidence_payload(
                decoded,
                f"{name}.base64",
                archive_depth=archive_depth,
                _budget=budget,
                _base64_state=base64_state,
                _base64_depth=_base64_depth + 1,
            )
            findings.update(decoded_findings)
            if decoded_findings & {
                "content.raw_playwright", "archive.invalid", "archive.oversize",
                "archive.unsupported", "archive.encrypted", "archive.nested_limit",
            }:
                findings.add("content.embedded_playwright")
    has_zip_signature = data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x01\x02"))
    is_zip = zipfile.is_zipfile(io.BytesIO(data))
    if suffix == ".zip" and not is_zip:
        findings.add("archive.invalid")
        return findings, budget["members"] - starting_members
    if suffix in ARCHIVE_SUFFIXES and suffix != ".zip" and not is_zip:
        findings.add("archive.unsupported")
        return findings, budget["members"] - starting_members
    if has_zip_signature and not is_zip:
        findings.add("archive.invalid")
        return findings, budget["members"] - starting_members
    if not is_zip:
        return findings, budget["members"] - starting_members
    if archive_depth >= MAX_ARCHIVE_DEPTH:
        findings.add("archive.nested_limit")
        return findings, budget["members"] - starting_members
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS or budget["members"] + len(infos) > MAX_ARCHIVE_MEMBERS:
                findings.add("archive.oversize")
                return findings, budget["members"] - starting_members
            total = 0
            seen: set[str] = set()
            for info in infos:
                if info.is_dir():
                    continue
                budget["members"] += 1
                path = PurePosixPath(info.filename)
                if path.is_absolute() or ".." in path.parts or info.filename in seen:
                    findings.add("archive.invalid")
                    continue
                seen.add(info.filename)
                unix_mode = info.external_attr >> 16
                file_type = stat.S_IFMT(unix_mode)
                if file_type and not stat.S_ISREG(unix_mode):
                    findings.add("archive.invalid")
                    continue
                if info.flag_bits & 0x1:
                    findings.add("archive.encrypted")
                    continue
                if info.compress_type not in SUPPORTED_ZIP_COMPRESSION:
                    findings.add("archive.unsupported")
                    continue
                total += info.file_size
                budget["expanded"] += info.file_size
                if (
                    info.file_size > MAX_ARCHIVE_MEMBER_BYTES
                    or total > MAX_ARCHIVE_TOTAL_BYTES
                    or budget["expanded"] > MAX_ARCHIVE_TOTAL_BYTES
                ):
                    findings.add("archive.oversize")
                    continue
                try:
                    member_data = archive.read(info)
                except (OSError, RuntimeError, EOFError, zlib.error, zipfile.BadZipFile, NotImplementedError):
                    findings.add("archive.invalid")
                    continue
                child_findings, child_count = scan_evidence_payload(
                    member_data,
                    info.filename,
                    archive_depth=archive_depth + 1,
                    _budget=budget,
                    _base64_state=base64_state,
                    _base64_depth=_base64_depth,
                )
                findings.update(child_findings)
                del child_count
    except (OSError, zipfile.BadZipFile, ValueError):
        findings.add("archive.invalid")
    return findings, budget["members"] - starting_members
