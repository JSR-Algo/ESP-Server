import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from scripts.google_live_command_runner import CommandSpec, execute_and_record


IDENTITY = {
    "gitSha": "a" * 40,
    "imageDigest": "sha256:" + "b" * 64,
    "firmwareIdentity": "firmware-v1",
    "configFingerprint": "sha256:" + "c" * 64,
    "fixtureSha256": "d" * 64,
}


def _spec(root: Path, code: str, **changes) -> CommandSpec:
    values = {
        "command_id": "real_api.round_trip",
        "argv": (sys.executable, "-c", code),
        "cwd": root,
        "candidate_identity": IDENTITY,
        "outputs": (root / "real-api" / "report.json",),
        "expected_exit_codes": (0,),
        "timeout_sec": 3.0,
        "cleanup_grace_sec": 0.1,
    }
    values.update(changes)
    return CommandSpec(**values)


def _write_report_code(payload: str = "ok") -> str:
    return (
        "from pathlib import Path; "
        "p=Path('real-api/report.json'); p.parent.mkdir(parents=True, exist_ok=True); "
        f"p.write_text({payload!r})"
    )


def test_executes_argv_and_records_only_secret_source(tmp_path: Path) -> None:
    secret = "must-never-persist"
    spec = _spec(
        tmp_path,
        "import os; " + _write_report_code("ok") + "; assert os.environ['GOOGLE_API_KEY']",
        secret_env=("GOOGLE_API_KEY",),
    )
    provenance = tmp_path / "commands.jsonl"

    result = execute_and_record(spec, env={"GOOGLE_API_KEY": secret}, provenance=provenance)

    entry = json.loads(provenance.read_text().splitlines()[0])
    assert result.exit_code == 0
    assert result.classification == "expected_exit"
    assert entry["secretSources"] == ["<env:GOOGLE_API_KEY>"]
    assert entry["outputs"][0]["sha256"]
    assert secret not in provenance.read_text()
    assert secret not in (tmp_path / "commands.txt").read_text()


def test_rejects_shell_strings_lists_and_argument_secrets(tmp_path: Path) -> None:
    with pytest.raises((TypeError, ValueError)):
        _spec(tmp_path, "pass", argv="echo injected")
    with pytest.raises((TypeError, ValueError)):
        _spec(tmp_path, "pass", argv=[sys.executable, "-c", "pass"])
    with pytest.raises(ValueError):
        _spec(tmp_path, "pass", argv=(sys.executable, "-c", "pass", "GOOGLE_API_KEY=secret"))


def test_passes_only_allowlisted_environment_and_rejects_overlap(tmp_path: Path) -> None:
    spec = _spec(
        tmp_path,
        "import os; assert os.environ.get('SAFE') == 'yes'; "
        "assert 'UNRELATED' not in os.environ; " + _write_report_code(),
        env_allowlist=("SAFE",),
    )
    with pytest.raises(ValueError, match="non-allowlisted"):
        execute_and_record(
            spec,
            env={"SAFE": "yes", "UNRELATED": "no"},
            provenance=tmp_path / "commands.jsonl",
        )
    execute_and_record(
        spec,
        env={"SAFE": "yes"},
        provenance=tmp_path / "commands.jsonl",
    )
    with pytest.raises(ValueError):
        _spec(tmp_path, "pass", env_allowlist=("SAFE",), secret_env=("SAFE",))


def test_protected_stdin_is_passed_but_never_recorded(tmp_path: Path) -> None:
    secret = b"private transcript plan"
    spec = _spec(
        tmp_path,
        "import sys; from pathlib import Path; data=sys.stdin.buffer.read(); "
        "assert data.startswith(b'private'); " + _write_report_code(),
        stdin_source="protected_transcript_plan",
    )
    provenance = tmp_path / "commands.jsonl"
    execute_and_record(spec, provenance=provenance, stdin_bytes=secret)
    rendered = provenance.read_bytes()
    assert b"<stdin:protected_transcript_plan>" in rendered
    assert secret not in rendered


def test_absolute_evidence_arguments_are_recorded_as_relative_labels(tmp_path: Path) -> None:
    protected = tmp_path / "private-plan.json"
    protected.write_text("safe fixture")
    spec = _spec(
        tmp_path,
        _write_report_code(),
        argv=(sys.executable, "-c", _write_report_code(), str(protected)),
        inputs=(protected,),
    )
    execute_and_record(spec, provenance=tmp_path / "commands.jsonl")
    entry = json.loads((tmp_path / "commands.jsonl").read_text())
    assert str(protected) not in json.dumps(entry)
    assert "<evidence:private-plan.json>" in entry["argv"]


def test_absolute_input_argument_reads_bound_file(tmp_path: Path) -> None:
    protected = tmp_path / "input.txt"
    protected.write_text("bound-content")
    output = tmp_path / "real-api" / "report.json"
    code = (
        "import pathlib,sys; data=pathlib.Path(sys.argv[1]).read_text(); "
        "pathlib.Path(sys.argv[2]).write_text(data)"
    )
    execute_and_record(
        _spec(
            tmp_path,
            code,
            argv=(sys.executable, "-c", code, str(protected), str(output)),
            inputs=(protected,),
        ),
        provenance=tmp_path / "commands.jsonl",
    )
    assert output.read_text() == "bound-content"


def test_absolute_argument_must_be_an_exact_declared_artifact(tmp_path: Path) -> None:
    undeclared = tmp_path / "undeclared.txt"
    undeclared.write_text("no")
    with pytest.raises(ValueError, match="declared artifact"):
        execute_and_record(
            _spec(
                tmp_path,
                "pass",
                argv=(sys.executable, "-c", "pass", str(undeclared)),
                outputs=(),
            ),
            provenance=tmp_path / "commands.jsonl",
        )


def test_absolute_input_parent_symlink_to_outside_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-argv-outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside-secret")
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    candidate = linked / "secret.txt"
    with pytest.raises((ValueError, RuntimeError, OSError)):
        execute_and_record(
            _spec(
                tmp_path,
                "pass",
                argv=(sys.executable, "-c", "pass", str(candidate)),
                inputs=(candidate,),
                outputs=(),
            ),
            provenance=tmp_path / "commands.jsonl",
        )


def test_absolute_input_parent_swap_cannot_change_child_bytes(tmp_path: Path) -> None:
    parent = tmp_path / "inputs"
    parent.mkdir()
    source = parent / "value.txt"
    source.write_text("approved")
    outside = tmp_path / "replacement"
    outside.mkdir()
    (outside / "value.txt").write_text("outside")
    observed = tmp_path / "real-api" / "report.json"
    code = "import pathlib,sys; pathlib.Path(sys.argv[2]).write_text(pathlib.Path(sys.argv[1]).read_text())"

    def swap() -> None:
        parent.rename(tmp_path / "moved-inputs")
        parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="(?:input parent|cwd) changed"):
        execute_and_record(
            _spec(
                tmp_path,
                code,
                argv=(sys.executable, "-c", code, str(source), str(observed)),
                inputs=(source,),
            ),
            provenance=tmp_path / "commands.jsonl",
            _before_spawn=swap,
        )
    assert not observed.exists()


