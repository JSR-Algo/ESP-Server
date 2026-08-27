import hashlib
import json
import os
from pathlib import Path
from urllib.request import urlopen

import pytest


BACKEND_ROOT = Path(os.environ.get(
    "TBOT_BACKEND_WORKTREE",
    Path(__file__).resolve().parents[5] / "tbot-backend",
))
ESP_ROOT = Path(os.environ.get(
    "TBOT_ESP_REPOSITORY_ROOT",
    Path(__file__).resolve().parents[3],
))
FIXTURE_ROOT = BACKEND_ROOT / "src/lessons/fixtures/course-mode/pilot/v2"
IDENTITY_FIXTURE = FIXTURE_ROOT / "course-mode-v5-identity-candidate.json"
ORIGIN = os.environ.get("COURSE_MODE_ASSET_ORIGIN_BASE", "http://127.0.0.1:8102/")


def published_assets():
    return json.loads(IDENTITY_FIXTURE.read_text())["sharedAssets"]


@pytest.mark.parametrize(
    "asset_key",
    ["course-mode.v5.scene.farm", "course-mode.v5.object.barn"],
)
def test_canonical_static_asset_returns_published_bytes_and_mime(asset_key):
    asset = next(item for item in published_assets() if item["assetKey"] == asset_key)
    assert_asset_response(asset)


def test_cinematic_asset_returns_published_bytes_and_mime():
    asset = next(
        item for item in published_assets()
        if item["assetKey"] == "course-mode.v5.robot.teach"
    )
    assert_asset_response(asset)


def assert_asset_response(asset):
    storage_path = asset["storagePath"]
    fixture = (
        FIXTURE_ROOT.parents[1] / storage_path
        if storage_path.startswith("pilot/v2/")
        else ESP_ROOT / storage_path
    )
    with urlopen(f'{ORIGIN}{storage_path}', timeout=5) as response:
        body = response.read()
        content_type = response.headers.get_content_type()

    assert content_type == asset["mediaType"]
    assert len(body) == asset["size"] == fixture.stat().st_size
    assert hashlib.sha256(body).hexdigest() == asset["sha256"]
    assert body == fixture.read_bytes()
