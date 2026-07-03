import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "local_sample_demo_server.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("local_sample_demo_server", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_config_uses_in_memory_local_demo_posture():
    module = _load_script()

    config = module.build_config(lan_ip="192.168.0.104", ws_port=8000, http_port=8003)

    assert config["read_config_from_api"] is True
    assert config["selected_module"] == {}
    assert config["server"]["ip"] == "0.0.0.0"
    assert config["server"]["port"] == 8000
    assert config["server"]["http_port"] == 8003
    assert config["server"]["websocket"] == "ws://192.168.0.104:8000/tbot/v1/"
    assert config["server"]["auth"] == {"enabled": False}
    assert config["voice_mode"] == {
        "type": "google_live",
        "fallback_to_classic_on_error": False,
    }
    assert "manager-api" not in config
    assert config["lesson"]["runtime_enabled"] is False
    assert config["lesson"]["sample_lesson"] is True
    assert config["lesson"]["sample_mode"] == "interactive"


def test_status_lines_include_loopback_nudge_and_lan_ota_urls():
    module = _load_script()

    lines = module.status_lines(lan_ip="192.168.0.104", ws_port=8000, http_port=8003)

    assert "local_http_loopback=http://127.0.0.1:8003" in lines
    assert "local_http_lan=http://192.168.0.104:8003" in lines
    assert "local_ws_advertised=ws://192.168.0.104:8000/tbot/v1/" in lines
    assert (
        "local_nudge_url=http://127.0.0.1:8003/internal/devices/{deviceId}/lesson-nudge"
        in lines
    )


def test_ensure_project_root_on_path_supports_direct_script_execution(monkeypatch):
    module = _load_script()
    root = str(SCRIPT.parents[1])
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry != root])

    module.ensure_project_root_on_path()

    assert sys.path[0] == root
