from __future__ import annotations

import http.client
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
NGINX_CONFIG = REPO_ROOT / "docs/docker/nginx.conf"
DOCKER_INFO_TIMEOUT_SECONDS = 2
DERIVATIVE_ID = "a" * 64


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return (
            subprocess.run(
                ["docker", "info"],
                capture_output=True,
                check=False,
                timeout=DOCKER_INFO_TIMEOUT_SECONDS,
            ).returncode
            == 0
        )
    except subprocess.TimeoutExpired:
        return False


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _request(
    port: int, path: str, *, method: str = "GET"
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, headers={"Host": "localhost"})
        response = connection.getresponse()
        body = response.read()
        headers = {key.lower(): value for key, value in response.getheaders()}
        return response.status, headers, body
    finally:
        connection.close()


def _render_config(destination: Path) -> None:
    rendered = NGINX_CONFIG.read_text(encoding="utf-8")
    replacements = {
        "__NESTJS_UPSTREAM_HOST__": "127.0.0.1",
        "__NESTJS_AUTH_HEADER__": "",
        "__NESTJS_UPSTREAM_SCHEME__": "http",
        "__NESTJS_ADMIN_PROXY_KEY__": "",
    }
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    destination.write_text(rendered, encoding="utf-8")


@pytest.mark.skipif(
    not _docker_ready(), reason="Docker daemon is required for executable nginx coverage"
)
def test_manager_web_serves_trgb_and_mp4_derivatives_without_spa_fallback(tmp_path):
    web_root = tmp_path / "web"
    web_root.mkdir()
    spa_body = b"manager web SPA fallback"
    (web_root / "index.html").write_bytes(spa_body)

    derivatives = tmp_path / "lesson-derivatives" / "lessons" / "derivatives" / DERIVATIVE_ID
    derivatives.mkdir(parents=True)
    trgb_body = b"TRGB-device-frames"
    mp4_body = b"MP4-preview"
    (derivatives / "barn-listen.trgb").write_bytes(trgb_body)
    (derivatives / "barn-listen.mp4").write_bytes(mp4_body)

    rendered_config = tmp_path / "nginx.conf"
    _render_config(rendered_config)
    port = _free_port()
    container_name = f"tbot-manager-web-lesson-derivatives-{port}"
    command = [
        "docker",
        "run",
        "--rm",
        "-d",
        "--name",
        container_name,
        "-p",
        f"127.0.0.1:{port}:8002",
        "-v",
        f"{rendered_config}:/etc/nginx/nginx.conf:ro",
        "-v",
        f"{web_root}:/usr/share/nginx/html:ro",
        "-v",
        f"{tmp_path / 'lesson-derivatives'}:/uploadfile/lesson-derivatives:ro",
        "nginx:alpine",
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    try:
        trgb_path = (
            f"/lesson-derivatives/lessons/derivatives/{DERIVATIVE_ID}/barn-listen.trgb"
        )
        for _attempt in range(50):
            try:
                if _request(port, trgb_path)[0] in {200, 404}:
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("manager-web nginx derivative probe did not become ready")

        expected = {
            trgb_path: ("application/vnd.tbot.rgb565-indexed", trgb_body),
            trgb_path.removesuffix(".trgb") + ".mp4": ("video/mp4", mp4_body),
        }
        for path, (media_type, body) in expected.items():
            status, headers, response_body = _request(port, path)
            assert status == 200
            assert headers["content-type"] == media_type
            assert headers["cache-control"] == "public, max-age=31536000, immutable"
            assert headers["accept-ranges"] == "bytes"
            assert headers["x-content-type-options"] == "nosniff"
            assert response_body == body
            assert _request(port, path, method="HEAD")[0] == 200
            assert _request(port, path, method="POST")[0] in {403, 405}

        missing_paths = (
            f"/lesson-derivatives/lessons/derivatives/{DERIVATIVE_ID}/missing.trgb",
            "/lesson-derivatives/malformed.trgb",
        )
        for path in missing_paths:
            missing_status, _missing_headers, missing_body = _request(port, path)
            assert missing_status == 404
            assert missing_body != spa_body
    finally:
        subprocess.run(["docker", "stop", container_name], check=False, capture_output=True)
