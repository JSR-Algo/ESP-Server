from pathlib import Path

import yaml


PROJECT_DIR = Path(__file__).resolve().parents[1]


def test_manager_api_template_exposes_tbot_connect_public_endpoints():
    template = yaml.safe_load((PROJECT_DIR / "config_from_api.yaml").read_text())

    server = template["server"]

    assert server["websocket"] == "wss://freebsd-concern-noon-cement.trycloudflare.com/tbot/v1/"

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

    assert server["vision_explain"] == "https://carefully-freelance-improving-numerical.trycloudflare.com/mcp/vision/explain"
    assert template["manager-api"]["url"] == "http://tbot-esp32-server-web:8002/tbot"
