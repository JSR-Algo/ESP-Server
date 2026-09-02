import json
import os
import signal
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


def test_duplicate_ids_and_corrupt_existing_log_fail_closed(tmp_path: Path) -> None:
    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, _write_report_code()), provenance=provenance)
    (tmp_path / "real-api" / "report.json").unlink()
    with pytest.raises(ValueError, match="duplicate"):
        execute_and_record(_spec(tmp_path, _write_report_code()), provenance=provenance)
    provenance.write_text('{"truncated":')
    with pytest.raises(ValueError, match="provenance"):
        execute_and_record(
            _spec(tmp_path, _write_report_code(), command_id="diagnostic.retry"),
            provenance=provenance,
        )


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


def test_inconsistent_projection_fails_closed(tmp_path: Path) -> None:
    provenance = tmp_path / "commands.jsonl"
    execute_and_record(_spec(tmp_path, _write_report_code()), provenance=provenance)
    (tmp_path / "commands.txt").write_text("tampered\n")
    with pytest.raises(ValueError, match="projection"):
        execute_and_record(
            _spec(tmp_path, "pass", command_id="diagnostic.retry", outputs=()),
            provenance=provenance,
        )
