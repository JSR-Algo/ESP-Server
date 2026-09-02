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
