from pathlib import Path

import yaml


PROJECT_DIR = Path(__file__).resolve().parents[1]


def test_manager_api_template_exposes_tbot_connect_public_endpoints():
    template = yaml.safe_load((PROJECT_DIR / "config_from_api.yaml").read_text())

    server = template["server"]

    # WebSocket stays a deliberate placeholder: the live tunnel/domain is
    # supplied by deployment config or Admin Parameter Management, never baked
    # into this template (ephemeral trycloudflare URLs must not be committed).
    assert server["websocket"] == "ws://your-ip-or-domain:port/tbot/v1/"
    assert "trycloudflare.com/tbot/v1/" not in server["websocket"]

    # api_url is the firmware-facing backend base. Per the locked ownership
    # decision it points at the NestJS backend (/v1 prefix kept so firmware can
    # append /device/config and /claim/confirm). It must NOT be a bare
    # placeholder, and must carry the Nest route prefix.
    api_url = server["api_url"]
    assert api_url.endswith("/v1"), api_url
    assert "your-backend-api-domain" not in api_url
    assert "your-api-domain" not in api_url
    assert api_url.startswith("https://"), api_url
    # Current default is the onrender Nest backend; override per deployment.
    assert api_url == "https://tbot-backend-8wmh.onrender.com/v1"
