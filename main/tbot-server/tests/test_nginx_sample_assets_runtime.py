import http.client
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
NGINX_CONFIG = REPO_ROOT / "deploy/nginx/tjbot.vn.conf"
DOCKER_INFO_TIMEOUT_SECONDS = 2


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


def _request_with_body(
    port: int, path: str, *, method: str = "GET", host: str = "esp.tjbot.vn"
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, headers={"Host": host})
        response = connection.getresponse()
        body = response.read()
        return response.status, {key.lower(): value for key, value in response.getheaders()}, body
    finally:
        connection.close()


def _request(port: int, path: str, *, method: str = "GET", host: str = "esp.tjbot.vn") -> tuple[int, dict[str, str]]:
    status, headers, _body = _request_with_body(port, path, method=method, host=host)
    return status, headers


@pytest.mark.skipif(not _docker_ready(), reason="Docker daemon is required for executable nginx coverage")
def test_nginx_sample_asset_runtime_contract(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    for filename in ("barn-round-field-poster.jpg", "bright-teach.png", "barn.png"):
        (assets / filename).write_bytes(b"sample")
    (assets / "leak.txt").symlink_to("/etc/passwd")

    port = _free_port()
    container_name = f"tbot-nginx-sample-assets-{port}"
    command = [
        "docker",
        "run",
        "--rm",
        "-d",
        "--name",
        container_name,
        "-p",
        f"127.0.0.1:{port}:80",
        "-v",
        f"{NGINX_CONFIG}:/etc/nginx/conf.d/tjbot.vn.conf:ro",
        "-v",
        f"{assets}:/var/www/tbot-sample-assets:ro",
        "nginx:alpine",
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    try:
        for _attempt in range(50):
            try:
                if _request(port, "/lesson-sample-assets/barn.png")[0] == 200:
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("nginx sample asset probe did not become ready")

        expected_assets = {
            "/lesson-sample-assets/assets/background/barn-round-field-poster.jpg": "image/jpeg",
            "/lesson-sample-assets/assets/robot/poses/bright-teach.png": "image/png",
            "/lesson-sample-assets/barn.png": "image/png",
        }
        for path, content_type in expected_assets.items():
            status, headers = _request(port, path)
            assert status == 200
            assert headers["content-type"] == content_type
            assert headers["cache-control"] == "public, max-age=31536000, immutable"
            assert _request(port, path, method="HEAD")[0] == 200

        assert _request(port, "/lesson-sample-assets/barn.png", method="POST")[0] == 405
        assert _request(port, "/lesson-sample-assets")[0] == 404
        assert _request(port, "/lesson-sample-assets/unknown.png")[0] == 404
        assert _request(port, "/lesson-sample-assets/assets/../bright-teach.png")[0] == 404
        assert _request(port, "/lesson-sample-assets/assets%2F..%2Fbright-teach.png")[0] == 404
        assert _request(port, "/lesson-sample-assets/assets/%2e%2e/bright-teach.png")[0] == 404
        assert _request(port, "/lesson-sample-assets/assets%5C..%5Cbright-teach.png")[0] == 404
        assert _request(port, "/lesson-sample-assets/barn.png?next=assets%2Fsafe")[0] == 200
        assert _request(port, "/lesson-sample-assets/barn.png?next=%2e%2e%2Fsafe")[0] == 200
        symlink_status, _symlink_headers, symlink_body = _request_with_body(
            port, "/lesson-sample-assets/leak.txt"
        )
        assert symlink_status in {403, 404}
        assert b"root:" not in symlink_body
        assert _request(port, "/lesson-sample-assets/barn.png", host="admin.tjbot.vn")[0] != 200
    finally:
        subprocess.run(["docker", "stop", container_name], check=False, capture_output=True)