def test_child_and_descendant_receive_no_directory_or_unrelated_fds(tmp_path: Path) -> None:
    output = tmp_path / "real-api" / "report.json"
    scan = (
        "import os,stat\nresult=[]\n"
        "for fd in range(3,128):\n"
        " try:\n  opened=os.fstat(fd)\n"
        " except OSError:\n  continue\n"
        " if stat.S_ISDIR(opened.st_mode): result.append(fd)\n"
        "print(result)\n"
    )
    code = (
        "import json,os,pathlib,stat,subprocess,sys\n"
        "fds=[]\n"
        "for fd in range(3,128):\n"
        " try:\n  opened=os.fstat(fd)\n"
        " except OSError:\n  continue\n"
        " if stat.S_ISDIR(opened.st_mode): fds.append(fd)\n"
        f"child=subprocess.check_output([sys.executable,'-c',{scan!r}])\n"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({'dirs':fds,'descendant':child.decode().strip()}))\n"
    )
    execute_and_record(
        _spec(tmp_path, code, argv=(sys.executable, "-c", code, str(output))),
        provenance=tmp_path / "commands.jsonl",
    )
    observed = json.loads(output.read_text())
    assert observed == {"dirs": [], "descendant": "[]"}


def test_rejects_absolute_command_argument_outside_evidence_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute command argument"):
        execute_and_record(
            _spec(tmp_path, "pass", argv=(sys.executable, "-c", "pass", "/private/outside"), outputs=()),
            provenance=tmp_path / "commands.jsonl",
        )


def test_rejects_paths_outside_evidence_root_and_input_output_aliases(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside"
    with pytest.raises(ValueError):
        execute_and_record(
            _spec(tmp_path, "pass", inputs=(outside,)),
            provenance=tmp_path / "commands.jsonl",
        )
    source = tmp_path / "input.txt"
    source.write_text("input")
    for kind in ("direct", "symlink", "hardlink"):
        output = source if kind == "direct" else tmp_path / f"{kind}.txt"
        if kind == "symlink":
            output.symlink_to(source)
        elif kind == "hardlink":
            os.link(source, output)
        with pytest.raises((ValueError, RuntimeError)):
            execute_and_record(
                _spec(tmp_path, "pass", inputs=(source,), outputs=(output,)),
                provenance=tmp_path / f"{kind}.jsonl",
            )


@pytest.mark.parametrize("depth", ["first", "middle", "late"])
def test_output_parent_symlink_never_creates_outside_directory(
    tmp_path: Path, depth: str
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-mkdir-{depth}"
    outside.mkdir()
    if depth == "first":
        (tmp_path / "link").symlink_to(outside, target_is_directory=True)
        output = tmp_path / "link" / "created" / "report.json"
    elif depth == "middle":
        (tmp_path / "safe").mkdir()
        (tmp_path / "safe" / "link").symlink_to(outside, target_is_directory=True)
        output = tmp_path / "safe" / "link" / "created" / "report.json"
    else:
        (tmp_path / "safe" / "nested").mkdir(parents=True)
        (tmp_path / "safe" / "nested" / "link").symlink_to(
            outside, target_is_directory=True
        )
        output = tmp_path / "safe" / "nested" / "link" / "created" / "report.json"
    with pytest.raises((ValueError, RuntimeError, OSError)):
        execute_and_record(
            _spec(tmp_path, "pass", outputs=(output,)),
            provenance=tmp_path / "commands.jsonl",
        )
    assert not (outside / "created").exists()


def test_file_component_cannot_be_materialized_as_output_parent(tmp_path: Path) -> None:
    component = tmp_path / "file-component"
    component.write_text("keep")
    with pytest.raises((ValueError, RuntimeError, OSError)):
        execute_and_record(
            _spec(tmp_path, "pass", outputs=(component / "nested" / "report.json",)),
            provenance=tmp_path / "commands.jsonl",
        )
    assert component.read_text() == "keep"


def test_case_output_parent_alias_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "SAFE").mkdir()
    with pytest.raises(ValueError, match="aliases|component"):
        execute_and_record(
            _spec(tmp_path, "pass", outputs=(tmp_path / "safe" / "report.json",)),
            provenance=tmp_path / "commands.jsonl",
        )


@pytest.mark.parametrize(
    ("component", "alias"),
    [("safe", "SAFE"), ("k", "\u212a")],
)
def test_output_parent_alias_created_after_scan_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    component: str,
    alias: str,
) -> None:
    import scripts.google_live_command_runner as runner

    def create_alias(stage: str, parent_fd: int, observed: str) -> None:
        if stage == "after_scan" and observed == component:
            os.mkdir(alias, dir_fd=parent_fd)

    monkeypatch.setattr(runner, "_component_race_hook", create_alias)
    with pytest.raises((ValueError, RuntimeError, OSError), match="aliases|component"):
        runner._secure_materialize_directory(tmp_path, tmp_path / component)


def test_output_parent_alias_winning_mkdir_eexist_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.google_live_command_runner as runner

    real_mkdir = runner.os.mkdir

    def alias_then_eexist(name, mode=0o777, *, dir_fd=None):
        if name == "safe":
            real_mkdir("SAFE", mode, dir_fd=dir_fd)
            raise FileExistsError(name)
        return real_mkdir(name, mode, dir_fd=dir_fd)

    monkeypatch.setattr(runner.os, "mkdir", alias_then_eexist)
    with pytest.raises((ValueError, RuntimeError, OSError), match="aliases|component"):
        runner._secure_materialize_directory(tmp_path, tmp_path / "safe")


def test_output_parent_renamed_to_alias_after_open_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.google_live_command_runner as runner

    (tmp_path / "safe").mkdir()

    def rename_alias(stage: str, parent_fd: int, component: str) -> None:
        if stage == "after_open" and component == "safe":
            os.rename("safe", "SAFE", src_dir_fd=parent_fd, dst_dir_fd=parent_fd)

    monkeypatch.setattr(runner, "_component_race_hook", rename_alias)
    with pytest.raises((ValueError, RuntimeError, OSError), match="aliases|component"):
        runner._secure_materialize_directory(tmp_path, tmp_path / "safe")


def test_output_parent_renamed_after_validation_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.google_live_command_runner as runner

    (tmp_path / "safe").mkdir()

    def rename_alias(stage: str, parent_fd: int, component: str) -> None:
        if stage == "before_return" and component == "safe":
            os.rename("safe", "SAFE", src_dir_fd=parent_fd, dst_dir_fd=parent_fd)

    monkeypatch.setattr(runner, "_component_race_hook", rename_alias)
    with pytest.raises((ValueError, RuntimeError, OSError), match="aliases|component"):
        runner._secure_materialize_directory(tmp_path, tmp_path / "safe")


