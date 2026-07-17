from __future__ import annotations

import ast
import io
import re
import textwrap
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_MAPPING_RE = re.compile(
    r"@(?P<kind>RequestMapping|GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping)"
    r"\s*(?:\((?P<args>[^)]*)\))?",
    re.DOTALL,
)
_STRING_LITERAL_RE = re.compile(r'"([^"]*)"')
_MAPPING_ATTRIBUTE_RE = re.compile(
    r"\b(?:path|value)\s*=\s*(?P<value>\{[^}]*\}|\"[^\"]*\")",
    re.DOTALL,
)
_CREDENTIAL_DECL_RE = re.compile(
    r"\b(?:String|char\s*\[\s*\]|byte\s*\[\s*\])\s+(ssid|password|wifiSsid|wifiPassword)\b",
    re.IGNORECASE,
)
_JAVA_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_FORBIDDEN_MARKERS = (
    "/device/provision",
    "device.provisionWifi",
    "wifiPassword",
    "wifiSsid",
)
_NON_PRODUCTION_DIRS = {
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "site-packages",
    "test",
    "tests",
    "vendor",
    "venv",
}


def _mapping_paths(match: re.Match[str]) -> list[str]:
    args = match.group("args") or ""
    attributes = _MAPPING_ATTRIBUTE_RE.findall(args)
    values = attributes if attributes else [args]
    paths = [literal for value in values for literal in _STRING_LITERAL_RE.findall(value)]
    return paths or [""]


def _join_route(prefix: str, suffix: str) -> str:
    segments = [segment.strip("/") for segment in (prefix, suffix) if segment.strip("/")]
    return "/" + "/".join(segments)


def _strip_comments(filename: str, source: str) -> str:
    if not filename.endswith(".py"):
        return _JAVA_COMMENT_RE.sub("", source)

    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        return tokenize.untokenize(
            (token.type, "" if token.type == tokenize.COMMENT else token.string) for token in tokens
        )
    except (IndentationError, tokenize.TokenError, ValueError):
        return source


def _python_call_route(call: ast.Call) -> str | None:
    route: ast.expr | None = call.args[0] if call.args else None
    if route is None:
        route = next(
            (keyword.value for keyword in call.keywords if keyword.arg in {"path", "route", "value"}),
            None,
        )
    return route.value if isinstance(route, ast.Constant) and isinstance(route.value, str) else None


def _python_route(decorator: ast.expr) -> str | None:
    if not isinstance(decorator, ast.Call):
        return None
    function = decorator.func
    if not isinstance(function, ast.Attribute) or function.attr.lower() not in {
        "delete",
        "get",
        "patch",
        "post",
        "put",
        "route",
    }:
        return None
    return _python_call_route(decorator)


def _is_wifi_provision_route(route: str) -> bool:
    normalized = route.lower().rstrip("/")
    return normalized == "/device/provision" or (
        "provision" in normalized and any(segment in normalized for segment in ("/wifi", "/wi-fi"))
    )


def _find_python_credential_handlers(filename: str, source: str) -> list[str]:
    try:
        tree = ast.parse(textwrap.dedent(source))
    except (IndentationError, SyntaxError, ValueError):
        return []

    violations: list[str] = []
    routes_by_handler: dict[str, list[str]] = {}
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        function = call.func
        route = _python_call_route(call)
        if (
            isinstance(function, ast.Attribute)
            and function.attr.lower() in {"delete", "get", "patch", "post", "put", "route"}
            and route is not None
            and _is_wifi_provision_route(route)
        ):
            violations.append(f"{filename}: route {route.lower()}")
        if (
            isinstance(function, ast.Attribute)
            and function.attr.lower() in {"delete", "get", "patch", "post", "put", "route"}
            and route is not None
            and len(call.args) >= 2
            and isinstance(call.args[1], ast.Name)
        ):
            routes_by_handler.setdefault(call.args[1].id, []).append(route)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        routes = [route for decorator in node.decorator_list if (route := _python_route(decorator))]
        routes.extend(routes_by_handler.get(node.name, []))
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        identifiers = {argument.arg.lower() for argument in arguments}
        identifiers.update(child.id.lower() for child in ast.walk(node) if isinstance(child, ast.Name))
        identifiers.update(
            child.value.lower()
            for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        )
        credentials = identifiers & {
            "password",
            "ssid",
            "wifi_password",
            "wifi_ssid",
            "wifipassword",
            "wifissid",
        }
        provisioning_context = "provision" in node.name.lower() or any("provision" in route.lower() for route in routes)
        explicit = credentials & {"wifi_password", "wifi_ssid", "wifipassword", "wifissid"}
        generic_pair = credentials & {"password", "ssid"} == {"password", "ssid"}
        if not explicit and not (provisioning_context and generic_pair):
            continue
        for route in routes:
            violations.append(f"{filename}: route {route.lower()}")
        for credential in sorted(credentials):
            violations.append(f"{filename}: {node.name} declares {credential}")
    return violations


