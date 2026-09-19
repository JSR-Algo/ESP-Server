"""Executable coverage for the asset-origin 404 contract in docs/docker/nginx.conf.

A missing asset under the `LESSON_ASSET_ORIGIN_BASE` prefix used to fall through
to the SPA history fallback and answer HTTP 200 with the manager index.html. That
made a dead media path indistinguishable from a live one by status code, and let
38 dead refs survive several review passes (session S04 / task T09, 2026-09-18).
This test pins the honest 404 while keeping the SPA fallback for app routes.
"""

import http.client
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
NGINX_TEMPLATE = REPO_ROOT / "docs/docker/nginx.conf"
DOCKER_INFO_TIMEOUT_SECONDS = 2

# docs/docker/start.sh substitutes these at container start; render the same
# placeholders here so the test exercises the shipped template verbatim.
TEMPLATE_SUBSTITUTIONS = {
    "__NGINX_RESOLVER__": "127.0.0.11",
    "__NESTJS_UPSTREAM_HOST__": "backend",
    "__NESTJS_UPSTREAM_SCHEME__": "http",
    "__PUBLIC_CMS_UPSTREAM_HOST__": "cms.invalid",
    "__PUBLIC_CMS_UPSTREAM_SCHEME__": "http",
    "__NESTJS_AUTH_HEADER__": "",
    "__NESTJS_ADMIN_PROXY_KEY__": "",
}

SPA_SHELL_MARKER = b"MANAGER-SPA-SHELL"
PRESENT_ASSET_BODY = b"REAL-CLIP-BYTES"


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


def _request(port: int, path: str) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path, headers={"Host": "localhost"})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.mark.skipif(not _docker_ready(), reason="Docker daemon is required for executable nginx coverage")
def test_asset_origin_miss_is_404_not_spa_shell(tmp_path):
    rendered = tmp_path / "nginx.conf"
    config = NGINX_TEMPLATE.read_text()
    for placeholder, value in TEMPLATE_SUBSTITUTIONS.items():
        assert placeholder in config, f"{placeholder} vanished from the nginx template"
        config = config.replace(placeholder, value)
    rendered.write_text(config)

    html = tmp_path / "html"
    course_mode = html / "tvideo-demo" / "shared" / "course-mode"
    course_mode.mkdir(parents=True)
    (html / "index.html").write_bytes(SPA_SHELL_MARKER)
    (html / "tvideo-demo" / "index.html").write_bytes(b"TVIDEO-DEMO-PAGE")
    (course_mode / "present.mp4").write_bytes(PRESENT_ASSET_BODY)

    # Bare-root asset-origin mount points (no /tvideo-demo prefix).
    bare_root_course_mode = html / "shared" / "course-mode"
    bare_root_course_mode.mkdir(parents=True)
    (bare_root_course_mode / "present.mp4").write_bytes(PRESENT_ASSET_BODY)

    port = _free_port()
    container_name = f"tbot-nginx-asset-origin-{port}"
    subprocess.run(
        [
            "docker", "run", "--rm", "-d", "--name", container_name,
            "-p", f"127.0.0.1:{port}:8002",
            "-v", f"{rendered}:/etc/nginx/nginx.conf:ro",
            "-v", f"{html}:/usr/share/nginx/html:ro",
            "nginx:alpine",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        for _attempt in range(50):
            try:
                if _request(port, "/tvideo-demo/shared/course-mode/present.mp4")[0] == 200:
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("nginx asset-origin probe did not become ready")

        status, body = _request(port, "/tvideo-demo/shared/course-mode/present.mp4")
        assert status == 200
        assert body == PRESENT_ASSET_BODY

        # Every shape of a miss under the asset-origin prefix must be a real 404,
        # and must never carry the SPA shell body.
        for path in (
            "/tvideo-demo/shared/course-mode/missing.mp4",
            "/tvideo-demo/shared/course-mode/missing.png",
            "/tvideo-demo/shared/course-mode/",
            "/tvideo-demo/shared/missing-directory/",
            "/tvideo-demo/missing-nested/deep/clip.mp4",
        ):
            status, body = _request(port, path)
            assert status == 404, f"{path} answered {status}, not 404"
            assert SPA_SHELL_MARKER not in body, f"{path} served the SPA shell"

        # The demo landing page is the one directory index under the prefix.
        assert _request(port, "/tvideo-demo/") == (200, b"TVIDEO-DEMO-PAGE")
        assert _request(port, "/tvideo-demo/index.html") == (200, b"TVIDEO-DEMO-PAGE")

        # Some stacks (docker-compose.course-mode-physical-tft.yml) point the
        # asset origin at a bare origin root, so source-relative storage paths
        # resolve at `/`. Those prefixes must 404 on a miss too.
        for path in (
            "/shared/course-mode/missing.mp4",
            "/assets/robot/poses/missing.png",
            "/lessons/authoring/missing.json",
            "/pilot/v1/missing.mp4",
            "/pilot/v2/missing.mp4",
            "/main/manager-web/public/tvideo-demo/assets/t54-layered/missing.png",
        ):
            status, body = _request(port, path)
            assert status == 404, f"{path} answered {status}, not 404"
            assert SPA_SHELL_MARKER not in body, f"{path} served the SPA shell"

        # A real file under a bare-root prefix still serves its own bytes.
        status, body = _request(port, "/shared/course-mode/present.mp4")
        assert status == 200
        assert body == PRESENT_ASSET_BODY

        # Application routes keep the SPA history fallback.
        for path in ("/course/lesson/123", "/some/manager/route"):
            status, body = _request(port, path)
            assert status == 200
            assert body == SPA_SHELL_MARKER
    finally:
        subprocess.run(["docker", "stop", container_name], check=False, capture_output=True)
