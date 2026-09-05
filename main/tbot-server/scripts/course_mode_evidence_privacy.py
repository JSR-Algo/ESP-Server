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
from pathlib import PurePosixPath

AUDIO_MAGIC = (b"RIFF", b"ID3", b"OggS", b"fLaC", b"ADIF")
ISO_AUDIO_BRANDS = {b"M4A ", b"M4B ", b"M4P ", b"F4A ", b"F4B "}
ISO_AUDIO_SAMPLE_ENTRIES = (b"mp4a", b"enca", b"alac", b"ac-3", b"ec-3", b"Opus", b"fLaC")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SANITIZED_CONTAINER_KEYS = {"entries", "files", "schemaVersion"}
SANITIZED_ENTRY_KEYS = {"path", "sha256", "bytes", "classification", "action", "reason", "timestamp"}
MAX_ARCHIVE_MEMBERS = 4096
MAX_ARCHIVE_MEMBER_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 2
SUPPORTED_ZIP_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
ARCHIVE_SUFFIXES = {".zip", ".tar", ".tgz", ".gz", ".bz2", ".xz", ".7z", ".rar"}
SECRET_KEY = re.compile(r"(?i)(?:authorization|cookie|set-cookie|session|password|secret|token)")
SECRET_TEXT = re.compile(
    rb"(?i)(?:authorization|cookie|set-cookie|session(?:id)?|password|secret|token)"
    rb"\s*[:=]\s*[\"']?(?!\s*(?:null|none|redacted|<redacted>)(?:[\"']|\s|$))[^\s\"',;}]{3,}"
)
BEARER_VALUE = re.compile(rb"(?i)\bbearer\s+[a-z0-9._~+/=-]{4,}")
TRANSCRIPT_TEXT = re.compile(rb"(?i)\b(?:child[-_ ]?)?transcript\s*[:=]")
PRIVATE_PATH = re.compile(r"(?i)(?:^|[-_.\/])(?:audio|transcript|utterance|raw[-_]?speech)(?:[-_.\/]|$)")
BASE64_BLOCK = re.compile(rb"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{128,}={0,2}(?![A-Za-z0-9+/=])")
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
        document = json.loads(data)
    except (UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(document, dict) or not set(document) <= SANITIZED_CONTAINER_KEYS:
        return False
    if document.get("schemaVersion") != 1:
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
        if "bytes" in entry and (not isinstance(entry["bytes"], int) or entry["bytes"] < 0):
            return False
        if any(not isinstance(entry[key], str) for key in SANITIZED_ENTRY_KEYS - {"path", "sha256", "bytes"} if key in entry):
            return False
    return not (SECRET_TEXT.search(data) or BEARER_VALUE.search(data))


def _has_secret_json(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if SECRET_KEY.search(str(key)) and child not in (None, "", "redacted", "<redacted>", False):
                return True
            if _has_secret_json(child):
                return True
    elif isinstance(value, list):
        return any(_has_secret_json(child) for child in value)
    return False


def _contains_secret(data: bytes) -> bool:
    if SECRET_TEXT.search(data) or BEARER_VALUE.search(data):
        return True
    try:
        return _has_secret_json(json.loads(data))
    except (UnicodeError, json.JSONDecodeError):
        return False


def _contains_private_key(data: bytes) -> bool:
    for match in PEM_PRIVATE_KEY.finditer(data):
        block = match.group(0)
        if b"ENCRYPTED PRIVATE KEY" in block:
            return True
        try:
            from cryptography.hazmat.primitives import serialization

            serialization.load_pem_private_key(block, password=None)
            return True
        except TypeError:
            return True
        except (ImportError, ValueError):
            continue
    return False


def _decoded_base64_blocks(data: bytes):
    for match in BASE64_BLOCK.finditer(data):
        payload = match.group(0)
        try:
            yield base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            continue


def _looks_like_playwright(data: bytes, name: str) -> bool:
    lowered = data.lower()
    lowered_name = name.lower().replace("\\", "/")
    return any(marker in lowered for marker in PLAYWRIGHT_MARKERS) or any(
        segment in lowered_name
        for segment in ("playwright-report/", "test-results/", "blob-report/", "trace.zip")
    )


def _embedded_playwright(data: bytes) -> bool:
    if _looks_like_playwright(data, ""):
        return True
    if not data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                return True
            for info in infos:
                if _looks_like_playwright(b"", info.filename):
                    return True
                if info.file_size <= MAX_ARCHIVE_MEMBER_BYTES and info.compress_type in SUPPORTED_ZIP_COMPRESSION:
                    with archive.open(info) as member:
                        if _looks_like_playwright(member.read(min(info.file_size, 64 * 1024)), info.filename):
                            return True
    except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError):
        return True
    return False


def scan_evidence_payload(data: bytes, name: str, *, archive_depth: int = 0) -> tuple[set[str], int]:
    """Scan one file and bounded nested ZIP members without returning payload bytes."""
    findings: set[str] = set()
    members_checked = 0
    if is_sanitized_manifest(data):
        return findings, members_checked
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
    for decoded in _decoded_base64_blocks(data):
        if _embedded_playwright(decoded):
            findings.add("content.embedded_playwright")
            break
    is_zip = data.startswith(b"PK\x03\x04") or data.startswith(b"PK\x05\x06")
    if suffix in ARCHIVE_SUFFIXES and suffix != ".zip" and not is_zip:
        findings.add("archive.unsupported")
        return findings, members_checked
    if not is_zip:
        return findings, members_checked
    if archive_depth >= MAX_ARCHIVE_DEPTH:
        findings.add("archive.nested_limit")
        return findings, members_checked
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                findings.add("archive.oversize")
                return findings, members_checked
            total = 0
            seen: set[str] = set()
            for info in infos:
                if info.is_dir():
                    continue
                members_checked += 1
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
                if info.file_size > MAX_ARCHIVE_MEMBER_BYTES or total > MAX_ARCHIVE_TOTAL_BYTES:
                    findings.add("archive.oversize")
                    continue
                try:
                    member_data = archive.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError):
                    findings.add("archive.invalid")
                    continue
                child_findings, child_count = scan_evidence_payload(
                    member_data, info.filename, archive_depth=archive_depth + 1
                )
                findings.update(child_findings)
                members_checked += child_count
    except (OSError, zipfile.BadZipFile, ValueError):
        findings.add("archive.invalid")
    return findings, members_checked