def _is_non_production_dir(part: str) -> bool:
    normalized = part.lower()
    return normalized in _NON_PRODUCTION_DIRS or normalized.startswith(".venv") or normalized.startswith("venv-")


def _find_wifi_provisioning_violations(production: dict[str, str]) -> list[str]:
    violations: list[str] = []

    for filename, source in production.items():
        source = _strip_comments(filename, source)
        for marker in _FORBIDDEN_MARKERS:
            if marker in source:
                violations.append(f"{filename}: forbidden {marker}")

        if filename.endswith(".py"):
            violations.extend(_find_python_credential_handlers(filename, source))
            continue
        if not filename.endswith(".java"):
            continue

        class_match = re.search(r"\bclass\s+(\w+)", source)
        class_name = class_match.group(1) if class_match else Path(filename).stem
        class_offset = class_match.start() if class_match else 0
        class_prefixes = [""]

        for mapping in _MAPPING_RE.finditer(source, 0, class_offset):
            if mapping.group("kind") == "RequestMapping":
                class_prefixes = _mapping_paths(mapping)

        for mapping in _MAPPING_RE.finditer(source, class_offset):
            for class_prefix in class_prefixes:
                for mapping_path in _mapping_paths(mapping):
                    route = _join_route(class_prefix, mapping_path).lower()
                    if route == "/device/provision" or route.startswith("/device/provision/"):
                        violations.append(f"{filename}: route {route}")

        credentials = {match.group(1).lower() for match in _CREDENTIAL_DECL_RE.finditer(source)}
        explicit_wifi_credentials = credentials & {"wifissid", "wifipassword"}
        generic_credentials = credentials & {"ssid", "password"}
        provisioning_type = bool(
            re.search(
                r"(?:wifi.*provision|provision.*wifi|provision(?:ing)?(?:request|dto))", class_name, re.IGNORECASE
            )
        )
        if (
            explicit_wifi_credentials
            or generic_credentials == {"ssid", "password"}
            or (provisioning_type and generic_credentials)
        ):
            for credential in sorted(credentials):
                violations.append(f"{filename}: {class_name} declares {credential}")

    return violations


def _load_production_sources() -> dict[str, str]:
    roots = (
        (ROOT / "manager-api" / "src" / "main" / "java", "*.java"),
        (ROOT / "tbot-server", "*.py"),
    )
    production: dict[str, str] = {}

    for source_root, pattern in roots:
        for path in source_root.rglob(pattern):
            relative = path.relative_to(source_root)
            if any(_is_non_production_dir(part) for part in relative.parts[:-1]):
                continue
            production[str(path.relative_to(ROOT))] = path.read_text(
                encoding="utf-8",
                errors="ignore",
            )

    return production


def test_boundary_scanner_detects_composed_device_provision_route_and_credentials():
    production = {
        "DeviceController.java": """
            @RestController
            @RequestMapping("/device")
            class DeviceController {
                @PostMapping("/provision")
                Result provision(@RequestBody ProvisionWifiRequest request) { return null; }
            }
        """,
        "ProvisionWifiRequest.java": """
            class ProvisionWifiRequest {
                private String ssid;
                private String password;
            }
        """,
    }

    violations = _find_wifi_provisioning_violations(production)

    assert any("/device/provision" in violation for violation in violations)
    assert any("ProvisionWifiRequest" in violation and "ssid" in violation for violation in violations)
    assert any("ProvisionWifiRequest" in violation and "password" in violation for violation in violations)


