from __future__ import annotations

import io
import json
import subprocess
import tarfile
from pathlib import Path
from typing import Any, cast

import pytest

import scripts.generate_tvideo_farm_manifest_fixture as fixture_generator
from scripts.generate_tvideo_farm_manifest_fixture import (
    FixtureGenerationError,
    _extract_archive,
    _verify_backend,
    generate_fixture,
)


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _backend_repo(tmp_path: Path) -> tuple[Path, str]:
    backend = tmp_path / "backend"
    source = backend / "src" / "lessons" / "tvideo-journey" / "fixtures" / "farm-golden.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const FARM_TVIDEO_JOURNEY_V1 = { version: 1 };\n", encoding="utf-8")
    (backend / "package.json").write_text('{"name":"fixture-backend"}\n', encoding="utf-8")
    (backend / "package-lock.json").write_text('{"lockfileVersion":3}\n', encoding="utf-8")
    (backend / "tsconfig.json").write_text('{"compilerOptions":{}}\n', encoding="utf-8")
    (backend / "tsconfig.build.json").write_text(
        '{"extends":"./tsconfig.json","include":["src/**/*"]}\n', encoding="utf-8"
    )
    typescript = backend / "node_modules" / "typescript" / "lib" / "typescript.js"
    typescript.parent.mkdir(parents=True)
    typescript.write_text("// compiler fixture\n", encoding="utf-8")
    (typescript.parent / "tsc.js").write_text("// compiler CLI fixture\n", encoding="utf-8")
    _git(backend, "init", "-q")
    _git(backend, "config", "user.email", "fixture@example.test")
    _git(backend, "config", "user.name", "Fixture Test")
    _git(backend, "add", "package.json", "package-lock.json", "tsconfig.json", "tsconfig.build.json", "src")
    _git(backend, "commit", "-qm", "fixture backend")
    return backend, _git(backend, "rev-parse", "HEAD")


def _recording_runner(calls: list[list[str]]):
    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "--project" in command:
            build_root = Path(command[command.index("--outDir") + 1])
            source_root = Path(kwargs["cwd"])
            assert not (source_root / "src" / "ignored-sentinel.ts").exists()
            assert not (source_root / "dist").exists()
            compiled = build_root / "lessons" / "tvideo-journey" / "fixtures" / "farm-golden.js"
            compiled.parent.mkdir(parents=True)
            compiled.write_text(
                (source_root / "src" / "lessons" / "tvideo-journey" / "fixtures" / "farm-golden.ts").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
        elif "-e" in command:
            Path(command[-1]).write_text("// compiled generator fixture\n", encoding="utf-8")
        else:
            build_root = Path(command[command.index("--backend-build-root") + 1])
            output = Path(command[command.index("--output") + 1])
            compiled = build_root / "lessons" / "tvideo-journey" / "fixtures" / "farm-golden.js"
            output.write_text(json.dumps({"compiledSource": compiled.read_text(encoding="utf-8")}) + "\n")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"checksum":"fixture-checksum","cueCount":19}\n' if "--backend-build-root" in command else "",
            stderr="",
        )

    return run


def test_generator_uses_clean_temporary_build_and_ignores_backend_dist(tmp_path: Path) -> None:
    backend, head = _backend_repo(tmp_path)
    stale = backend / "dist" / "lessons" / "tvideo-journey" / "fixtures" / "farm-golden.js"
    stale.parent.mkdir(parents=True)
    stale.write_text("STALE_DIST_SENTINEL\n", encoding="utf-8")
    ignored = backend / "src" / "ignored-sentinel.ts"
    ignored.write_text("export const IGNORED_SOURCE_SENTINEL = true;\n", encoding="utf-8")
    (backend / ".git" / "info" / "exclude").write_text("src/ignored-sentinel.ts\n", encoding="utf-8")
    output = tmp_path / "manifest.json"
    provenance = tmp_path / "manifest.provenance.json"
    calls: list[list[str]] = []

    result = generate_fixture(
        backend_root=backend,
        output=output,
        provenance_output=provenance,
        expected_backend_head=head,
        node=Path("node"),
        run=_recording_runner(calls),
    )

    assert "STALE_DIST_SENTINEL" not in output.read_text(encoding="utf-8")
    compile_root = Path(calls[0][calls[0].index("--outDir") + 1])
    load_root = Path(calls[-1][calls[-1].index("--backend-build-root") + 1])
    assert compile_root == load_root
    assert compile_root != backend / "dist"
    assert not compile_root.exists()
    assert result == json.loads(provenance.read_text(encoding="utf-8"))
    assert result["backend"]["head"] == head
    assert result["manifest"]["manifestChecksum"] == "fixture-checksum"
    assert result["backend"]["relevantSources"]["src/lessons/tvideo-journey/fixtures/farm-golden.ts"]


