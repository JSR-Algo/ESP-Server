from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/course_mode_evidence_sanitize.py"
RECORD_DIR_NAME = "SANITIZATION-RECORDS"
OUTPUT_NAMES = ("SANITIZATION-MANIFEST.json", "SANITIZED-SUMMARY.json")


def _record(root: Path, name: str) -> Path:
    return root / RECORD_DIR_NAME / name


def _assert_no_records(root: Path) -> None:
    assert not (root / RECORD_DIR_NAME).exists()
    for name in OUTPUT_NAMES:
        assert not (root / name).exists()


def _write_spec(path: Path, entries: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps({"schemaVersion": 1, "entries": entries}, sort_keys=True),
        encoding="utf-8",
    )


def _run(root: Path, spec: Path, *, apply: bool = False) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        "--artifact-root",
        str(root),
        "--remediation-spec",
        str(spec),
    ]
    if apply:
        command.append("--apply")
    return subprocess.run(command, check=False, capture_output=True, text=True)


def _entry(
    path: str,
    action: str,
    *,
    classification: str = "private-content",
    reason: str = "private content removed",
    **extra: object,
) -> dict[str, object]:
    return {
        "action": action,
        "classification": classification,
        "path": path,
        "reason": reason,
        **extra,
    }


def _load_module():
    spec = importlib.util.spec_from_file_location("course_mode_evidence_sanitize", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_privacy_module():
    path = ROOT / "scripts/course_mode_evidence_privacy.py"
    spec = importlib.util.spec_from_file_location("course_mode_evidence_privacy", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_dry_run_is_default_and_never_echoes_match_values(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    target = artifact_root / "report.txt"
    leaked = "do-not-echo-this-bearer-value"
    target.write_text(f"Authorization: Bearer {leaked}\n", encoding="utf-8")
    spec = tmp_path / "spec.json"
    _write_spec(spec, [_entry("report.txt", "replace-text", matches=[leaked])])

    result = _run(artifact_root, spec)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "actionCount": 1,
        "apply": False,
        "fileCount": 1,
        "status": "dry-run",
    }
    assert leaked not in result.stdout + result.stderr
    assert target.read_text(encoding="utf-8") == f"Authorization: Bearer {leaked}\n"
    _assert_no_records(artifact_root)


def test_apply_performs_all_actions_and_writes_auditor_compatible_manifests(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    text = artifact_root / "request.log"
    text_before = b"Authorization: Bearer live-secret\nkeep=yes\n"
    text.write_bytes(text_before)
    yaml = artifact_root / "session.yaml"
    yaml_before = b"auth:\n  token: live-yaml-secret\nkeep: yes\n"
    yaml.write_bytes(yaml_before)
    deleted = artifact_root / "raw-audio.bin"
    deleted_before = b"RIFF-private-audio"
    deleted.write_bytes(deleted_before)
    tree = artifact_root / "playwright-report"
    nested = tree / "data" / "trace.txt"
    nested.parent.mkdir(parents=True)
    nested_before = b"cookie=private-cookie\n"
    nested.write_bytes(nested_before)
    empty = tree / "empty"
    empty.mkdir()
    spec = tmp_path / "spec.json"
    _write_spec(
        spec,
        [
            _entry("request.log", "replace-text", matches=["live-secret"]),
            _entry("session.yaml", "redact-yaml", pointers=["/auth/token"]),
            _entry("raw-audio.bin", "delete-file", classification="raw-capture", reason="raw capture excluded"),
            _entry(
                "playwright-report",
                "delete-tree",
                classification="unsupported-artifact",
                reason="unsupported artifact",
            ),
        ],
    )

    result = _run(artifact_root, spec, apply=True)

    assert result.returncode == 0, result.stderr
    assert "live-secret" not in result.stdout + result.stderr
    assert "live-yaml-secret" not in result.stdout + result.stderr
    assert text.read_text(encoding="utf-8") == "Authorization: Bearer <redacted>\nkeep=yes\n"
    assert "live-yaml-secret" not in yaml.read_text(encoding="utf-8")
    assert "<redacted>" in yaml.read_text(encoding="utf-8")
    assert not deleted.exists()
    assert not tree.exists()

    expected = [text_before, yaml_before, deleted_before, nested_before]
    assert not any((artifact_root / name).exists() for name in OUTPUT_NAMES)
    assert stat.S_IMODE((artifact_root / RECORD_DIR_NAME).stat().st_mode) == 0o700
    for output_name in OUTPUT_NAMES:
        output = _record(artifact_root, output_name)
        assert stat.S_IMODE(output.stat().st_mode) == 0o444
        document = json.loads(output.read_text(encoding="utf-8"))
        assert document["schemaVersion"] == 1
        assert [entry["path"] for entry in document["entries"]] == [
            "artifact-0001",
            "artifact-0002",
            "artifact-0003",
            "artifact-0004",
        ]
        expected_hashes = {(hashlib.sha256(before).hexdigest(), len(before)) for before in expected}
        assert {(entry["sha256"], entry["bytes"]) for entry in document["entries"]} == expected_hashes
        for entry in document["entries"]:
            assert set(entry) == {
                "action",
                "bytes",
                "classification",
                "path",
                "reason",
                "sha256",
                "timestamp",
            }
        serialized = output.read_text(encoding="utf-8")
        assert "live-secret" not in serialized
        assert "live-yaml-secret" not in serialized
        assert hashlib.sha256(b"live-secret").hexdigest() not in serialized
        assert _load_privacy_module().is_sanitized_manifest(output.read_bytes())


@pytest.mark.parametrize(
    ("target_factory", "path", "action", "error_fragment"),
    [
        (lambda root: (root / "sub").mkdir(), "sub", "delete-file", "regular file"),
        (lambda root: (root / "link").symlink_to(root / "outside"), "link", "delete-file", "symbolic link"),
        (
            lambda root: ((root / "source").write_text("x", encoding="utf-8"), os.link(root / "source", root / "hard")),
            "hard",
            "delete-file",
            "hard link",
        ),
    ],
)
def test_preflight_rejects_unsafe_targets_without_mutating_earlier_entries(
    tmp_path: Path,
    target_factory,
    path: str,
    action: str,
    error_fragment: str,
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    first = artifact_root / "first.txt"
    first.write_text("remove-me", encoding="utf-8")
    target_factory(artifact_root)
    spec = tmp_path / "spec.json"
    _write_spec(spec, [_entry("first.txt", "delete-file"), _entry(path, action)])

    result = _run(artifact_root, spec, apply=True)

    assert result.returncode == 2
    assert error_fragment in result.stderr
    assert first.read_text(encoding="utf-8") == "remove-me"
    _assert_no_records(artifact_root)


@pytest.mark.parametrize("unsafe_path", ["../outside", "/absolute", "a/../../outside", "."])
def test_rejects_paths_outside_artifact_root(tmp_path: Path, unsafe_path: str) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    spec = tmp_path / "spec.json"
    _write_spec(spec, [_entry(unsafe_path, "delete-file")])

    result = _run(artifact_root, spec, apply=True)

    assert result.returncode == 2
    assert "safe relative path" in result.stderr


def test_nul_in_relative_path_is_a_deterministic_preflight_rejection(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    spec = tmp_path / "spec.json"
    _write_spec(spec, [_entry("safe\x00suffix.txt", "delete-file")])

    result = _run(artifact_root, spec, apply=True)

    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "safe relative path" in result.stderr
    _assert_no_records(artifact_root)


def test_rejects_symlinked_or_insecure_parent_before_any_mutation(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    good = artifact_root / "good.txt"
    good.write_text("keep", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (artifact_root / "linked").symlink_to(outside, target_is_directory=True)
    insecure = artifact_root / "insecure"
    insecure.mkdir(mode=0o777)
    insecure.chmod(0o777)
    (insecure / "secret.txt").write_text("secret", encoding="utf-8")

    for path, fragment in (("linked/secret.txt", "symbolic link"), ("insecure/secret.txt", "insecure parent")):
        spec = tmp_path / f"{path.split('/')[0]}.json"
        _write_spec(spec, [_entry("good.txt", "delete-file"), _entry(path, "delete-file")])
        result = _run(artifact_root, spec, apply=True)
        assert result.returncode == 2
        assert fragment in result.stderr
        assert good.exists()


def test_rejects_non_owner_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    metadata = list((tmp_path / "owned.txt").parent.stat())
    metadata[stat.ST_MODE] = stat.S_IFREG | 0o600
    metadata[stat.ST_NLINK] = 1
    metadata[stat.ST_UID] = os.geteuid() + 1

    with pytest.raises(module.SanitizationError, match="ownership"):
        module._validate_file(os.stat_result(metadata))


def test_rejects_invalid_enums_and_output_collisions(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    target = artifact_root / "target.txt"
    target.write_text("secret", encoding="utf-8")
    cases = [
        _entry("target.txt", "shell-command"),
        _entry("target.txt", "delete-file", reason="arbitrary reason"),
        _entry("target.txt", "delete-file", classification="arbitrary"),
        _entry("SANITIZATION-MANIFEST.json", "delete-file"),
        _entry("SANITIZATION-RECORDS/nested.json", "delete-file"),
    ]
    for index, entry in enumerate(cases):
        spec = tmp_path / f"bad-{index}.json"
        _write_spec(spec, [entry])
        result = _run(artifact_root, spec, apply=True)
        assert result.returncode == 2
        assert target.exists()


def test_failed_preflight_does_not_apply_valid_replacement(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    target = artifact_root / "target.txt"
    target.write_text("secret-one", encoding="utf-8")
    spec = tmp_path / "spec.json"
    _write_spec(
        spec,
        [
            _entry("target.txt", "replace-text", matches=["secret-one"]),
            _entry("missing.txt", "delete-file"),
        ],
    )

    result = _run(artifact_root, spec, apply=True)

    assert result.returncode == 2
    assert target.read_text(encoding="utf-8") == "secret-one"


def test_identity_change_at_commit_is_not_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    target = artifact_root / "target.txt"
    target.write_text("original", encoding="utf-8")
    spec = tmp_path / "spec.json"
    _write_spec(spec, [_entry("target.txt", "delete-file")])
    real_rename = module.os.rename
    raced = False

    def racing_rename(src, dst, *args, **kwargs):
        nonlocal raced
        if not raced and src == "target.txt":
            raced = True
            target.unlink()
            target.write_text("replacement", encoding="utf-8")
        return real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(module.os, "rename", racing_rename)
    result = module.run(["--artifact-root", str(artifact_root), "--remediation-spec", str(spec), "--apply"])

    assert result == 3
    assert target.read_text(encoding="utf-8") == "replacement"
    _assert_no_records(artifact_root)


def test_partial_commit_error_rolls_back_all_artifact_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    first = artifact_root / "first.txt"
    second = artifact_root / "second.txt"
    first.write_text("first-secret", encoding="utf-8")
    second.write_text("second-secret", encoding="utf-8")
    spec = tmp_path / "spec.json"
    _write_spec(
        spec,
        [
            _entry("first.txt", "replace-text", matches=["first-secret"]),
            _entry("second.txt", "replace-text", matches=["second-secret"]),
        ],
    )
    real_link = module.os.link
    staged_replacements = 0

    def fail_second_staged_replacement(src, dst, *args, **kwargs):
        nonlocal staged_replacements
        if isinstance(src, str) and src.startswith("replacement-"):
            staged_replacements += 1
            if staged_replacements == 2:
                raise OSError("injected commit failure")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(module.os, "link", fail_second_staged_replacement)
    result = module.run(["--artifact-root", str(artifact_root), "--remediation-spec", str(spec), "--apply"])

    assert result == 3
    assert first.read_text(encoding="utf-8") == "first-secret"
    assert second.read_text(encoding="utf-8") == "second-secret"
    _assert_no_records(artifact_root)


def test_delete_tree_rechecks_every_member_identity_after_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    tree = artifact_root / "raw"
    tree.mkdir()
    target = tree / "secret.txt"
    target.write_text("same-bytes", encoding="utf-8")
    tree_times = (tree.stat().st_atime_ns, tree.stat().st_mtime_ns)
    target_times = (target.stat().st_atime_ns, target.stat().st_mtime_ns)
    spec = tmp_path / "spec.json"
    _write_spec(spec, [_entry("raw", "delete-tree")])
    real_rename = module.os.rename
    raced = False

    def replace_member_after_quarantine(src, dst, *args, **kwargs):
        nonlocal raced
        result = real_rename(src, dst, *args, **kwargs)
        if not raced and src == "raw" and dst == "backup-0":
            raced = True
            backup_fd = os.open("backup-0", os.O_RDONLY | os.O_DIRECTORY, dir_fd=kwargs["dst_dir_fd"])
            try:
                os.unlink("secret.txt", dir_fd=backup_fd)
                descriptor = os.open("secret.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=backup_fd)
                os.write(descriptor, b"same-bytes")
                os.close(descriptor)
                os.utime("secret.txt", ns=target_times, dir_fd=backup_fd, follow_symlinks=False)
            finally:
                os.close(backup_fd)
            os.utime("backup-0", ns=tree_times, dir_fd=kwargs["dst_dir_fd"], follow_symlinks=False)
        return result

    monkeypatch.setattr(module.os, "rename", replace_member_after_quarantine)
    result = module.run(["--artifact-root", str(artifact_root), "--remediation-spec", str(spec), "--apply"])

    assert result == 3
    assert target.read_text(encoding="utf-8") == "same-bytes"
    _assert_no_records(artifact_root)


def test_cleanup_failure_is_fail_closed_without_success_manifests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    target = artifact_root / "target.txt"
    target.write_text("private-value", encoding="utf-8")
    spec = tmp_path / "spec.json"
    _write_spec(spec, [_entry("target.txt", "replace-text", matches=["private-value"])])

    def fail_cleanup(*_args, **_kwargs):
        raise OSError("injected cleanup failure")

    monkeypatch.setattr(module, "_remove_at", fail_cleanup)
    result = module.run(["--artifact-root", str(artifact_root), "--remediation-spec", str(spec), "--apply"])

    assert result == 4
    assert target.read_text(encoding="utf-8") == "<redacted>"
    _assert_no_records(artifact_root)
    quarantines = list(artifact_root.glob(".course-mode-sanitize-*"))
    assert len(quarantines) == 1
    assert (quarantines[0] / "backup-0").read_text(encoding="utf-8") == "private-value"


def test_staging_failure_leaves_targets_and_artifact_root_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    target = artifact_root / "target.txt"
    target.write_text("private-value", encoding="utf-8")
    spec = tmp_path / "spec.json"
    _write_spec(spec, [_entry("target.txt", "replace-text", matches=["private-value"])])
    real_write_staged = module._write_staged
    calls = 0

    def fail_second_stage(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected staging failure")
        return real_write_staged(*args, **kwargs)

    monkeypatch.setattr(module, "_write_staged", fail_second_stage)
    result = module.run(["--artifact-root", str(artifact_root), "--remediation-spec", str(spec), "--apply"])

    assert result == 3
    assert target.read_text(encoding="utf-8") == "private-value"
    assert list(artifact_root.iterdir()) == [target]


def test_empty_transaction_cleanup_failure_cannot_split_success_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    target = artifact_root / "target.txt"
    target.write_text("private-value", encoding="utf-8")
    spec = tmp_path / "spec.json"
    _write_spec(spec, [_entry("target.txt", "replace-text", matches=["private-value"])])
    real_rmdir = module.os.rmdir

    def fail_transaction_removal(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith(".course-mode-sanitize-"):
            raise OSError("injected final cleanup failure")
        return real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(module.os, "rmdir", fail_transaction_removal)
    result = module.run(["--artifact-root", str(artifact_root), "--remediation-spec", str(spec), "--apply"])

    assert result == 0
    assert target.read_text(encoding="utf-8") == "<redacted>"
    for name in OUTPUT_NAMES:
        output = _record(artifact_root, name)
        assert output.exists()
        document = json.loads(output.read_text(encoding="utf-8"))
        assert [entry["action"] for entry in document["entries"]] == ["replace-text"]
        assert _load_privacy_module().is_sanitized_manifest(output.read_bytes())


@pytest.mark.parametrize("field", ["action", "classification", "reason"])
def test_unhashable_enum_fields_fail_with_sanitized_validation_error(tmp_path: Path, field: str) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    target = artifact_root / "target.txt"
    target.write_text("private", encoding="utf-8")
    entry = _entry("target.txt", "delete-file")
    entry[field] = {"unexpected": "value"}
    spec = tmp_path / "spec.json"
    _write_spec(spec, [entry])

    result = _run(artifact_root, spec, apply=True)

    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "unexpected" not in result.stderr
    assert target.read_text(encoding="utf-8") == "private"


def test_fifo_is_rejected_by_lstat_without_opening_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    fifo = artifact_root / "capture.pipe"
    os.mkfifo(fifo, 0o600)
    spec = tmp_path / "spec.json"
    _write_spec(spec, [_entry("capture.pipe", "delete-file")])
    real_open = module.os.open

    def forbid_fifo_open(path, *args, **kwargs):
        if path == "capture.pipe":
            raise AssertionError("FIFO was opened before lstat rejection")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", forbid_fifo_open)

    result = module.run(["--artifact-root", str(artifact_root), "--remediation-spec", str(spec), "--apply"])

    assert result == 2
    assert stat.S_ISFIFO(fifo.lstat().st_mode)


def test_fifo_remediation_spec_is_rejected_without_opening_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    fifo = tmp_path / "spec.pipe"
    os.mkfifo(fifo, 0o600)
    real_open = module.os.open

    def forbid_fifo_open(path, *args, **kwargs):
        if Path(path) == fifo:
            raise AssertionError("FIFO remediation spec was opened")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", forbid_fifo_open)

    with pytest.raises(module.SanitizationError, match="regular file"):
        module._safe_json(fifo)


def test_secret_bearing_filenames_are_never_persisted_or_echoed(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    marker = "do-not-persist-path-secret"
    target = artifact_root / f"token={marker}.txt"
    target.write_text("private payload", encoding="utf-8")
    spec = tmp_path / "spec.json"
    _write_spec(spec, [_entry(target.name, "delete-file")])

    result = _run(artifact_root, spec, apply=True)

    assert result.returncode == 0, result.stderr
    outputs = [_record(artifact_root, name) for name in OUTPUT_NAMES]
    serialized = result.stdout + result.stderr + "".join(path.read_text(encoding="utf-8") for path in outputs)
    assert marker not in serialized
    assert "token=" not in serialized
    for output in outputs:
        document = json.loads(output.read_text(encoding="utf-8"))
        assert document["entries"][0]["path"] == "artifact-0001"
        assert _load_privacy_module().is_sanitized_manifest(output.read_bytes())


def test_staged_replacement_swap_after_link_is_detected_and_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    target = artifact_root / "target.txt"
    target.write_text("original-secret", encoding="utf-8")
    spec = tmp_path / "spec.json"
    _write_spec(spec, [_entry("target.txt", "replace-text", matches=["original-secret"])])
    real_link = module.os.link
    real_unlink = module.os.unlink
    swapped = False

    def swap_link_source(src, dst, *args, **kwargs):
        nonlocal swapped
        if not swapped and src == "replacement-0":
            swapped = True
            transaction_fd = kwargs["src_dir_fd"]
            real_unlink(src, dir_fd=transaction_fd)
            descriptor = os.open(src, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400, dir_fd=transaction_fd)
            os.write(descriptor, b"original-secret")
            os.close(descriptor)
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(module.os, "link", swap_link_source)
    result = module.run(["--artifact-root", str(artifact_root), "--remediation-spec", str(spec), "--apply"])

    assert result == 3
    assert target.read_text(encoding="utf-8") == "original-secret"
    _assert_no_records(artifact_root)


def test_staged_output_swap_is_rejected_without_success_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    target = artifact_root / "target.txt"
    target.write_text("private-value", encoding="utf-8")
    spec = tmp_path / "spec.json"
    _write_spec(spec, [_entry("target.txt", "replace-text", matches=["private-value"])])
    real_rename = module.os.rename
    swapped = False

    def swap_output_source(src, dst, *args, **kwargs):
        nonlocal swapped
        if not swapped and src == "records-pending":
            swapped = True
            transaction_fd = kwargs["src_dir_fd"]
            records_fd = os.open(src, os.O_RDONLY | os.O_DIRECTORY, dir_fd=transaction_fd)
            try:
                os.unlink("SANITIZATION-MANIFEST.json", dir_fd=records_fd)
                descriptor = os.open(
                    "SANITIZATION-MANIFEST.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444, dir_fd=records_fd
                )
                os.write(descriptor, b'{"schemaVersion":1,"entries":[]}\n')
                os.close(descriptor)
            finally:
                os.close(records_fd)
        return real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(module.os, "rename", swap_output_source)
    result = module.run(["--artifact-root", str(artifact_root), "--remediation-spec", str(spec), "--apply"])

    assert result == 4
    assert target.read_text(encoding="utf-8") == "<redacted>"
    _assert_no_records(artifact_root)


def test_record_directory_rename_failure_publishes_neither_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    target = artifact_root / "target.txt"
    target.write_text("private-value", encoding="utf-8")
    spec = tmp_path / "spec.json"
    _write_spec(spec, [_entry("target.txt", "replace-text", matches=["private-value"])])
    real_rename = module.os.rename

    def fail_record_publish(src, dst, *args, **kwargs):
        if src == "records-pending" and dst == RECORD_DIR_NAME:
            raise OSError("injected record publication failure")
        return real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(module.os, "rename", fail_record_publish)
    result = module.run(["--artifact-root", str(artifact_root), "--remediation-spec", str(spec), "--apply"])

    assert result == 4
    assert target.read_text(encoding="utf-8") == "<redacted>"
    _assert_no_records(artifact_root)


def test_backup_swap_before_unlink_is_detected_and_never_reports_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    target = artifact_root / "target.txt"
    target.write_text("private-value", encoding="utf-8")
    spec = tmp_path / "spec.json"
    _write_spec(spec, [_entry("target.txt", "replace-text", matches=["private-value"])])
    real_unlink = module.os.unlink
    real_rename = module.os.rename
    swapped = False
    escaped = tmp_path / "escaped-original"

    def swap_backup(path, *args, **kwargs):
        nonlocal swapped
        if not swapped and path == "backup-0":
            swapped = True
            transaction_fd = kwargs["dir_fd"]
            real_rename("backup-0", escaped, src_dir_fd=transaction_fd)
            descriptor = os.open("backup-0", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400, dir_fd=transaction_fd)
            os.write(descriptor, b"replacement")
            os.close(descriptor)
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(module.os, "unlink", swap_backup)
    result = module.run(["--artifact-root", str(artifact_root), "--remediation-spec", str(spec), "--apply"])

    assert result == 4
    assert target.read_text(encoding="utf-8") == "<redacted>"
    _assert_no_records(artifact_root)
    assert escaped.read_text(encoding="utf-8") == "private-value"


def test_tree_member_swap_before_unlink_is_detected_and_never_reports_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    tree = artifact_root / "raw"
    tree.mkdir()
    target = tree / "secret.txt"
    target.write_text("private-value", encoding="utf-8")
    spec = tmp_path / "spec.json"
    _write_spec(spec, [_entry("raw", "delete-tree")])
    real_unlink = module.os.unlink
    real_rename = module.os.rename
    swapped = False
    escaped = tmp_path / "escaped-tree-member"

    def swap_tree_member(path, *args, **kwargs):
        nonlocal swapped
        if not swapped and path == "secret.txt" and "dir_fd" in kwargs:
            swapped = True
            directory_fd = kwargs["dir_fd"]
            real_rename("secret.txt", escaped, src_dir_fd=directory_fd)
            descriptor = os.open("secret.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory_fd)
            os.write(descriptor, b"replacement")
            os.close(descriptor)
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(module.os, "unlink", swap_tree_member)
    result = module.run(["--artifact-root", str(artifact_root), "--remediation-spec", str(spec), "--apply"])

    assert result == 4
    assert not tree.exists()
    _assert_no_records(artifact_root)
    assert escaped.read_text(encoding="utf-8") == "private-value"
