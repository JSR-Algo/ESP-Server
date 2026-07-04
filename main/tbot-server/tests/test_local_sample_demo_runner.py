import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "local_sample_demo_runner.py"


class _Server:
    def __init__(self):
        self.terminated = False
        self.waited = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited = True


class _StubbornServer(_Server):
    def __init__(self):
        super().__init__()
        self.killed = False

    def wait(self, timeout=None):
        self.waited = True
        raise TimeoutError("still running")

    def kill(self):
        self.killed = True


def _load_script():
    spec = importlib.util.spec_from_file_location("local_sample_demo_runner", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_guarded_runner_nudges_only_after_local_preflight_ready():
    module = _load_script()
    server = _Server()
    calls = []

    result = module.run_guarded(
        device_id="28:84:85:85:1a:80",
        lan_ip="192.168.0.104",
        ws_port=8000,
        http_port=8003,
        wait_seconds=10,
        poll_seconds=1,
        start_server=lambda **kwargs: server,
        run_preflight=lambda **kwargs: {"canNudgeLocal": True, "status": "LOCAL_NUDGE_READY"},
        run_nudge=lambda **kwargs: calls.append(kwargs) or {"data": {"nudged": True}},
        sleep=lambda seconds: None,
    )

    assert result["status"] == "NUDGED"
    assert calls == [
        {
            "device_id": "28:84:85:85:1a:80",
            "base_url": "http://127.0.0.1:8003",
        }
    ]
    assert server.terminated is True
    assert server.waited is True


def test_guarded_runner_never_nudges_when_preflight_is_not_ready():
    module = _load_script()
    server = _Server()
    nudges = []

    result = module.run_guarded(
        device_id="28:84:85:85:1a:80",
        lan_ip="192.168.0.104",
        ws_port=8000,
        http_port=8003,
        wait_seconds=3,
        poll_seconds=1,
        start_server=lambda **kwargs: server,
        run_preflight=lambda **kwargs: {
            "canNudgeLocal": False,
            "status": "TARGET_PUBLIC_ONLY",
        },
        run_nudge=lambda **kwargs: nudges.append(kwargs),
        sleep=lambda seconds: None,
    )

    assert result["status"] == "NOT_READY"
    assert result["lastPreflight"]["status"] == "TARGET_PUBLIC_ONLY"
    assert nudges == []
    assert server.terminated is True
    assert server.waited is True


def test_guarded_runner_polls_through_non_divisible_wait_window():
    module = _load_script()
    server = _Server()
    attempts = []

    def preflight(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 3:
            return {"canNudgeLocal": True, "status": "LOCAL_NUDGE_READY"}
        return {"canNudgeLocal": False, "status": "TARGET_NOT_CONNECTED"}

    result = module.run_guarded(
        device_id="28:84:85:85:1a:80",
        lan_ip="192.168.0.104",
        ws_port=8000,
        http_port=8003,
        wait_seconds=5,
        poll_seconds=2,
        start_server=lambda **kwargs: server,
        run_preflight=preflight,
        run_nudge=lambda **kwargs: {"data": {"nudged": True}},
        sleep=lambda seconds: None,
    )

    assert result["status"] == "NUDGED"
    assert len(attempts) == 3
    assert server.terminated is True
    assert server.waited is True


def test_guarded_runner_builds_server_command_with_lan_endpoint():
    module = _load_script()

    command = module.build_server_command(
        lan_ip="192.168.0.104", ws_port=8000, http_port=8003
    )

    assert command[-6:] == [
        "--lan-ip",
        "192.168.0.104",
        "--ws-port",
        "8000",
        "--http-port",
        "8003",
    ]
    assert command[1].endswith("local_sample_demo_server.py")


def test_start_server_suppresses_child_output(monkeypatch):
    module = _load_script()
    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _Server()

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)

    module.start_server(lan_ip="192.168.0.104", ws_port=8000, http_port=8003)

    assert captured["kwargs"]["stdout"] == module.subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] == module.subprocess.DEVNULL


def test_start_server_isolates_process_group(monkeypatch):
    module = _load_script()
    captured = {}

    def fake_popen(command, **kwargs):
        captured["kwargs"] = kwargs
        return _Server()

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)

    module.start_server(lan_ip="192.168.0.104", ws_port=8000, http_port=8003)

    assert captured["kwargs"]["start_new_session"] is True


def test_stop_server_terminates_process_group_before_launcher_fallback(monkeypatch):
    module = _load_script()
    server = _Server()
    server.pid = 12345
    signals = []

    monkeypatch.setattr(module.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    module._stop_server(server)

    assert signals == [(12345, module.signal.SIGTERM)]
    assert server.terminated is False
    assert server.waited is True


def test_guarded_runner_kills_server_when_terminate_wait_times_out():
    module = _load_script()
    server = _StubbornServer()

    result = module.run_guarded(
        device_id="28:84:85:85:1a:80",
        lan_ip="192.168.0.104",
        ws_port=8000,
        http_port=8003,
        wait_seconds=1,
        poll_seconds=1,
        start_server=lambda **kwargs: server,
        run_preflight=lambda **kwargs: {"canNudgeLocal": False, "status": "NOT_READY"},
        run_nudge=lambda **kwargs: {"data": {"nudged": True}},
        sleep=lambda seconds: None,
    )

    assert result["status"] == "NOT_READY"
    assert server.terminated is True
    assert server.waited is True
    assert server.killed is True