def test_output_parent_renamed_before_advancing_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.google_live_command_runner as runner

    (tmp_path / "safe").mkdir()

    def rename_alias(stage: str, parent_fd: int, component: str) -> None:
        if stage == "before_advance" and component == "safe":
            os.rename("safe", "SAFE", src_dir_fd=parent_fd, dst_dir_fd=parent_fd)

    monkeypatch.setattr(runner, "_component_race_hook", rename_alias)
    with pytest.raises((ValueError, RuntimeError, OSError), match="aliases|component"):
        runner._secure_materialize_directory(tmp_path, tmp_path / "safe" / "nested")


def test_unicode_output_parent_component_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="component"):
        execute_and_record(
            _spec(tmp_path, "pass", outputs=(tmp_path / "saf\u00e9" / "report.json",)),
            provenance=tmp_path / "commands.jsonl",
        )


def test_valid_nested_output_parent_is_materialized_inside_root(tmp_path: Path) -> None:
    output = tmp_path / "safe" / "nested" / "report.json"
    code = "from pathlib import Path; Path('safe/nested/report.json').write_text('ok')"
    execute_and_record(
        _spec(tmp_path, code, outputs=(output,)),
        provenance=tmp_path / "commands.jsonl",
    )
    assert output.read_text() == "ok"


def test_provenance_parent_symlink_does_not_create_lock_outside(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-provenance-outside"
    outside.mkdir()
    linked = tmp_path / "linked-root"
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises((ValueError, RuntimeError, OSError)):
        execute_and_record(
            _spec(linked, "pass", outputs=()),
            provenance=linked / "commands.jsonl",
        )
    assert list(outside.iterdir()) == []


def _second_provenance_entry(provenance: Path) -> dict:
    entry = json.loads(provenance.read_text())
    entry["commandId"] = "diagnostic.second"
    entry["specSha256"] = "e" * 64
    return entry


def _provenance_entry(provenance: Path, command_id: str, digest: str) -> dict:
    entry = json.loads(provenance.read_text().splitlines()[0])
    entry["commandId"] = command_id
    entry["specSha256"] = digest * 64
    return entry


def test_pair_pointer_is_canonical_and_binds_generation_hashes() -> None:
    import scripts.google_live_command_runner as runner

    pointer = runner.PairPointer(
        generation="a" * 32,
        jsonl_sha256="b" * 64,
        projection_sha256="c" * 64,
        entry_count=2,
        spec_digest_sha256="d" * 64,
    )
    rendered = runner._render_pair_pointer(pointer)
    assert runner._parse_pair_pointer(rendered) == pointer
    assert rendered.endswith(b"\n")
    for invalid in (
        rendered.replace(b'"generation":"', b'"generation":"../', 1),
        rendered.replace(b'"entryCount":2', b'"entryCount":-1', 1),
        b" " + rendered,
    ):
        with pytest.raises(ValueError, match="pointer"):
            runner._parse_pair_pointer(invalid)


def test_successful_commit_creates_pointer_bound_immutable_generation(
    tmp_path: Path,
) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    pointer = runner._parse_pair_pointer(
        (tmp_path / ".commands.jsonl.pair").read_bytes()
    )
    generation_dir = tmp_path / ".commands.jsonl.generations"
    jsonl_generation = generation_dir / f"{pointer.generation}.jsonl"
    projection_generation = generation_dir / f"{pointer.generation}.txt"
    assert jsonl_generation.read_bytes() == provenance.read_bytes()
    assert projection_generation.read_bytes() == provenance.with_suffix(".txt").read_bytes()
    assert stat.S_IMODE(jsonl_generation.stat().st_mode) == 0o400
    assert stat.S_IMODE(projection_generation.stat().st_mode) == 0o400


@pytest.mark.parametrize(
    "failure_stage",
    [
        "after_generation_jsonl",
        "after_generation_projection",
        "after_public_jsonl",
        "after_public_projection",
        "before_pointer",
        "after_pointer",
    ],
)
def test_generation_commit_crash_boundaries_leave_complete_old_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    original_jsonl = provenance.read_bytes()
    original_projection = provenance.with_suffix(".txt").read_bytes()

    def fail(stage: str) -> None:
        if stage == failure_stage:
            raise RuntimeError(f"injected {failure_stage}")

    monkeypatch.setattr(runner, "_provenance_transaction_hook", fail)
    with pytest.raises(RuntimeError, match="injected"):
        runner._commit_entry(
            provenance,
            _provenance_entry(provenance, "diagnostic.writer_a", "1"),
        )

    parent_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        committed = runner._read_committed_pair_at(
            parent_fd, provenance.name, provenance.with_suffix(".txt").name
        )
    finally:
        os.close(parent_fd)
    assert committed.jsonl == original_jsonl
    assert committed.projection == original_projection
    assert provenance.read_bytes() == original_jsonl
    assert provenance.with_suffix(".txt").read_bytes() == original_projection


def test_next_commit_cleans_only_unreachable_known_generations(tmp_path: Path) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    generation_dir = tmp_path / ".commands.jsonl.generations"
    stale = "e" * 32
    stale_jsonl = generation_dir / f"{stale}.jsonl"
    stale_projection = generation_dir / f"{stale}.txt"
    stale_jsonl.write_bytes(provenance.read_bytes())
    stale_projection.write_bytes(provenance.with_suffix(".txt").read_bytes())
    stale_jsonl.chmod(0o400)
    stale_projection.chmod(0o400)

    runner._commit_entry(
        provenance,
        _provenance_entry(provenance, "diagnostic.writer_a", "1"),
    )

    known = [
        path
        for path in generation_dir.iterdir()
        if path.name.endswith((".jsonl", ".txt"))
    ]
    assert len(known) <= 4
    assert not stale_jsonl.exists()
    assert not stale_projection.exists()


@pytest.mark.parametrize(
    "kind", ["unknown", "lone_jsonl", "lone_txt", "forged", "symlink", "hardlink"]
)
def test_generation_cleanup_rejects_unsafe_inventory_without_deleting(
    tmp_path: Path, kind: str
) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    generation_dir = tmp_path / ".commands.jsonl.generations"
    stale = "e" * 32
    stale_jsonl = generation_dir / f"{stale}.jsonl"
    stale_projection = generation_dir / f"{stale}.txt"
    if kind == "unknown":
        unsafe = generation_dir / "operator-note"
        unsafe.write_text("keep")
    elif kind == "lone_jsonl":
        unsafe = stale_jsonl
        unsafe.write_bytes(provenance.read_bytes())
        unsafe.chmod(0o400)
    elif kind == "lone_txt":
        unsafe = stale_projection
        unsafe.write_bytes(provenance.with_suffix(".txt").read_bytes())
        unsafe.chmod(0o400)
    elif kind == "forged":
        stale_jsonl.write_bytes(b"forged\n")
        stale_projection.write_bytes(b"forged\n")
        stale_jsonl.chmod(0o400)
        stale_projection.chmod(0o400)
        unsafe = stale_jsonl
    elif kind == "symlink":
        unsafe = stale_jsonl
        unsafe.symlink_to(provenance)
        stale_projection.write_bytes(provenance.with_suffix(".txt").read_bytes())
        stale_projection.chmod(0o400)
    else:
        unsafe = stale_jsonl
        os.link(provenance, unsafe)
        stale_projection.write_bytes(provenance.with_suffix(".txt").read_bytes())
        stale_projection.chmod(0o400)
    before = {path.name for path in generation_dir.iterdir()}

    with pytest.raises((OSError, RuntimeError, ValueError), match="generation|pair|artifact|inventory"):
        runner._commit_entry(
            provenance,
            _provenance_entry(provenance, "diagnostic.writer_a", "1"),
        )

    assert {path.name for path in generation_dir.iterdir()} == before
    assert unsafe.exists() or unsafe.is_symlink()


@pytest.mark.parametrize("kind", ["pointer_input", "generation_output"])
def test_rejects_pointer_and_generation_control_artifacts(
    tmp_path: Path, kind: str
) -> None:
    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    marker = tmp_path / "child-executed"
    code = f"from pathlib import Path; Path({str(marker)!r}).write_text('bad')"
    if kind == "pointer_input":
        changes = {"inputs": (tmp_path / ".commands.jsonl.pair",), "outputs": ()}
    else:
        output = tmp_path / ".commands.jsonl.generations" / "command-output"
        changes = {"inputs": (), "outputs": (output,)}
        code += f"; Path({str(output)!r}).write_text('bad')"
    with pytest.raises(ValueError, match="control"):
        execute_and_record(
            _spec(
                tmp_path,
                code,
                command_id=f"diagnostic.{kind}",
                **changes,
            ),
            provenance=provenance,
        )
    assert not marker.exists()


@pytest.mark.parametrize(
    ("stage", "target_name"),
    [
        ("after_jsonl_final_check", "commands.jsonl"),
        ("after_projection_final_check", "commands.txt"),
    ],
)
def test_committed_pair_rejects_public_mutation_between_final_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    target_name: str,
) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    mutated = False

    def mutate(
        observed_stage: str,
        directory_fd: int,
        jsonl_name: str,
        projection_name: str,
    ) -> None:
        nonlocal mutated
        if (
            observed_stage == stage
            and jsonl_name == provenance.name
            and not mutated
        ):
            mutated = True
            name = target_name
            descriptor = os.open(name, os.O_WRONLY | os.O_APPEND, dir_fd=directory_fd)
            try:
                os.write(descriptor, b"tampered")
            finally:
                os.close(descriptor)

    monkeypatch.setattr(runner, "_provenance_pair_hook", mutate)
    parent_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(RuntimeError, match="pair|artifact|public"):
            runner._read_committed_pair_at(
                parent_fd, provenance.name, provenance.with_suffix(".txt").name
            )
    finally:
        os.close(parent_fd)


