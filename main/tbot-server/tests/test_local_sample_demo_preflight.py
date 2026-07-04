import importlib.util
import json
from pathlib import Path
from urllib.error import URLError

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "local_sample_demo_preflight.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("local_sample_demo_preflight", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_classifies_ready_only_when_target_is_in_local_metrics():
    module = _load_script()

    result = module.classify(
        device_id="28:84:85:85:1a:80",
        usb_ports=["/dev/cu.usbmodem1101 303A:1001 28:84:85:85:1A:80"],
        local_metrics={"connections": 1, "devices": [{"deviceId": "28:84:85:85:1a:80"}]},
        public_metrics={"connections": 0, "devices": []},
        local_error=None,
        public_error=None,
    )

    assert result["status"] == "LOCAL_NUDGE_READY"
    assert result["canNudgeLocal"] is True
    assert result["nextAction"] == "run_local_sample_demo_nudge"


def test_classifies_public_only_when_usb_and_public_metrics_have_target_but_local_does_not():
    module = _load_script()

    result = module.classify(
        device_id="28:84:85:85:1a:80",
        usb_ports=["/dev/cu.usbmodem1101 303A:1001 28:84:85:85:1A:80"],
        local_metrics={"connections": 0, "devices": []},
        public_metrics={"connections": 1, "devices": [{"deviceId": "28:84:85:85:1a:80"}]},
        local_error=None,
        public_error=None,
    )

    assert result["status"] == "TARGET_PUBLIC_ONLY"
    assert result["canNudgeLocal"] is False
    assert result["nextAction"] == "approve_endpoint_redirect_or_production_nudge"


def test_classifies_local_server_unreachable_before_any_nudge():
    module = _load_script()

    result = module.classify(
        device_id="28:84:85:85:1a:80",
        usb_ports=["/dev/cu.usbmodem1101 303A:1001 28:84:85:85:1A:80"],
        local_metrics=None,
        public_metrics={"connections": 0, "devices": []},
        local_error="connection refused",
        public_error=None,
    )

    assert result["status"] == "LOCAL_SERVER_UNREACHABLE"
    assert result["canNudgeLocal"] is False


def test_classifies_usb_absent_before_endpoint_actions():
    module = _load_script()

    result = module.classify(
        device_id="28:84:85:85:1a:80",
        usb_ports=["/dev/cu.Bluetooth-Incoming-Port"],
        local_metrics={"connections": 0, "devices": []},
        public_metrics={"connections": 0, "devices": []},
        local_error=None,
        public_error=None,
    )

    assert result["status"] == "TARGET_USB_ABSENT"
    assert result["canNudgeLocal"] is False


def test_classifies_malformed_metrics_devices_as_target_absent():
    module = _load_script()

    result = module.classify(
        device_id="28:84:85:85:1a:80",
        usb_ports=["/dev/cu.usbmodem1101 303A:1001 28:84:85:85:1A:80"],
        local_metrics={"connections": 1, "devices": None},
        public_metrics={"connections": 1, "devices": {"deviceId": "28:84:85:85:1a:80"}},
        local_error=None,
        public_error=None,
    )

    assert result["status"] == "TARGET_NOT_CONNECTED"
    assert result["canNudgeLocal"] is False
    assert result["localTargetPresent"] is False
    assert result["publicTargetPresent"] is False


def test_get_json_reports_error_without_throwing():
    module = _load_script()

    def open_url(url, *, timeout=5):
        raise URLError("connection refused")

    payload, error = module.get_json("http://127.0.0.1:8003/internal/lesson-runtime/metrics", open_url=open_url)

    assert payload is None
    assert "connection refused" in error


def test_main_prints_json_and_returns_not_ready(monkeypatch, capsys):
    module = _load_script()

    monkeypatch.setattr(
        module,
        "list_usb_ports",
        lambda: ["/dev/cu.usbmodem1101 USB VID:PID=303A:1001 SER=28:84:85:85:1A:80"],
    )

    def open_url(url, *, timeout=5):
        url = url.full_url if hasattr(url, "full_url") else url
        if "127.0.0.1" in url:
            raise URLError("connection refused")
        return json.dumps(
            {"connections": 1, "devices": [{"deviceId": "28:84:85:85:1a:80"}]}
        ).encode()

    rc = module.main(["--device-id", "28:84:85:85:1a:80"], open_url=open_url)

    output = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert output["status"] == "TARGET_PUBLIC_ONLY"
    assert output["publicTargetPresent"] is True

def test_main_sends_device_affinity_headers_to_public_metrics(monkeypatch, capsys):
    module = _load_script()
    seen_public_headers = {}

    monkeypatch.setattr(
        module,
        "list_usb_ports",
        lambda: ["/dev/cu.usbmodem1101 USB VID:PID=303A:1001 SER=28:84:85:85:1A:80"],
    )

    def open_url(request, *, timeout=5):
        url = request.full_url if hasattr(request, "full_url") else request
        if "127.0.0.1" in url:
            raise URLError("connection refused")
        seen_public_headers.update(dict(request.header_items()))
        return json.dumps(
            {"connections": 1, "devices": [{"deviceId": "28:84:85:85:1a:80"}]}
        ).encode()

    rc = module.main(["--device-id", "28:84:85:85:1a:80"], open_url=open_url)

    output = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert output["status"] == "TARGET_PUBLIC_ONLY"
    assert seen_public_headers["Device-id"] == "28:84:85:85:1a:80"
    assert seen_public_headers["X-device-id"] == "28:84:85:85:1a:80"
