"""Renderer-v5 asset-cache inputs must join phase layers to manifest assets by the shared identity.

The backend (lesson-manifest.logic.ts) pins every renderer-v5 phase layer's ``assetVersionId`` to the
published visual-ref UUID, while ``manifest.assets`` are keyed ``assetKey@vN``. S18 run12 observed on a real
cpr-s03-final52 wire that ``_manifest_asset_cache_inputs`` keyed the layered entries by the raw UUID, so the
manifest asset (path/url) was never joined and ``validate_layered_cinematic_runtime_asset`` refused the
entry (``key != f"{sharedAssetKey}@v{sharedAssetVersion}"``) -> every real v5 lesson failed
ASSET_PROFILE_UNAVAILABLE before a byte was fetched. Canonical ``assetKey@vN`` references must keep their
exact behaviour; malformed references keep their previous (raw) key so existing refusals are unchanged.
"""
import copy

import pytest

from core.lesson.layered_cinematic_contract import (
    LayeredCinematicContractError,
    is_layered_cinematic_generation_asset,
    validate_layered_cinematic_runtime_asset,
)
from core.lesson.runtime import RENDERER_V5, _manifest_asset_cache_inputs

UUID_ROBOT = "cc9633a9-3c6a-47d7-88ad-b87195cc49ed"
UUID_SCENE = "e7514d67-d71b-4d54-b980-39252071b0a8"
SHA_ROBOT = "88cda7f86672a272ce5294167ca5c08f11ac60b66d15f58495b48f5cf0e36e93"
SHA_SCENE = "d4abb6087dc3122e0a00feb5e6a86b03dc7db550eb59d25e92f54d0fd09e4fc0"
ORIGIN = "http://media.example/tvideo-demo"


def _wire_manifest(robot_ref: str, scene_ref: str) -> dict:
    """Shape observed on the real backend wire (S18 run12, w01-greetings-politeness v5)."""
    return {
        "manifestVersion": RENDERER_V5,
        "protocolVersion": RENDERER_V5,
        "assets": [
            {"assetId": "cpr-t09.robot.flyIn@v2", "id": "cpr-t09.robot.flyIn@v2", "assetKey": "cpr-t09.robot.flyIn",
             "version": 2, "layer": "robotOverlay", "role": "pose", "mediaType": "video/mp4",
             "path": "cpr-t09/flyIn.mp4", "url": f"{ORIGIN}/cpr-t09/flyIn.mp4", "sha256": SHA_ROBOT, "bytes": 95007,
             "dimensions": {"width": 240, "height": 240}, "critical": True},
            {"assetId": "scene.playground-park@v3", "id": "scene.playground-park@v3", "assetKey": "scene.playground-park",
             "version": 3, "layer": "backgroundScene", "role": "poster", "mediaType": "image/jpeg",
             "path": "cpr-t09/background-farm.jpg", "url": f"{ORIGIN}/cpr-t09/background-farm.jpg", "sha256": SHA_SCENE,
             "bytes": 43599, "dimensions": {"width": 480, "height": 320}, "critical": True},
        ],
        "cinematicPhases": [{
            "phaseId": "flyIn", "templateId": "layeredCinematic", "templateVersion": 1, "playbackMode": "once",
            "activityIds": ["w01.a01"], "timing": {"durationMs": 3200},
            "layers": [
                {"slot": "backgroundScene", "layer": "background", "assetKey": "scene.playground-park", "version": 3,
                 "assetVersionId": scene_ref, "sha256": SHA_SCENE, "bytes": 43599,
                 "metadata": {"fit": "cover", "rect": {"x": 0, "y": 0, "width": 480, "height": 320}, "width": 480,
                              "height": 320, "mediaKind": "image", "mediaType": "image/jpeg"}},
                {"slot": "robotOverlay", "layer": "robotOverlay", "assetKey": "cpr-t09.robot.flyIn", "version": 2,
                 "assetVersionId": robot_ref, "sha256": SHA_ROBOT, "bytes": 95007,
                 "metadata": {"fps": 15, "rect": {"x": 240, "y": 0, "width": 240, "height": 240}, "codec": "mjpeg",
                              "width": 240, "height": 240, "hasAudio": False,
                              "chromaKey": {"keyColor": "#00ff00", "tolerance": 32, "featherPx": 4},
                              "mediaKind": "video", "mediaType": "video/mp4", "durationMs": 3200, "frameCount": 48}},
            ],
        }],
        "steps": [],
    }


@pytest.mark.parametrize("robot_ref,scene_ref", [
    (UUID_ROBOT, UUID_SCENE),                                   # real backend wire: published UUIDs
    ("cpr-t09.robot.flyIn@v2", "scene.playground-park@v3"),    # canonical shared form
])
def test_v5_layered_cache_inputs_join_manifest_assets_by_shared_identity(robot_ref, scene_ref):
    manifest = _wire_manifest(robot_ref, scene_ref)
    inputs = {item["key"]: item for item in _manifest_asset_cache_inputs(manifest)}
    assert set(inputs) == {"cpr-t09.robot.flyIn@v2", "scene.playground-park@v3"}
    robot = inputs["cpr-t09.robot.flyIn@v2"]
    assert robot["path"] == "cpr-t09/flyIn.mp4" and robot["url"] == f"{ORIGIN}/cpr-t09/flyIn.mp4"
    assert robot["sha256"] == SHA_ROBOT and robot["size"] == 95007 and robot["critical"] is True
    assert robot["sharedAssetKey"] == "cpr-t09.robot.flyIn" and robot["sharedAssetVersion"] == 2
    for item in inputs.values():
        assert is_layered_cinematic_generation_asset(item)
        projected = validate_layered_cinematic_runtime_asset(item)   # what asset_cache uses to flag renderer_v5_media
        assert projected["sharedAssetKey"] == item["sharedAssetKey"]


def test_v5_canonical_form_projection_is_unchanged_by_reference_form():
    canonical = _manifest_asset_cache_inputs(_wire_manifest("cpr-t09.robot.flyIn@v2", "scene.playground-park@v3"))
    wire = _manifest_asset_cache_inputs(_wire_manifest(UUID_ROBOT, UUID_SCENE))
    assert canonical == wire


def test_v5_conflicting_phase_identities_still_refuse_across_reference_forms():
    manifest = _wire_manifest(UUID_ROBOT, UUID_SCENE)
    second = copy.deepcopy(manifest["cinematicPhases"][0])
    second["phaseId"] = "walk"
    second["layers"][1]["assetVersionId"] = "cpr-t09.robot.flyIn@v2"
    second["layers"][1]["sha256"] = "0" * 64
    manifest["cinematicPhases"].append(second)
    with pytest.raises(ValueError, match="conflicting phase identities"):
        _manifest_asset_cache_inputs(manifest)


def test_v5_malformed_reference_keeps_raw_key_and_is_refused_later():
    manifest = _wire_manifest("not-a-uuid-and-not-shared", UUID_SCENE)
    inputs = {item["key"]: item for item in _manifest_asset_cache_inputs(manifest)}
    assert "not-a-uuid-and-not-shared" in inputs
    with pytest.raises(LayeredCinematicContractError):
        validate_layered_cinematic_runtime_asset(inputs["not-a-uuid-and-not-shared"])