def test_committed_pair_rejects_pointer_aba_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    pointer = tmp_path / ".commands.jsonl.pair"
    moved = tmp_path / ".commands.jsonl.pair.moved"
    mutated = False

    def aba(
        stage: str,
        directory_fd: int,
        jsonl_name: str,
        projection_name: str,
    ) -> None:
        del directory_fd, projection_name
        nonlocal mutated
        if stage == "before_pointer_recheck" and jsonl_name == provenance.name and not mutated:
            mutated = True
            pointer.rename(moved)
            moved.rename(pointer)

    monkeypatch.setattr(runner, "_provenance_pair_hook", aba)
    parent_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(RuntimeError, match="pointer|pair"):
            runner._read_committed_pair_at(
                parent_fd, provenance.name, provenance.with_suffix(".txt").name
            )
    finally:
        os.close(parent_fd)


def test_committed_pair_rejects_in_place_pointer_aba_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    pointer = tmp_path / ".commands.jsonl.pair"
    original = pointer.read_bytes()
    mutated = False

    def aba(
        stage: str,
        directory_fd: int,
        jsonl_name: str,
        projection_name: str,
    ) -> None:
        del directory_fd, projection_name
        nonlocal mutated
        if stage == "before_pointer_recheck" and jsonl_name == provenance.name and not mutated:
            mutated = True
            pointer.write_bytes(b"tampered\n")
            pointer.write_bytes(original)

    monkeypatch.setattr(runner, "_provenance_pair_hook", aba)
    parent_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(RuntimeError, match="pointer|pair"):
            runner._read_committed_pair_at(
                parent_fd, provenance.name, provenance.with_suffix(".txt").name
            )
    finally:
        os.close(parent_fd)


def test_committed_read_does_not_create_missing_generation_directory(
    tmp_path: Path,
) -> None:
    import scripts.google_live_command_runner as runner

    pointer = runner.PairPointer("a" * 32, "b" * 64, "c" * 64, 0, "d" * 64)
    (tmp_path / ".commands.jsonl.pair").write_bytes(
        runner._render_pair_pointer(pointer)
    )
    parent_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises((FileNotFoundError, RuntimeError)):
            runner._read_committed_pair_at(parent_fd, "commands.jsonl", "commands.txt")
    finally:
        os.close(parent_fd)
    assert not (tmp_path / ".commands.jsonl.generations").exists()


def test_committed_pair_rejects_pointer_wrong_mode(tmp_path: Path) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    (tmp_path / ".commands.jsonl.pair").chmod(0o644)
    parent_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(RuntimeError, match="pointer|artifact"):
            runner._read_committed_pair_at(
                parent_fd, provenance.name, provenance.with_suffix(".txt").name
            )
    finally:
        os.close(parent_fd)


def test_committed_pair_rejects_in_place_generation_aba_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    pointer = runner._parse_pair_pointer(
        (tmp_path / ".commands.jsonl.pair").read_bytes()
    )
    generation = (
        tmp_path / ".commands.jsonl.generations" / f"{pointer.generation}.jsonl"
    )
    original = generation.read_bytes()
    mutated = False

    def aba(
        stage: str,
        directory_fd: int,
        jsonl_name: str,
        projection_name: str,
    ) -> None:
        del directory_fd, projection_name
        nonlocal mutated
        if stage == "before_pointer_recheck" and jsonl_name == provenance.name and not mutated:
            mutated = True
            generation.chmod(0o600)
            generation.write_bytes(b"tampered\n")
            generation.write_bytes(original)
            generation.chmod(0o400)

    monkeypatch.setattr(runner, "_provenance_pair_hook", aba)
    parent_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(RuntimeError, match="committed|pair"):
            runner._read_committed_pair_at(
                parent_fd, provenance.name, provenance.with_suffix(".txt").name
            )
    finally:
        os.close(parent_fd)


@pytest.mark.parametrize("mutation", ["append", "hardlink"])
def test_provenance_read_rejects_file_mutation_after_initial_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    import scripts.google_live_command_runner as runner

    artifact = tmp_path / "commands.jsonl"
    artifact.write_bytes(b"original")

    def mutate(stage: str, directory_fd: int, name: str) -> None:
        if stage != "after_read":
            return
        if mutation == "append":
            descriptor = os.open(name, os.O_WRONLY | os.O_APPEND, dir_fd=directory_fd)
            try:
                os.write(descriptor, b"changed")
            finally:
                os.close(descriptor)
        else:
            os.link(
                name,
                "commands.alias",
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )

    monkeypatch.setattr(runner, "_provenance_read_hook", mutate)
    directory_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(RuntimeError, match="artifact"):
            runner._read_existing_at(directory_fd, artifact.name)
    finally:
        os.close(directory_fd)


