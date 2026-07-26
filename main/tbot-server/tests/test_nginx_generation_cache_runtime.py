from __future__ import annotations

import http.client
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
NGINX_CONFIG = REPO_ROOT / "deploy/nginx/tjbot.vn.conf"
ETAG = '"generation-cache-test"'
BODY = b'{"generation":2,"indexChecksum":"test"}'


class _CountingUpstream(ThreadingHTTPServer):
    request_count: int
    accept_encodings: list[str | None]
    lock: threading.Lock


class _GenerationHandler(BaseHTTPRequestHandler):
    server: _CountingUpstream

    def do_GET(self) -> None:
        with self.server.lock:
            self.server.request_count += 1
            self.server.accept_encodings.append(self.headers.get("Accept-Encoding"))
        time.sleep(0.2)
        if self.headers.get("If-None-Match") == ETAG:
            self.send_response(304)
            self.send_header("ETag", ETAG)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(BODY)))
        self.send_header("ETag", ETAG)
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _docker_ready() -> bool:
    return shutil.which("docker") is not None and subprocess.run(
        ["docker", "info"], capture_output=True, check=False
    ).returncode == 0


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _request(
    port: int,
    path: str,
    *,
    method: str = "GET",
    host: str = "esp.tjbot.vn",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    request_headers = {"Host": host, **(headers or {})}
    try:
        connection.request(method, path, headers=request_headers)
        response = connection.getresponse()
        body = response.read()
        headers_by_name = {key.lower(): value for key, value in response.getheaders()}
        return response.status, headers_by_name, body
    finally:
        connection.close()


@pytest.mark.skipif(not _docker_ready(), reason="Docker daemon is required for executable nginx coverage")
def test_generation_cache_collapses_cloudflared_burst_and_preserves_http_semantics(tmp_path):
    upstream_port = _free_port()
    upstream = _CountingUpstream(("0.0.0.0", upstream_port), _GenerationHandler)
    upstream.request_count = 0
    upstream.accept_encodings = []
    upstream.lock = threading.Lock()
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()

    rendered_config = tmp_path / "tjbot.vn.conf"
    rendered_config.write_text(
        NGINX_CONFIG.read_text(encoding="utf-8")
        .replace("listen 80;", "listen 80 default_server;", 1)
        .replace("127.0.0.1:3300", f"host.docker.internal:{upstream_port}")
        .replace("127.0.0.1:8003", f"host.docker.internal:{upstream_port}"),
        encoding="utf-8",
    )

    nginx_port = _free_port()
    container_name = f"tbot-nginx-generation-cache-{nginx_port}"
    command = [
        "docker",
        "run",
        "--rm",
        "-d",
        "--name",
        container_name,
        "--add-host",
        "host.docker.internal:host-gateway",
        "-p",
        f"127.0.0.1:{nginx_port}:80",
        "-v",
        f"{rendered_config}:/etc/nginx/conf.d/tjbot.vn.conf:ro",
        "nginx:alpine",
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    try:
        for _attempt in range(50):
            try:
                status, _headers, _body = _request(
                    nginx_port, "/public/lesson-assets/generation"
                )
                if status == 200:
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("nginx generation cache probe did not become ready")

        with upstream.lock:
            upstream.request_count = 0
            upstream.accept_encodings.clear()
        cold_status, cold_headers, cold_body = _request(
            nginx_port,
            "/v1/public/lesson-assets/latest?cold-conditional=1",
            host="admin.tjbot.vn",
            headers={"If-None-Match": ETAG},
        )
        assert cold_status == 304
        assert cold_headers["etag"] == ETAG
        assert cold_body == b""
        requests = [
            (f"rotated-{index}.invalid", f"?variant={index}") for index in range(96)
        ]
        with ThreadPoolExecutor(max_workers=96) as executor:
            responses = list(
                executor.map(
                    lambda item: _request(
                        nginx_port,
                        f"/v1/public/lesson-assets/latest{item[1]}",
                        host=item[0],
                    ),
                    requests,
                )
            )

        assert all(status == 200 and body == BODY for status, _headers, body in responses)
        assert upstream.request_count == 1
        assert upstream.accept_encodings == ["identity"]

        head_status, head_headers, head_body = _request(
            nginx_port, "/v1/public/lesson-assets/latest?head=1", method="HEAD", host="admin.tjbot.vn"
        )
        assert head_status == 200
        assert head_headers["etag"] == ETAG
        assert head_body == b""

        conditional_status, conditional_headers, conditional_body = _request(
            nginx_port,
            "/v1/public/lesson-assets/latest?conditional=1",
            host="esp.tjbot.vn",
            headers={"If-None-Match": ETAG},
        )
        assert conditional_status == 304
        assert conditional_headers["etag"] == ETAG
        assert conditional_body == b""
        assert upstream.request_count == 1

        # Let the valid burst drain before proving Host rotation cannot evade the cap.
        time.sleep(2)
        abusive_requests = [
            (f"attacker-{index}.invalid", f"?abuse={index}") for index in range(500)
        ]
        with ThreadPoolExecutor(max_workers=160) as executor:
            abusive_responses = list(
                executor.map(
                    lambda item: _request(
                        nginx_port,
                        f"/v1/public/lesson-assets/latest{item[1]}",
                        host=item[0],
                    ),
                    abusive_requests,
                )
            )
        abusive_statuses = [status for status, _headers, _body in abusive_responses]
        assert set(abusive_statuses) <= {200, 429}
        assert 200 in abusive_statuses
        assert 429 in abusive_statuses
        assert upstream.request_count == 1

        assert _request(nginx_port, "/v1/public/lesson-assets/latest", method="POST")[0] == 405
    finally:
        subprocess.run(["docker", "stop", container_name], check=False, capture_output=True)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=5)
