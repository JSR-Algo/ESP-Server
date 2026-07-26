from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_TEMPLATE = REPO_ROOT / "deploy/cloudflared/config.yml.example"


def _ingress_rules() -> list[dict[str, str]]:
    rules: list[dict[str, str]] = []
    in_ingress = False
    for raw_line in CONFIG_TEMPLATE.read_text(encoding="utf-8").splitlines():
        if raw_line == "ingress:":
            in_ingress = True
            continue
        if not in_ingress or not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        stripped = raw_line.strip()
        if stripped.startswith("- "):
            rules.append({})
            stripped = stripped[2:]
        key, value = stripped.split(":", 1)
        rules[-1][key] = value.strip().strip('"\'')
    return rules


def _rule_index(rules: list[dict[str, str]], hostname: str, path: str | None = None) -> int:
    for index, rule in enumerate(rules):
        if rule.get("hostname") == hostname and rule.get("path") == path:
            return index
    raise AssertionError(f"missing ingress rule hostname={hostname!r} path={path!r}")


def test_public_generation_reads_reach_host_nginx_before_hostname_catch_alls():
    rules = _ingress_rules()
    nginx_service = "http://127.0.0.1"

    required_routes = (
        ("admin.tjbot.vn", "^/v1/public/lesson-assets/latest$"),
        ("admin.tjbot.vn", "^/public/lesson-assets/generation$"),
        ("esp.tjbot.vn", "^/v1/public/lesson-assets/latest$"),
    )
    for hostname, path in required_routes:
        route_index = _rule_index(rules, hostname, path)
        catch_all_index = _rule_index(rules, hostname)
        assert rules[route_index]["service"] == nginx_service
        assert route_index < catch_all_index

    admin_catch_all_index = _rule_index(rules, "admin.tjbot.vn")
    assert rules[admin_catch_all_index]["service"] == "http://127.0.0.1:8002"

    esp_generation_path = "^/public/lesson-assets/generation$"
    assert not any(
        rule.get("hostname") == "esp.tjbot.vn" and rule.get("path") == esp_generation_path
        for rule in rules
    )


def test_existing_esp_routes_and_terminal_404_are_preserved():
    rules = _ingress_rules()
    expected_routes = (
        ("^/lesson-sample-assets/.*", "http://127.0.0.1"),
        ("^/tbot/ota/.*", "http://127.0.0.1:8003"),
        ("^/internal/.*", "http://127.0.0.1:8003"),
        ("^/mcp/vision/.*", "http://127.0.0.1:8003"),
        ("^/tbot/v1/.*", "http://127.0.0.1:8000"),
    )

    esp_catch_all_index = _rule_index(rules, "esp.tjbot.vn")
    for path, service in expected_routes:
        route_index = _rule_index(rules, "esp.tjbot.vn", path)
        assert rules[route_index]["service"] == service
        assert route_index < esp_catch_all_index

    assert rules[esp_catch_all_index]["service"] == "http://127.0.0.1:8003"
    assert rules[-1] == {"service": "http_status:404"}


def test_template_contains_placeholders_instead_of_tunnel_credentials():
    raw = CONFIG_TEMPLATE.read_text(encoding="utf-8")

    assert "<TUNNEL_UUID>" in raw
    assert "<CREDENTIALS_FILE>" in raw
    assert "credentials-file: /root/.cloudflared/" not in raw
