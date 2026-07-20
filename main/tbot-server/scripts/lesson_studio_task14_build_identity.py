#!/usr/bin/env python3
"""Validate and flatten immutable Task 6 firmware build manifests."""

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import suppress
from pathlib import Path, PurePosixPath

SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
PROFILES = ("hil", "production")
ARTIFACT_LABELS = frozenset(
    {"bin", "elf", "map", "mainArchive", "projectDescription", "sdkconfig", "partitionBinary"}
)
TOP_LEVEL_FIELDS = frozenset(
    {
        "status", "profile", "sourceCommit", "sourceCommitTimestamp", "target",
        "project", "buildDirectory", "configDefaults", "artifacts", "partition", "checks",
    }
)
FLAT_FIELDS = frozenset(
    {
        "sourceCommit", "profile", "configEnabled", "sdkconfigSha256",
        "binarySha256", "elfSha256", "mapSha256", "archiveSha256",
        "binaryBytes", "appPartitionFreeBytes",
    }
)
RELEASE_ORDER = (
    "hil-flash", "hil-matrix-pass", "production-reflash",
    "production-attest", "production-soak",
)


class BuildIdentityError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise BuildIdentityError(message)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_manifest_bytes(path):
    path = Path(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BuildIdentityError("manifest missing") from exc
    try:
        metadata = os.fstat(descriptor)
        require(stat.S_ISREG(metadata.st_mode), "manifest must be a regular file")
        require(metadata.st_size > 0, "manifest missing")
        chunks = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            require(bool(chunk), "manifest read truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        require(os.read(descriptor, 1) == b"", "manifest changed while reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _exact_int(value, name, *, minimum=0):
    require(type(value) is int and value >= minimum, f"invalid {name}")
    return value


def _safe_relative_path(root, value, label):
    require(isinstance(value, str) and value, f"invalid {label} path")
    pure = PurePosixPath(value)
    require(not pure.is_absolute() and ".." not in pure.parts, f"unsafe {label} path")
    candidate = root.joinpath(*pure.parts)
    cursor = root
    for part in pure.parts:
        cursor = cursor / part
        require(not cursor.is_symlink(), f"{label} path must not contain a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise BuildIdentityError(f"missing {label}") from exc
    resolved_root = root.resolve(strict=True)
    require(resolved == resolved_root or resolved_root in resolved.parents, f"{label} path escapes root")
    require(resolved.is_file() and resolved.stat().st_size > 0, f"invalid {label} artifact")
    return resolved


def _read_manifest(path):
    path = Path(path)
    require(not path.is_symlink(), "manifest must not be a symlink")
    require(path.name == "lesson-storage-hil-build.json", "unexpected manifest filename")
    manifest_bytes = _read_manifest_bytes(path)
    checksum = path.with_name("lesson-storage-hil-build.sha256")
    require(not checksum.is_symlink() and checksum.is_file(), "manifest checksum missing")
    parts = checksum.read_text(encoding="ascii").strip().split()
    require(
        len(parts) == 2
        and SHA256_RE.fullmatch(parts[0]) is not None
        and parts[1].lstrip("*") == path.name
        and parts[0] == hashlib.sha256(manifest_bytes).hexdigest(),
        "manifest checksum mismatch",
    )
    try:
        data = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildIdentityError("invalid manifest JSON") from exc
    require(isinstance(data, dict) and set(data) == TOP_LEVEL_FIELDS, "invalid Task 6 manifest fields")
    return path.parent.resolve(strict=True) / path.name, data


def _validate_artifacts(build_dir, manifest):
    artifacts = manifest.get("artifacts")
    require(isinstance(artifacts, dict) and set(artifacts) == ARTIFACT_LABELS, "invalid artifact set")
    resolved = {}
    for label in sorted(ARTIFACT_LABELS):
        record = artifacts[label]
        require(isinstance(record, dict) and set(record) == {"path", "bytes", "sha256"}, f"invalid {label} record")
        path = _safe_relative_path(build_dir, record.get("path"), label)
        size = _exact_int(record.get("bytes"), f"{label} bytes", minimum=1)
        digest = record.get("sha256")
        require(isinstance(digest, str) and SHA256_RE.fullmatch(digest), f"invalid {label} sha256")
        require(path.stat().st_size == size, f"{label} byte count mismatch")
        require(sha256_file(path) == digest, f"{label} hash mismatch")
        resolved[label] = path
    return resolved


def _firmware_root(build_dir, project_description):
    try:
        description = json.loads(project_description.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildIdentityError("invalid project description") from exc
    require(isinstance(description, dict), "invalid project description")
    root_value = description.get("project_path")
    build_value = description.get("build_dir")
    require(isinstance(root_value, str) and isinstance(build_value, str), "project identity missing")
    root = Path(root_value)
    require(root.is_absolute() and not root.is_symlink(), "invalid firmware project path")
    root = root.resolve(strict=True)
    require(root.is_dir() and root == build_dir.parent.resolve(strict=True), "firmware project path mismatch")
    require(Path(build_value).resolve(strict=True) == build_dir.resolve(strict=True), "build directory identity mismatch")
    return root


def _validate_defaults(root, records, profile):
    require(isinstance(records, list) and records, "config defaults missing")
    names = []
    for record in records:
        require(isinstance(record, dict) and set(record) == {"path", "sha256"}, "invalid config default record")
        relative = record.get("path")
        path = _safe_relative_path(root, relative, "config default")
        digest = record.get("sha256")
        require(isinstance(digest, str) and SHA256_RE.fullmatch(digest), "invalid config default sha256")
        require(sha256_file(path) == digest, "config default hash mismatch")
        names.append(path.name)
    has_hil = "sdkconfig.defaults.hil-storage" in names
    require(has_hil == (profile == "hil"), "config default profile mismatch")


def load_build_identity(manifest_path, *, expected_profile=None):
    manifest_path, manifest = _read_manifest(manifest_path)
    build_dir = manifest_path.parent
    require(not build_dir.is_symlink(), "build directory must not be a symlink")
    profile = manifest.get("profile")
    require(profile in PROFILES, "invalid build profile")
    if expected_profile is not None:
        require(expected_profile in PROFILES and profile == expected_profile, "foreign build profile")
    require(manifest.get("status") == "PASS", "Task 6 manifest is not PASS")
    source_commit = manifest.get("sourceCommit")
    require(isinstance(source_commit, str) and COMMIT_RE.fullmatch(source_commit), "invalid source commit")
    _exact_int(manifest.get("sourceCommitTimestamp"), "source commit timestamp", minimum=1)
    require(manifest.get("target") == "esp32s3" and manifest.get("project") == "xiaozhi", "foreign firmware target")
    require(manifest.get("buildDirectory") == build_dir.name, "build directory name mismatch")
    artifacts = _validate_artifacts(build_dir, manifest)
    root = _firmware_root(build_dir, artifacts["projectDescription"])
    _validate_defaults(root, manifest.get("configDefaults"), profile)
    checks = manifest.get("checks")
    require(
        isinstance(checks, dict)
        and set(checks) == {"hilConfigEnabled", "toolLiterals", "hilSymbols", "bannedApis"}
        and type(checks.get("hilConfigEnabled")) is bool,
        "invalid build checks",
    )
    enabled = checks["hilConfigEnabled"]
    require(enabled == (profile == "hil"), "HIL config/profile mismatch")
    require(checks.get("toolLiterals") == ("present" if enabled else "absent"), "tool literal profile mismatch")
    require(checks.get("hilSymbols") == ("present" if enabled else "absent"), "symbol profile mismatch")
    require(checks.get("bannedApis") == "absent", "banned API audit failed")
    partition = manifest.get("partition")
    require(
        isinstance(partition, dict)
        and set(partition) == {"partitionTable", "partitionBytes", "imageBytes", "freeBytes", "freePercent"},
        "invalid partition record",
    )
    image_bytes = _exact_int(partition.get("imageBytes"), "partition image bytes", minimum=1)
    free_bytes = _exact_int(partition.get("freeBytes"), "partition free bytes", minimum=1)
    partition_bytes = _exact_int(partition.get("partitionBytes"), "partition bytes", minimum=1)
    require(image_bytes == artifacts["bin"].stat().st_size, "partition image size mismatch")
    require(image_bytes + free_bytes == partition_bytes, "partition arithmetic mismatch")
    require(type(partition.get("freePercent")) in (int, float), "invalid partition free percent")
    binding = {
        "sourceCommit": source_commit,
        "profile": profile,
        "configEnabled": enabled,
        "sdkconfigSha256": sha256_file(artifacts["sdkconfig"]),
        "binarySha256": sha256_file(artifacts["bin"]),
        "elfSha256": sha256_file(artifacts["elf"]),
        "mapSha256": sha256_file(artifacts["map"]),
        "archiveSha256": sha256_file(artifacts["mainArchive"]),
        "binaryBytes": artifacts["bin"].stat().st_size,
        "appPartitionFreeBytes": free_bytes,
    }
    require(set(binding) == FLAT_FIELDS, "internal build binding error")
    return binding


def validate_build_pair(hil_manifest, production_manifest):
    hil = load_build_identity(hil_manifest, expected_profile="hil")
    production = load_build_identity(production_manifest, expected_profile="production")
    require(hil["sourceCommit"] == production["sourceCommit"], "paired builds use different source commits")
    require(hil["binarySha256"] != production["binarySha256"], "HIL and production binaries must differ")
    return {"sourceCommit": hil["sourceCommit"], "hil": hil, "production": production}


def validate_release_order(events):
    require(isinstance(events, list) and all(isinstance(item, str) for item in events), "invalid release events")
    positions = []
    for event in RELEASE_ORDER:
        require(events.count(event) == 1, f"release event missing or duplicated: {event}")
        positions.append(events.index(event))
    require(positions == sorted(positions), "HIL-to-production release order invalid")
    return events


def atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--manifest", required=True, type=Path)
    validate.add_argument("--profile", required=True, choices=PROFILES)
    validate.add_argument("--output", type=Path)
    pair = subparsers.add_parser("pair")
    pair.add_argument("--hil-manifest", required=True, type=Path)
    pair.add_argument("--production-manifest", required=True, type=Path)
    pair.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.command == "validate":
            result = load_build_identity(arguments.manifest, expected_profile=arguments.profile)
        else:
            result = validate_build_pair(arguments.hil_manifest, arguments.production_manifest)
        if arguments.output:
            atomic_write_json(arguments.output, result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (BuildIdentityError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"lesson storage build identity: FAIL: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