@pytest.mark.parametrize("mutation", ["recreate", "byte_restore", "hardlink"])
def test_pair_snapshot_rejects_projection_mutation_during_jsonl_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    import scripts.google_live_command_runner as runner

    jsonl = tmp_path / "commands.jsonl"
    projection = tmp_path / "commands.txt"
    jsonl.write_bytes(b"jsonl")
    projection.write_bytes(b"projection")
    mutated = False

    def mutate(stage: str, directory_fd: int, name: str) -> None:
        nonlocal mutated
        if stage != "after_read" or name != jsonl.name or mutated:
            return
        mutated = True
        if mutation == "recreate":
            projection.unlink()
            projection.write_bytes(b"projection")
        elif mutation == "byte_restore":
            descriptor = os.open(projection.name, os.O_WRONLY, dir_fd=directory_fd)
            try:
                os.write(descriptor, b"X")
                os.lseek(descriptor, 0, os.SEEK_SET)
                os.write(descriptor, b"p")
            finally:
                os.close(descriptor)
        else:
            os.link(projection, tmp_path / "projection.alias")

    monkeypatch.setattr(runner, "_provenance_read_hook", mutate)
    directory_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(RuntimeError, match="pair|artifact"):
            runner._read_provenance_pair_at(
                directory_fd, jsonl.name, projection.name
            )
    finally:
        os.close(directory_fd)


def test_absent_pair_snapshot_rejects_file_appearing_between_lookups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.google_live_command_runner as runner

    def create_projection(
        stage: str, directory_fd: int, jsonl_name: str, projection_name: str
    ) -> None:
        if stage == "after_jsonl_lookup":
            descriptor = os.open(
                projection_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=directory_fd,
            )
            os.close(descriptor)

    monkeypatch.setattr(runner, "_provenance_pair_hook", create_projection)
    directory_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        with pytest.raises(RuntimeError, match="pair"):
            runner._read_provenance_pair_at(
                directory_fd, "commands.jsonl", "commands.txt"
            )
    finally:
        os.close(directory_fd)


def test_pair_snapshot_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "commands.jsonl"
    os.mkfifo(fifo)
    script = (
        "import os,sys\n"
        "from scripts import google_live_command_runner as runner\n"
        "fd=os.open(sys.argv[1], os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))\n"
        "try:\n"
        " runner._read_provenance_pair_at(fd, 'commands.jsonl', 'commands.txt')\n"
        "except RuntimeError:\n"
        " raise SystemExit(0)\n"
        "finally:\n"
        " os.close(fd)\n"
        "raise SystemExit(2)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).parents[1],
        timeout=3,
        check=False,
    )
    assert completed.returncode == 0


def test_final_pair_verification_rejects_jsonl_mutation_during_projection_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    original_jsonl = provenance.read_bytes()
    original_projection = provenance.with_suffix(".txt").read_bytes()
    armed = False
    mutated = False

    def arm(stage: str) -> None:
        nonlocal armed
        if stage == "before_verify":
            armed = True

    def mutate(stage: str, directory_fd: int, name: str) -> None:
        nonlocal mutated
        if (
            armed
            and not mutated
            and stage == "after_read"
            and name == provenance.with_suffix(".txt").name
        ):
            mutated = True
            runner._atomic_replace_at(directory_fd, provenance.name, original_jsonl)

    monkeypatch.setattr(runner, "_provenance_transaction_hook", arm)
    monkeypatch.setattr(runner, "_provenance_read_hook", mutate)
    with pytest.raises(RuntimeError, match="pair|artifact|rollback"):
        runner._commit_entry(
            provenance,
            _provenance_entry(provenance, "diagnostic.writer_a", "1"),
        )
    assert provenance.read_bytes() == original_jsonl
    assert provenance.with_suffix(".txt").read_bytes() == original_projection


def test_preflight_pair_race_never_allows_child_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    existing = runner.parse_provenance(provenance.read_bytes())
    newer_jsonl = runner.render_provenance(
        [*existing, _provenance_entry(provenance, "diagnostic.injected", "3")]
    )
    marker = tmp_path / "child-executed"
    armed = False
    mutated = False

    def arm(stage: str) -> None:
        nonlocal armed
        if stage == "preflight_before_read":
            armed = True

    def mutate(stage: str, directory_fd: int, name: str) -> None:
        nonlocal mutated
        if (
            armed
            and not mutated
            and stage == "after_read"
            and name == provenance.with_suffix(".txt").name
        ):
            mutated = True
            runner._atomic_replace_at(directory_fd, provenance.name, newer_jsonl)

    monkeypatch.setattr(runner, "_provenance_transaction_hook", arm)
    monkeypatch.setattr(runner, "_provenance_read_hook", mutate)
    code = f"from pathlib import Path; Path({str(marker)!r}).write_text('bad')"
    with pytest.raises((RuntimeError, ValueError), match="pair|artifact|provenance"):
        execute_and_record(
            _spec(tmp_path, code, command_id="diagnostic.candidate", outputs=()),
            provenance=provenance,
        )
    assert not marker.exists()


@pytest.mark.parametrize("mutation", ["missing_jsonl", "missing_projection", "mismatch"])
def test_preflight_repairs_public_views_from_committed_generation_before_child(
    tmp_path: Path, mutation: str
) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    projection = provenance.with_suffix(".txt")
    if mutation == "missing_jsonl":
        provenance.unlink()
    elif mutation == "missing_projection":
        projection.unlink()
    else:
        provenance.write_bytes(b"tampered\n")
        projection.write_bytes(b"tampered\n")
    marker = tmp_path / "child-executed"
    code = f"from pathlib import Path; Path({str(marker)!r}).write_text('ok')"

    execute_and_record(
        _spec(
            tmp_path,
            code,
            command_id=f"diagnostic.repair_{mutation}",
            outputs=(marker,),
        ),
        provenance=provenance,
    )

    assert marker.read_text() == "ok"
    entries = runner.parse_provenance(provenance.read_bytes())
    assert entries[-1]["commandId"] == f"diagnostic.repair_{mutation}"
    assert projection.read_bytes() == runner.render_commands_projection(entries)


def test_preflight_repair_race_never_allows_child_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    provenance.with_suffix(".txt").write_bytes(b"tampered\n")
    marker = tmp_path / "child-executed"

    def mutate(stage: str) -> None:
        if stage == "preflight_after_repair_jsonl":
            provenance.write_bytes(b"raced\n")

    monkeypatch.setattr(runner, "_provenance_transaction_hook", mutate)
    code = f"from pathlib import Path; Path({str(marker)!r}).write_text('bad')"
    with pytest.raises((RuntimeError, ValueError), match="provenance|pair"):
        execute_and_record(
            _spec(
                tmp_path,
                code,
                command_id="diagnostic.repair_race",
                outputs=(),
            ),
            provenance=provenance,
        )
    assert not marker.exists()


