#!/usr/bin/env python3
"""Build the TVideo fixture from a pinned, clean backend source checkout."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

RELEVANT_BACKEND_SOURCES = (
    "src/lessons/tvideo-journey/fixtures/farm-golden.ts",
    "src/lessons/tvideo-journey/tvideo-journey.cues.ts",
    "src/lessons/authoring/lesson-authoring.flattened-derivatives.ts",
    "src/lessons/lesson-manifest.logic.ts",
    "src/lessons/lesson.constants.ts",
)
BUILD_CONFIG_INPUTS = ("package.json", "package-lock.json", "tsconfig.json", "tsconfig.build.json")
Run = Callable[..., subprocess.CompletedProcess[str]]


class FixtureGenerationError(RuntimeError):
    """Raised when the backend checkout cannot provide reproducible fixture inputs."""


def _git(backend: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=backend,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _tracked_build_inputs(backend: Path) -> list[str]:
    tracked = _git(backend, "ls-files", "--", "src", *BUILD_CONFIG_INPUTS).splitlines()
    return sorted(path for path in tracked if path)


def _build_input_sha256(source_root: Path, inputs: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for relative in inputs:
        payload = (source_root / relative).read_bytes()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(str(len(payload)).encode())
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


def _verify_backend(backend: Path, expected_head: str) -> tuple[str, str, list[str]]:
    head = _git(backend, "rev-parse", "HEAD")
    if head != expected_head:
        raise FixtureGenerationError(f"backend HEAD mismatch: expected {expected_head}, found {head}")
    inputs = _tracked_build_inputs(backend)
    if not inputs:
        raise FixtureGenerationError("backend has no tracked TypeScript build inputs")
    diff = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *inputs],
        cwd=backend,
        check=False,
    )
    if diff.returncode == 1:
        raise FixtureGenerationError("tracked backend build inputs differ from HEAD")
    if diff.returncode != 0:
        raise FixtureGenerationError(f"could not verify backend build inputs (git exit {diff.returncode})")
    tree = _git(backend, "rev-parse", "HEAD^{tree}")
    return head, tree, inputs


def _extract_archive(archive: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    resolved_destination = destination.resolve()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        for member in bundle.getmembers():
            relative = PurePosixPath(member.name)
            target = (destination / relative).resolve()
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not target.is_relative_to(resolved_destination)
                or not (member.isfile() or member.isdir())
            ):
                raise FixtureGenerationError(f"unsafe backend archive member: {member.name}")
        bundle.extractall(destination)


def _materialize_backend_head(backend: Path, destination: Path) -> None:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=backend,
        check=True,
        capture_output=True,
    ).stdout
    _extract_archive(archive, destination)


def _copy_runtime_inputs(source_root: Path, build_root: Path) -> None:
    for source in source_root.glob("src/**/*.cjs"):
        target = build_root / source.relative_to(source_root / "src")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _manifest_provenance(
    *,
    head: str,
    tree: str,
    inputs: Sequence[str],
    build_input_sha256: str,
    relevant_sources: dict[str, str],
    output: Path,
    generator_python: Path,
    generator_typescript: Path,
    typescript_compiler: Path,
    generated_metadata: dict[str, Any],
) -> dict[str, Any]:
    manifest = json.loads(output.read_text(encoding="utf-8"))
    return {
        "schemaVersion": 1,
        "backend": {
            "head": head,
            "tree": tree,
            "buildInputSha256": build_input_sha256,
            "buildInputCount": len(inputs),
            "relevantSources": relevant_sources,
            "typescriptCompilerSha256": _sha256(typescript_compiler),
        },
        "generator": {
            "pythonSha256": _sha256(generator_python),
            "typescriptSha256": _sha256(generator_typescript),
        },
        "manifest": {
            "fileSha256": _sha256(output),
            "canonicalSha256": _canonical_sha256(manifest),
            "manifestChecksum": generated_metadata.get("checksum"),
            "cueCount": generated_metadata.get("cueCount"),
        },
    }


def generate_fixture(
    *,
    backend_root: Path,
    output: Path,
    provenance_output: Path,
    expected_backend_head: str,
    node: Path,
    run: Run = subprocess.run,
) -> dict[str, Any]:
    backend = backend_root.resolve(strict=True)
    source = Path(__file__).with_suffix(".ts").resolve(strict=True)
    python_source = Path(__file__).resolve(strict=True)
    output = output.resolve()
    provenance_output = provenance_output.resolve()
    typescript = (backend / "node_modules" / "typescript" / "lib" / "typescript.js").resolve(strict=True)
    tsc = (backend / "node_modules" / "typescript" / "lib" / "tsc.js").resolve(strict=True)
    head, tree, inputs = _verify_backend(backend, expected_backend_head)

    with tempfile.TemporaryDirectory(prefix="tvideo-farm-manifest-") as directory:
        temporary_root = Path(directory)
        source_root = temporary_root / "backend-source"
        build_root = temporary_root / "backend-build"
        compiled_generator = temporary_root / "generator.mjs"
        _materialize_backend_head(backend, source_root)
        (source_root / "node_modules").symlink_to(backend / "node_modules", target_is_directory=True)
        run(
            [
                str(node),
                str(tsc),
                "--project",
                str(source_root / "tsconfig.build.json"),
                "--outDir",
                str(build_root),
                "--declaration",
                "false",
                "--sourceMap",
                "false",
                "--incremental",
                "false",
            ],
            cwd=source_root,
            check=True,
        )
        _copy_runtime_inputs(source_root, build_root)
        (build_root / "node_modules").symlink_to(backend / "node_modules", target_is_directory=True)
        transpile = (
            "const fs=require('fs'),ts=require(process.argv[1]);"
            "const src=fs.readFileSync(process.argv[2],'utf8');"
            "const out=ts.transpileModule(src,{compilerOptions:{module:ts.ModuleKind.ESNext,"
            "target:ts.ScriptTarget.ES2022}}).outputText;"
            "fs.writeFileSync(process.argv[3],out);"
        )
        run(
            [str(node), "-e", transpile, str(typescript), str(source), str(compiled_generator)],
            check=True,
        )
        generated = run(
            [
                str(node),
                str(compiled_generator),
                "--backend-build-root",
                str(build_root),
                "--output",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        build_input_sha256 = _build_input_sha256(source_root, inputs)
        relevant_sources = {
            relative: _sha256(source_root / relative)
            for relative in RELEVANT_BACKEND_SOURCES
            if relative in inputs
        }

    if not output.is_file():
        raise FixtureGenerationError(f"generator did not create output: {output}")
    try:
        generated_metadata = json.loads(generated.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise FixtureGenerationError("generator did not report checksum metadata") from exc
    provenance = _manifest_provenance(
        head=head,
        tree=tree,
        inputs=inputs,
        build_input_sha256=build_input_sha256,
        relevant_sources=relevant_sources,
        output=output,
        generator_python=python_source,
        generator_typescript=source,
        typescript_compiler=typescript,
        generated_metadata=generated_metadata,
    )
    provenance_output.parent.mkdir(parents=True, exist_ok=True)
    provenance_output.write_text(f"{json.dumps(provenance, indent=2, sort_keys=True)}\n", encoding="utf-8")
    return provenance


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-root", type=Path, required=True)
    parser.add_argument("--expected-backend-head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provenance-output", type=Path)
    parser.add_argument("--node", type=Path, default=Path(os.environ.get("NODE", "node")))
    args = parser.parse_args()
    provenance_output = args.provenance_output or args.output.with_suffix(".provenance.json")
    provenance = generate_fixture(
        backend_root=args.backend_root,
        output=args.output,
        provenance_output=provenance_output,
        expected_backend_head=args.expected_backend_head,
        node=args.node,
    )
    print(json.dumps(provenance, sort_keys=True))


if __name__ == "__main__":
    main()
