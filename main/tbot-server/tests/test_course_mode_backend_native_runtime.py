import importlib
import json
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest


def fake_docker(monkeypatch, *, wrong_image=False, cleanup_failure=False, start_failure=False):
    module = importlib.import_module("scripts.course_mode_backend_native_runtime")
    calls = []
    state = {"exists": False, "name": "", "label": ""}
    image = "sha256:" + "a" * 64
    cid = "b" * 64

    def run(command, **kwargs):
        args = list(command)[1:]
        calls.append((args, kwargs.get("env", {})))
        code, output = 0, ""
        if args[0] == "create":
            state.update(exists=True, name=args[args.index("--name") + 1], label=args[args.index("--label") + 1])
            output = cid
        elif args[0] == "start":
            code = 1 if start_failure else 0
        elif args[0] == "inspect":
            if not state["exists"]:
                code = 1
            else:
                key, value = state["label"].split("=", 1)
                output = json.dumps([{"Id": cid, "Image": "wrong" if wrong_image else image,
                    "Config": {"Labels": {key: value}},
                    "NetworkSettings": {"Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "54321"}]}}}])
        elif args[0] == "rm":
            code = 1 if cleanup_failure else 0
            if not code:
                state["exists"] = False
        return SimpleNamespace(returncode=code, stdout=output, stderr="")

    monkeypatch.setattr(module, "_run", run)
    return module, calls, state, image


def test_owned_database_uses_pinned_image_and_removes_only_own_container(monkeypatch):
    module, calls, state, image = fake_docker(monkeypatch)
    with module.OwnedPostgres("/trusted/docker", image, {}) as database:
        assert database.url.startswith("postgresql://postgres:")
        assert "@127.0.0.1:54321/postgres" in database.url
        create, environment = next(item for item in calls if item[0][0] == "create")
        assert create[-1] == image
        assert create[create.index("--pull") + 1] == "never"
        assert "127.0.0.1::5432" in create
        assert environment["POSTGRES_PASSWORD"] not in " ".join(create)
    assert not state["exists"]
    assert [args for args, _ in calls if args[0] == "rm"] == [["rm", "--force", "--volumes", "b" * 64]]


@pytest.mark.parametrize("failure", ["wrong_image", "start_failure"])
def test_failed_preparation_cleans_owned_database(monkeypatch, failure):
    module, _, state, image = fake_docker(monkeypatch, **{failure: True})
    with pytest.raises(module.NativePrerequisiteError):
        with module.OwnedPostgres("/trusted/docker", image, {}):
            pytest.fail("failed prerequisite admitted")
    assert not state["exists"]


def test_test_exception_cleans_owned_database(monkeypatch):
    module, _, state, image = fake_docker(monkeypatch)
    with pytest.raises(TimeoutError):
        with module.OwnedPostgres("/trusted/docker", image, {}):
            raise TimeoutError()
    assert not state["exists"]


def test_failed_cleanup_never_reports_success(monkeypatch):
    module, _, state, image = fake_docker(monkeypatch, cleanup_failure=True)
    with pytest.raises(module.NativeCleanupError):
        with module.OwnedPostgres("/trusted/docker", image, {}):
            pass
    assert state["exists"]


@pytest.mark.parametrize("command", ["inspect", "rm"])
@pytest.mark.parametrize("exception", [OSError("unavailable"), subprocess.TimeoutExpired("docker", 30)])
def test_cleanup_transport_failure_keeps_owned_identity(monkeypatch, command, exception):
    module, _, _, image = fake_docker(monkeypatch)
    database = module.OwnedPostgres("/trusted/docker", image, {})
    database.__enter__()
    original = module._run

    def fail(arguments, **kwargs):
        if arguments[1] == command:
            raise exception
        return original(arguments, **kwargs)

    monkeypatch.setattr(module, "_run", fail)
    with pytest.raises(module.NativeCleanupError, match=database.name):
        database.close()


@pytest.mark.parametrize("cleanup_ok", [True, False])
def test_interruption_retains_failed_scratch_cleanup(tmp_path, monkeypatch, cleanup_ok):
    from scripts import course_mode_release_gate as gate
    monkeypatch.setattr(gate.shutil, "disk_usage", lambda _: SimpleNamespace(total=1024**3, free=1024**3))

    def interrupt(_):
        raise KeyboardInterrupt()

    monkeypatch.setattr(gate._manifest, "python_test_runtime_authorized", interrupt)
    if not cleanup_ok:
        monkeypatch.setattr(gate, "_remove_owned_tree", lambda *_: False)
    expected = KeyboardInterrupt if cleanup_ok else gate.RetainedStagingError
    with pytest.raises(expected) as error:
        gate._run_backend_native_tests([], {"tools": {"pythonTestRuntime": {}}},
            {"COURSE_MODE_NATIVE_TEST_SCRATCH_ROOT": str(tmp_path)}, tmp_path, 1, 1024)
    if cleanup_ok:
        assert list(tmp_path.iterdir()) == []
    else:
        assert len(error.value.paths) == 1
        assert Path(error.value.paths[0]).is_dir()
