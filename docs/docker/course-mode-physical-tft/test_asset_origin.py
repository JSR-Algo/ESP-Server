import hashlib
import json
import mimetypes
import os
from pathlib import Path
from urllib.request import urlopen

import pytest


BACKEND_ROOT = Path(os.environ.get(
    "TBOT_BACKEND_WORKTREE",
    Path(__file__).resolve().parents[5] / "tbot-backend",
))
FIXTURE_ROOT = BACKEND_ROOT / "src/lessons/fixtures/course-mode/admin-w1"
ORIGIN = os.environ.get("COURSE_MODE_ASSET_ORIGIN_BASE", "http://127.0.0.1:8102/")


def published_assets():
    return json.loads((FIXTURE_ROOT / "published-pack.json").read_text())["assets"]


@pytest.mark.parametrize("asset_key", ["object.robot", "scene.playground-park"])
def test_extensionless_static_asset_returns_published_bytes_and_mime(asset_key):
    asset = next(item for item in published_assets() if item["key"] == asset_key)
    assert_asset_response(asset)


def test_cinematic_asset_returns_published_bytes_and_mime():
    asset = next(item for item in published_assets() if item["key"] == "object.cinematic.greetings@v1")
    assert_asset_response(asset)


def assert_asset_response(asset):
    extension = mimetypes.guess_extension(asset["mediaType"], strict=False)
    assert extension is not None
    fixture = FIXTURE_ROOT / "assets" / f'{asset["sha256"]}{extension}'
    with urlopen(f'{ORIGIN}lesson-assets/{asset["sha256"]}', timeout=5) as response:
        body = response.read()
        content_type = response.headers.get_content_type()

    assert content_type == asset["mediaType"]
    assert len(body) == asset["size"] == fixture.stat().st_size
    assert hashlib.sha256(body).hexdigest() == asset["sha256"]
    assert body == fixture.read_bytes()