def test_initially_absent_pair_rolls_back_failure_after_first_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.google_live_command_runner as runner

    seed = tmp_path / "seed"
    seed.mkdir()
    seed_provenance = seed / "commands.jsonl"
    execute_and_record(_spec(seed, "pass", outputs=()), provenance=seed_provenance)
    target = tmp_path / "target"
    target.mkdir()
    provenance = target / "commands.jsonl"

    def fail_after_jsonl(stage: str) -> None:
        if stage == "after_jsonl_replace":
            raise ValueError("injected first publish failure")

    monkeypatch.setattr(runner, "_provenance_transaction_hook", fail_after_jsonl)
    with pytest.raises(ValueError, match="first publish failure"):
        runner._commit_entry(
            provenance,
            _provenance_entry(seed_provenance, "diagnostic.writer_a", "1"),
        )
    assert not provenance.exists()
    assert not provenance.with_suffix(".txt").exists()


@pytest.mark.parametrize(
    "swap_stage",
    [
        "after_lock",
        "before_read",
        "after_jsonl_replace",
        "after_projection_replace",
        "post_publish",
    ],
)
def test_provenance_parent_swap_never_targets_replacement_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swap_stage: str
) -> None:
    import scripts.google_live_command_runner as runner

    parent = tmp_path / "evidence"
    parent.mkdir()
    provenance = parent / "commands.jsonl"
    execute_and_record(_spec(parent, "pass", outputs=()), provenance=provenance)
    entry = _second_provenance_entry(provenance)
    original_jsonl = provenance.read_bytes()
    original_projection = provenance.with_suffix(".txt").read_bytes()
    moved = tmp_path / "moved-evidence"
    swapped = False

    def swap(stage: str) -> None:
        nonlocal swapped
        if stage == swap_stage and not swapped:
            swapped = True
            parent.rename(moved)
            parent.mkdir()

    monkeypatch.setattr(runner, "_provenance_transaction_hook", swap)
    with pytest.raises(RuntimeError, match="cwd changed"):
        runner._commit_entry(provenance, entry)

    assert list(parent.iterdir()) == []
    assert (moved / "commands.jsonl").read_bytes() == original_jsonl
    assert (moved / "commands.txt").read_bytes() == original_projection


def test_provenance_rollback_remains_bound_after_parent_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.google_live_command_runner as runner

    parent = tmp_path / "evidence"
    parent.mkdir()
    provenance = parent / "commands.jsonl"
    execute_and_record(_spec(parent, "pass", outputs=()), provenance=provenance)
    entry = _second_provenance_entry(provenance)
    original_jsonl = provenance.read_bytes()
    original_projection = provenance.with_suffix(".txt").read_bytes()
    moved = tmp_path / "moved-evidence"

    def fail_then_swap(stage: str) -> None:
        if stage == "after_jsonl_replace":
            raise ValueError("injected publish failure")
        if stage == "before_rollback":
            parent.rename(moved)
            parent.mkdir()

    monkeypatch.setattr(runner, "_provenance_transaction_hook", fail_then_swap)
    with pytest.raises(ValueError, match="injected publish failure"):
        runner._commit_entry(provenance, entry)

    assert list(parent.iterdir()) == []
    assert (moved / "commands.jsonl").read_bytes() == original_jsonl
    assert (moved / "commands.txt").read_bytes() == original_projection


def test_provenance_preflight_rejects_parent_swap_after_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.google_live_command_runner as runner

    parent = tmp_path / "evidence"
    parent.mkdir()
    provenance = parent / "commands.jsonl"
    execute_and_record(_spec(parent, "pass", outputs=()), provenance=provenance)
    moved = tmp_path / "moved-evidence"

    def swap(stage: str) -> None:
        if stage == "preflight_after_lock":
            parent.rename(moved)
            parent.mkdir()

    monkeypatch.setattr(runner, "_provenance_transaction_hook", swap)
    with pytest.raises(RuntimeError, match="cwd changed"):
        runner._preflight_provenance(provenance, "diagnostic.second")

    assert list(parent.iterdir()) == []


def test_replaced_lock_cannot_create_a_concurrent_lock_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    entry_a = _provenance_entry(provenance, "diagnostic.writer_a", "1")
    entry_b = _provenance_entry(provenance, "diagnostic.writer_b", "2")
    lock_path = tmp_path / ".commands.jsonl.lock"
    writer_b_acquired = threading.Event()
    writer_b_errors = []
    writer_b = None
    replaced = False

    def run_writer_b() -> None:
        try:
            runner._commit_entry(provenance, entry_b)
        except BaseException as exc:
            writer_b_errors.append(exc)

    def replace_lock(stage: str) -> None:
        nonlocal replaced, writer_b
        if threading.current_thread().name == "writer-b":
            if stage == "after_lock":
                writer_b_acquired.set()
            return
        if stage == "after_lock" and not replaced:
            replaced = True
            lock_path.unlink()
            lock_path.write_bytes(b"")
            lock_path.chmod(0o600)
            writer_b = threading.Thread(target=run_writer_b, name="writer-b")
            writer_b.start()
            assert not writer_b_acquired.wait(0.2)

    monkeypatch.setattr(runner, "_provenance_transaction_hook", replace_lock)
    with pytest.raises(RuntimeError, match="lock"):
        runner._commit_entry(provenance, entry_a)
    assert writer_b is not None
    writer_b.join(timeout=3)
    assert not writer_b.is_alive()
    assert writer_b_errors == []
    entries = runner.parse_provenance(provenance.read_bytes())
    assert [entry["commandId"] for entry in entries] == [
        "real_api.round_trip",
        "diagnostic.writer_b",
    ]
    assert provenance.with_suffix(".txt").read_bytes() == runner.render_commands_projection(
        entries
    )


@pytest.mark.parametrize(
    ("mutation_stage", "forbidden_stage", "mutation"),
    [
        ("after_lock", "before_read", "replace"),
        ("after_jsonl_replace", "after_projection_replace", "replace"),
        ("after_projection_replace", "post_publish", "unlink"),
        ("after_jsonl_replace", "after_projection_replace", "hardlink"),
    ],
)
def test_lock_entry_is_revalidated_before_each_transaction_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation_stage: str,
    forbidden_stage: str,
    mutation: str,
) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    original_jsonl = provenance.read_bytes()
    original_projection = provenance.with_suffix(".txt").read_bytes()
    lock_path = tmp_path / ".commands.jsonl.lock"
    reached_forbidden_boundary = False
    mutated = False

    def mutate_lock(stage: str) -> None:
        nonlocal reached_forbidden_boundary, mutated
        if stage == forbidden_stage:
            reached_forbidden_boundary = True
        if stage != mutation_stage or mutated:
            return
        mutated = True
        if mutation == "hardlink":
            os.link(lock_path, tmp_path / "lock.alias")
        else:
            lock_path.unlink()
            if mutation == "replace":
                lock_path.write_bytes(b"")

    monkeypatch.setattr(runner, "_provenance_transaction_hook", mutate_lock)
    with pytest.raises(RuntimeError, match="lock"):
        runner._commit_entry(
            provenance,
            _provenance_entry(provenance, "diagnostic.writer_a", "1"),
        )
    assert reached_forbidden_boundary is False
    assert provenance.read_bytes() == original_jsonl
    assert provenance.with_suffix(".txt").read_bytes() == original_projection