def test_generator_rejects_wrong_backend_head_before_compiling(tmp_path: Path) -> None:
    backend, _head = _backend_repo(tmp_path)

    with pytest.raises(FixtureGenerationError, match="backend HEAD mismatch"):
        generate_fixture(
            backend_root=backend,
            output=tmp_path / "manifest.json",
            provenance_output=tmp_path / "manifest.provenance.json",
            expected_backend_head="0" * 40,
            node=Path("node"),
            run=lambda *_args, **_kwargs: pytest.fail("compiler must not run"),
        )


def test_generator_rejects_uncommitted_tracked_build_input_mutation(tmp_path: Path) -> None:
    backend, head = _backend_repo(tmp_path)
    source = backend / "src" / "lessons" / "tvideo-journey" / "fixtures" / "farm-golden.ts"
    source.write_text("export const FARM_TVIDEO_JOURNEY_V1 = { version: 2 };\n", encoding="utf-8")

    with pytest.raises(FixtureGenerationError, match="tracked backend build inputs differ from HEAD"):
        generate_fixture(
            backend_root=backend,
            output=tmp_path / "manifest.json",
            provenance_output=tmp_path / "manifest.provenance.json",
            expected_backend_head=head,
            node=Path("node"),
            run=lambda *_args, **_kwargs: pytest.fail("compiler must not run"),
        )


def test_generator_materializes_verified_commit_when_head_moves_after_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, verified_head = _backend_repo(tmp_path)

    def verify_then_advance_head(root: Path, expected_head: str) -> tuple[str, str, list[str]]:
        verified = _verify_backend(root, expected_head)
        source = root / "src" / "lessons" / "tvideo-journey" / "fixtures" / "farm-golden.ts"
        source.write_text("export const FARM_TVIDEO_JOURNEY_V1 = { version: 2 };\n", encoding="utf-8")
        _git(root, "add", "src")
        _git(root, "commit", "-qm", "advance HEAD during generation")
        return cast(tuple[str, str, list[str]], verified)

    monkeypatch.setattr(fixture_generator, "_verify_backend", verify_then_advance_head)
    output = tmp_path / "manifest.json"

    result = generate_fixture(
        backend_root=backend,
        output=output,
        provenance_output=tmp_path / "manifest.provenance.json",
        expected_backend_head=verified_head,
        node=Path("node"),
        run=_recording_runner([]),
    )

    assert _git(backend, "rev-parse", "HEAD") != verified_head
    assert "version: 1" in output.read_text(encoding="utf-8")
    assert "version: 2" not in output.read_text(encoding="utf-8")
    assert result["backend"]["head"] == verified_head
    assert result["backend"]["tree"] == _git(backend, "rev-parse", f"{verified_head}^{{tree}}")


def test_approved_source_commit_changes_build_provenance(tmp_path: Path) -> None:
    backend, first_head = _backend_repo(tmp_path)
    first_output = tmp_path / "first.json"
    first = generate_fixture(
        backend_root=backend,
        output=first_output,
        provenance_output=tmp_path / "first.provenance.json",
        expected_backend_head=first_head,
        node=Path("node"),
        run=_recording_runner([]),
    )
    source = backend / "src" / "lessons" / "tvideo-journey" / "fixtures" / "farm-golden.ts"
    source.write_text("export const FARM_TVIDEO_JOURNEY_V1 = { version: 2 };\n", encoding="utf-8")
    _git(backend, "add", "src")
    _git(backend, "commit", "-qm", "change fixture source")
    second_head = _git(backend, "rev-parse", "HEAD")

    second = generate_fixture(
        backend_root=backend,
        output=tmp_path / "second.json",
        provenance_output=tmp_path / "second.provenance.json",
        expected_backend_head=second_head,
        node=Path("node"),
        run=_recording_runner([]),
    )

    assert second["backend"]["head"] != first["backend"]["head"]
    assert second["backend"]["buildInputSha256"] != first["backend"]["buildInputSha256"]
    assert second["backend"]["relevantSources"] != first["backend"]["relevantSources"]


def test_archive_extraction_rejects_paths_outside_destination(tmp_path: Path) -> None:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as bundle:
        member = tarfile.TarInfo("../escaped.txt")
        member.size = 7
        bundle.addfile(member, io.BytesIO(b"escaped"))

    with pytest.raises(FixtureGenerationError, match="unsafe backend archive member"):
        _extract_archive(payload.getvalue(), tmp_path / "destination")

    assert not (tmp_path / "escaped.txt").exists()
