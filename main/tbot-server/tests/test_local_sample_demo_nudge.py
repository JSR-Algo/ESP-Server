import importlib.util
import json
from urllib.error import URLError
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "local_sample_demo_nudge.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("local_sample_demo_nudge", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_refuses_non_loopback_base_url():
    module = _load_script()

    with pytest.raises(SystemExit) as exc:
        module.nudge(
            device_id="28:84:85:85:1a:80",
            base_url="http://192.168.0.104:8003",
            open_url=lambda *args, **kwargs: None,
        )

    assert exc.value.code == 2


def test_refuses_when_local_metrics_do_not_contain_target_device():
    module = _load_script()
    calls = []

    def open_url(url, *, method="GET", headers=None, timeout=5):
        calls.append((url, method, headers))
        return json.dumps({"connections": 0, "devices": []}).encode()

    with pytest.raises(SystemExit) as exc:
        module.nudge(
            device_id="28:84:85:85:1a:80",
            base_url="http://127.0.0.1:8003",
            open_url=open_url,
        )

    assert exc.value.code == 3
    assert calls == [
        ("http://127.0.0.1:8003/internal/lesson-runtime/metrics", "GET", None)
    ]


def test_refuses_when_local_metrics_payload_is_not_an_object():
    module = _load_script()
    calls = []

    def open_url(url, *, method="GET", headers=None, timeout=5):
        calls.append((url, method, headers))
        return json.dumps([{"deviceId": "28:84:85:85:1a:80"}]).encode()

    with pytest.raises(SystemExit) as exc:
        module.nudge(
            device_id="28:84:85:85:1a:80",
            base_url="http://127.0.0.1:8003",
            open_url=open_url,
        )

    assert exc.value.code == 3
    assert calls == [
        ("http://127.0.0.1:8003/internal/lesson-runtime/metrics", "GET", None)
    ]


def test_refuses_when_local_metrics_devices_is_not_a_list():
    module = _load_script()
    calls = []

    def open_url(url, *, method="GET", headers=None, timeout=5):
        calls.append((url, method, headers))
        return json.dumps({"connections": 1, "devices": None}).encode()

    with pytest.raises(SystemExit) as exc:
        module.nudge(
            device_id="28:84:85:85:1a:80",
            base_url="http://127.0.0.1:8003",
            open_url=open_url,
        )

    assert exc.value.code == 3
    assert calls == [
        ("http://127.0.0.1:8003/internal/lesson-runtime/metrics", "GET", None)
    ]


def test_refuses_when_local_metrics_are_unreachable():
    module = _load_script()

    def open_url(url, *, method="GET", headers=None, timeout=5):
        raise URLError("connection refused")

    with pytest.raises(SystemExit) as exc:
        module.nudge(
            device_id="28:84:85:85:1a:80",
            base_url="http://127.0.0.1:8003",
            open_url=open_url,
        )

    assert exc.value.code == 4


def test_posts_loopback_nudge_only_after_target_is_present():
    module = _load_script()
    calls = []

    def open_url(url, *, method="GET", headers=None, timeout=5):
        calls.append((url, method, headers))
        if url.endswith("/metrics"):
            return json.dumps(
                {
                    "connections": 1,
                    "devices": [{"deviceId": "28:84:85:85:1a:80"}],
                }
            ).encode()
        return json.dumps({"data": {"nudged": True, "mode": "sample"}}).encode()

    result = module.nudge(
        device_id="28:84:85:85:1a:80",
        base_url="http://127.0.0.1:8003",
        open_url=open_url,
    )

    assert result == {"data": {"nudged": True, "mode": "sample"}}
    assert calls == [
        ("http://127.0.0.1:8003/internal/lesson-runtime/metrics", "GET", None),
        (
            "http://127.0.0.1:8003/internal/devices/28:84:85:85:1a:80/lesson-nudge",
            "POST",
            {"X-TBOT-Local-Sample-Demo": "1"},
        ),
    ]

def test_posts_loopback_nudge_with_normalized_target_identity():
    module = _load_script()
    calls = []

    def open_url(url, *, method="GET", headers=None, timeout=5):
        calls.append((url, method, headers))
        if url.endswith("/metrics"):
            return json.dumps(
                {
                    "connections": 1,
                    "devices": [{"deviceId": "  28:84:85:85:1A:80\n"}],
                }
            ).encode()
        return json.dumps({"data": {"nudged": True, "mode": "sample"}}).encode()

    result = module.nudge(
        device_id="\t28:84:85:85:1A:80 ",
        base_url="http://127.0.0.1:8003/",
        open_url=open_url,
    )

    assert result == {"data": {"nudged": True, "mode": "sample"}}
    assert calls[-1] == (
        "http://127.0.0.1:8003/internal/devices/28:84:85:85:1a:80/lesson-nudge",
        "POST",
        {"X-TBOT-Local-Sample-Demo": "1"},
    )