@pytest.mark.parametrize(
    "failure_stage",
    ["after_jsonl_replace", "after_projection_replace", "post_publish"],
)
def test_rollback_compare_and_swap_preserves_newer_complete_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    entry_a = _provenance_entry(provenance, "diagnostic.writer_a", "1")
    entry_b = _provenance_entry(provenance, "diagnostic.writer_b", "2")
    existing = runner.parse_provenance(provenance.read_bytes())
    newer_entries = [*existing, entry_b]
    newer_jsonl = runner.render_provenance(newer_entries)
    newer_projection = runner.render_commands_projection(newer_entries)
    injected = False

    def inject_newer_commit(stage: str) -> None:
        nonlocal injected
        if stage == failure_stage:
            raise ValueError("injected writer A failure")
        if stage == "before_rollback" and not injected:
            injected = True
            parent_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                runner._atomic_replace_at(parent_fd, provenance.name, newer_jsonl)
                runner._atomic_replace_at(
                    parent_fd, provenance.with_suffix(".txt").name, newer_projection
                )
            finally:
                os.close(parent_fd)

    monkeypatch.setattr(runner, "_provenance_transaction_hook", inject_newer_commit)
    with pytest.raises(RuntimeError, match="rollback conflict"):
        runner._commit_entry(provenance, entry_a)
    assert provenance.read_bytes() == newer_jsonl
    assert provenance.with_suffix(".txt").read_bytes() == newer_projection


def test_directory_fsync_failure_after_publish_rolls_back_complete_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    original_jsonl = provenance.read_bytes()
    original_projection = provenance.with_suffix(".txt").read_bytes()
    real_fsync = runner.os.fsync
    failed = False

    def fail_first_directory_sync(descriptor: int) -> None:
        nonlocal failed
        if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not failed:
            failed = True
            raise OSError("injected directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(runner.os, "fsync", fail_first_directory_sync)
    with pytest.raises(OSError, match="directory fsync failure"):
        runner._commit_entry(
            provenance,
            _provenance_entry(provenance, "diagnostic.writer_a", "1"),
        )
    assert provenance.read_bytes() == original_jsonl
    assert provenance.with_suffix(".txt").read_bytes() == original_projection


@pytest.mark.parametrize("original_exists", [True, False])
def test_directory_fsync_failure_during_rollback_still_restores_complete_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original_exists: bool,
) -> None:
    import scripts.google_live_command_runner as runner

    seed = tmp_path / "seed"
    seed.mkdir()
    seed_provenance = seed / "commands.jsonl"
    execute_and_record(_spec(seed, "pass", outputs=()), provenance=seed_provenance)
    entry = _provenance_entry(seed_provenance, "diagnostic.writer_a", "1")
    parent = tmp_path / "target"
    parent.mkdir()
    provenance = parent / "commands.jsonl"
    if original_exists:
        execute_and_record(_spec(parent, "pass", outputs=()), provenance=provenance)
        original_jsonl = provenance.read_bytes()
        original_projection = provenance.with_suffix(".txt").read_bytes()
    else:
        original_jsonl = None
        original_projection = None
    real_fsync = runner.os.fsync
    directory_syncs = 0

    def fail_first_rollback_directory_sync(descriptor: int) -> None:
        nonlocal directory_syncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_syncs += 1
            if directory_syncs == 3:
                raise OSError("injected rollback directory fsync failure")
        real_fsync(descriptor)

    def fail_after_both_publishes(stage: str) -> None:
        if stage == "after_projection_replace":
            raise ValueError("trigger rollback")

    monkeypatch.setattr(runner.os, "fsync", fail_first_rollback_directory_sync)
    monkeypatch.setattr(
        runner, "_provenance_transaction_hook", fail_after_both_publishes
    )
    with pytest.raises(OSError, match="rollback directory fsync failure"):
        runner._commit_entry(provenance, entry)
    assert (
        provenance.read_bytes() if provenance.exists() else None
    ) == original_jsonl
    projection = provenance.with_suffix(".txt")
    assert (projection.read_bytes() if projection.exists() else None) == original_projection


def test_pointer_rollback_failure_still_restores_both_public_views(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, "pass", outputs=()), provenance=provenance)
    original_jsonl = provenance.read_bytes()
    original_projection = provenance.with_suffix(".txt").read_bytes()
    original_pointer = (tmp_path / ".commands.jsonl.pair").read_bytes()
    real_replace = runner._atomic_replace_at

    def fail_pointer_restore(directory_fd: int, name: str, content: bytes):
        result = real_replace(directory_fd, name, content)
        if name == ".commands.jsonl.pair" and content == original_pointer:
            raise OSError("injected pointer rollback failure")
        return result

    def fail_after_pointer(stage: str) -> None:
        if stage == "after_pointer":
            raise ValueError("trigger rollback")

    monkeypatch.setattr(runner, "_atomic_replace_at", fail_pointer_restore)
    monkeypatch.setattr(runner, "_provenance_transaction_hook", fail_after_pointer)
    with pytest.raises(OSError, match="pointer rollback failure"):
        runner._commit_entry(
            provenance,
            _provenance_entry(provenance, "diagnostic.writer_a", "1"),
        )
    assert provenance.read_bytes() == original_jsonl
    assert provenance.with_suffix(".txt").read_bytes() == original_projection
    assert (tmp_path / ".commands.jsonl.pair").read_bytes() == original_pointer