def test_boundary_scanner_ignores_unrelated_auth_and_runtime_identifiers():
    production = {
        "LoginController.java": """
            @RestController
            @RequestMapping("/login")
            class LoginController {
                @PostMapping
                Result login(@RequestParam String password) { return null; }
            }
        """,
        "DeviceReportReqDTO.java": """
            class DeviceReportReqDTO {
                private String ssid;
            }
        """,
        "DeviceReportRespDTO.java": """
            class DeviceReportRespDTO {
                private String password; // MQTT auth password, not home Wi-Fi.
                /* Documentation example only: String ssid; String password; */
            }
        """,
    }

    assert _find_wifi_provisioning_violations(production) == []


def test_boundary_scanner_detects_legacy_provisioning_command():
    production = {
        "DeviceCommand.java": """
            class DeviceCommand {
                private static final String COMMAND = "device.provisionWifi";
            }
        """,
    }

    violations = _find_wifi_provisioning_violations(production)

    assert any("device.provisionWifi" in violation for violation in violations)


def test_boundary_scanner_detects_python_route_and_wifi_credentials():
    production = {
        "provisioning.py": """
            @app.post("/device/provision")
            def provision(wifiSsid: str, wifiPassword: str):
                return None
        """,
    }

    violations = _find_wifi_provisioning_violations(production)

    assert any("/device/provision" in violation for violation in violations)
    assert any("wifiSsid" in violation for violation in violations)
    assert any("wifiPassword" in violation for violation in violations)


def test_boundary_scanner_detects_generic_python_wifi_provision_handler():
    production = {
        "wifi.py": """
            @app.post("/wifi/provision")
            def provision(ssid: str, password: str):
                return None
        """,
    }

    violations = _find_wifi_provisioning_violations(production)

    assert any("/wifi/provision" in violation for violation in violations)
    assert any("ssid" in violation for violation in violations)
    assert any("password" in violation for violation in violations)


def test_boundary_scanner_detects_aiohttp_route_and_json_body_credentials():
    production = {
        "wifi.py": """
            async def provision(request):
                payload = await request.json()
                ssid = payload["ssid"]
                password = payload.get("password")
                return web.json_response({"ok": True})

            app.add_routes([web.post("/wifi/provision", provision)])
        """,
    }

    violations = _find_wifi_provisioning_violations(production)

    assert any("/wifi/provision" in violation for violation in violations)
    assert any("ssid" in violation for violation in violations)
    assert any("password" in violation for violation in violations)


def test_boundary_scanner_detects_snake_case_and_keyword_python_routes():
    production = {
        "fastapi.py": """
            @app.post(path="/wifi/provision")
            def configure_network(wifi_ssid: str, wifi_password: str):
                return None
        """,
        "aiohttp.py": """
            async def handle_post(request):
                payload = await request.json()
                wifi_ssid = payload["wifi_ssid"]
                wifi_password = payload["wifi_password"]

            app.add_routes([web.post("/wifi/provision", self.wifi_handler.handle_post)])
        """,
    }

    violations = _find_wifi_provisioning_violations(production)

    assert sum("/wifi/provision" in violation for violation in violations) >= 2
    assert any("wifi_ssid" in violation for violation in violations)
    assert any("wifi_password" in violation for violation in violations)


def test_boundary_scanner_detects_named_and_multiple_spring_paths():
    production = {
        "DeviceController.java": """
            @RestController
            @RequestMapping(name = "device-controller", path = {"/health", "/device"})
            class DeviceController {
                @PostMapping(name = "provision", path = {"/status", "/provision"})
                Result provision() { return null; }
            }
        """,
    }

    violations = _find_wifi_provisioning_violations(production)

    assert any("/device/provision" in violation for violation in violations)


def test_versioned_virtualenv_directories_are_non_production():
    assert _is_non_production_dir(".venv311")
    assert _is_non_production_dir("venv-py312")


def test_boundary_scanner_detects_annotated_java_wifi_credentials():
    production = {
        "ProvisionWifiRequest.java": """
            class ProvisionWifiRequest {
                @JsonProperty("wifiSsid")
                private Secret network;
                @JsonProperty("wifiPassword")
                private Secret key;
            }
        """,
    }

    violations = _find_wifi_provisioning_violations(production)

    assert any("wifiSsid" in violation for violation in violations)
    assert any("wifiPassword" in violation for violation in violations)


def test_esp_server_has_no_home_wifi_credential_endpoint():
    production = _load_production_sources()

    assert any(filename.endswith(".java") for filename in production)
    assert any(filename.endswith(".py") for filename in production)
    assert _find_wifi_provisioning_violations(production) == []