def test_parent_swap_during_secure_mkdir_never_mutates_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.google_live_command_runner as runner

    safe = tmp_path / "safe"
    safe.mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-mkdir-swap"
    outside.mkdir()
    real_mkdir = runner.os.mkdir
    swapped = False

    def swapping_mkdir(name, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if name == "created" and not swapped:
            swapped = True
            safe.rename(tmp_path / "moved-safe")
            safe.symlink_to(outside, target_is_directory=True)
        return real_mkdir(name, mode, dir_fd=dir_fd)

    monkeypatch.setattr(runner.os, "mkdir", swapping_mkdir)
    with pytest.raises((ValueError, RuntimeError, OSError)):
        execute_and_record(
            _spec(tmp_path, "pass", outputs=(safe / "created" / "report.json",)),
            provenance=tmp_path / "commands.jsonl",
        )
    assert not (outside / "created").exists()


def test_concurrent_commands_safely_share_new_nested_parent(tmp_path: Path) -> None:
    provenance = tmp_path / "commands.jsonl"
    errors = []

    def run(index: int) -> None:
        output = tmp_path / "shared" / "nested" / f"report-{index}.json"
        code = f"from pathlib import Path; Path('shared/nested/report-{index}.json').write_text('ok')"
        try:
            execute_and_record(
                _spec(
                    tmp_path,
                    code,
                    command_id=f"diagnostic.nested{index}",
                    outputs=(output,),
                ),
                provenance=provenance,
            )
        except Exception as exc:  # pragma: no cover - assertion reports details
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert sorted(path.read_text() for path in (tmp_path / "shared" / "nested").iterdir()) == [
        "ok",
        "ok",
    ]


def test_rejects_cwd_symlink_to_outside_without_execution(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    escaped = outside / "escaped"
    linked = tmp_path / "linked-cwd"
    linked.symlink_to(outside, target_is_directory=True)
    spec = _spec(
        tmp_path,
        f"from pathlib import Path; Path({str(escaped)!r}).write_text('bad')",
        cwd=linked,
        outputs=(),
    )
    with pytest.raises((ValueError, RuntimeError)):
        execute_and_record(spec, provenance=tmp_path / "commands.jsonl")
    assert not escaped.exists()


def test_cwd_parent_swap_before_spawn_fails_without_outside_execution(tmp_path: Path) -> None:
    cwd = tmp_path / "safe-cwd"
    cwd.mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-outside-swap"
    outside.mkdir()
    escaped = outside / "escaped"

    def swap() -> None:
        cwd.rename(tmp_path / "moved-cwd")
        cwd.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="cwd changed"):
        execute_and_record(
            _spec(
                tmp_path,
                f"from pathlib import Path; Path({str(escaped)!r}).write_text('bad')",
                cwd=cwd,
                outputs=(),
            ),
            provenance=tmp_path / "commands.jsonl",
            _before_spawn=swap,
        )
    assert not escaped.exists()


def test_nonzero_exit_is_recorded_only_when_expected(tmp_path: Path) -> None:
    expected = _spec(tmp_path, "raise SystemExit(7)", outputs=(), expected_exit_codes=(7,))
    result = execute_and_record(expected, provenance=tmp_path / "commands.jsonl")
    assert result.exit_code == 7
    assert result.classification == "expected_exit"

    unexpected = _spec(tmp_path, "raise SystemExit(8)", outputs=(), expected_exit_codes=(0,))
    result = execute_and_record(unexpected, provenance=tmp_path / "other.jsonl")
    assert result.classification == "unexpected_exit"


def test_spawn_failure_does_not_publish_provenance(tmp_path: Path) -> None:
    spec = _spec(tmp_path, "pass", argv=(str(tmp_path / "missing"),), outputs=())
    with pytest.raises(RuntimeError, match="spawn_failed"):
        execute_and_record(spec, provenance=tmp_path / "commands.jsonl")
    assert not (tmp_path / "commands.jsonl").exists()


def test_timeout_terminates_owned_process_group_and_records_safe_classification(tmp_path: Path) -> None:
    child = tmp_path / "child.pid"
    spec = _spec(
        tmp_path,
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        f"open({str(child)!r},'w').write(str(p.pid)); time.sleep(30)",
        outputs=(),
        timeout_sec=0.2,
    )
    result = execute_and_record(spec, provenance=tmp_path / "commands.jsonl")
    assert result.classification == "timeout"
    pid = int(child.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_cancellation_terminates_owned_process_and_records_classification(tmp_path: Path) -> None:
    cancel = threading.Event()
    timer = threading.Timer(0.1, cancel.set)
    timer.start()
    try:
        result = execute_and_record(
            _spec(tmp_path, "import time; time.sleep(30)", outputs=()),
            provenance=tmp_path / "commands.jsonl",
            cancel_event=cancel,
        )
    finally:
        timer.cancel()
    assert result.classification == "cancelled"
    assert result.policy_satisfied is False


def test_keyboard_interrupt_cleans_group_and_records_terminal_state(monkeypatch, tmp_path: Path) -> None:
    import scripts.google_live_command_runner as runner

    real = runner.subprocess.Popen.communicate
    calls = 0

    def interrupt_once(process, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise KeyboardInterrupt
        return real(process, *args, **kwargs)

    monkeypatch.setattr(runner.subprocess.Popen, "communicate", interrupt_once)
    with pytest.raises(KeyboardInterrupt):
        execute_and_record(
            _spec(tmp_path, "import time; time.sleep(30)", outputs=()),
            provenance=tmp_path / "commands.jsonl",
        )
    entry = json.loads((tmp_path / "commands.jsonl").read_text())
    assert entry["terminalPolicy"]["classification"] == "keyboard_interrupt"


def test_missing_or_mutated_output_fails_without_provenance(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="output"):
        execute_and_record(_spec(tmp_path, "pass"), provenance=tmp_path / "commands.jsonl")
    assert not (tmp_path / "commands.jsonl").exists()


def test_duplicate_ids_fail_closed_and_corrupt_public_log_is_repaired(
    tmp_path: Path,
) -> None:
    import scripts.google_live_command_runner as runner

    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, _write_report_code()), provenance=provenance)
    (tmp_path / "real-api" / "report.json").unlink()
    with pytest.raises(ValueError, match="duplicate"):
        execute_and_record(_spec(tmp_path, _write_report_code()), provenance=provenance)
    provenance.write_text('{"truncated":')
    execute_and_record(
        _spec(tmp_path, _write_report_code(), command_id="diagnostic.retry"),
        provenance=provenance,
    )
    assert [
        entry["commandId"]
        for entry in runner.parse_provenance(provenance.read_bytes())
    ] == ["real_api.round_trip", "diagnostic.retry"]


def test_concurrent_writers_preserve_both_complete_entries(tmp_path: Path) -> None:
    provenance = tmp_path / "commands.jsonl"
    errors = []

    def run(index: int) -> None:
        try:
            execute_and_record(
                _spec(
                    tmp_path,
                    f"from pathlib import Path; p=Path('out{index}'); p.write_text('ok')",
                    command_id=f"diagnostic.command{index}",
                    outputs=(tmp_path / f"out{index}",),
                ),
                provenance=provenance,
            )
        except Exception as exc:  # pragma: no cover - assertion reports details
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert {json.loads(line)["commandId"] for line in provenance.read_text().splitlines()} == {
        "diagnostic.command0",
        "diagnostic.command1",
    }
    assert (tmp_path / "commands.txt").read_text().count("diagnostic.") == 2


def test_inconsistent_projection_is_repaired_from_committed_generation(
    tmp_path: Path,
) -> None:
    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, _write_report_code()), provenance=provenance)
    (tmp_path / "commands.txt").write_text("tampered\n")
    execute_and_record(
        _spec(tmp_path, "pass", command_id="diagnostic.retry", outputs=()),
        provenance=provenance,
    )
    import scripts.google_live_command_runner as runner

    entries = runner.parse_provenance(provenance.read_bytes())
    assert (tmp_path / "commands.txt").read_bytes() == runner.render_commands_projection(
        entries
    )
