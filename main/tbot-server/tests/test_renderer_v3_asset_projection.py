from core.lesson.asset_cache import AssetCache
from core.lesson.runtime import _manifest_asset_cache_inputs


def test_renderer_v3_manifest_projection_preserves_shared_mp4_attestation() -> None:
    asset = {
        "id": "scene.playground-park@v1",
        "path": "lesson-assets/scene-playground.mp4",
        "url": "https://cdn.example/lesson-assets/scene-playground.mp4",
        "sha256": "a" * 64,
        "bytes": 1234,
        "critical": True,
        "layer": "backgroundScene",
        "role": "video",
        "mediaType": "video/mp4",
        "sharedAssetKey": "scene.playground-park",
        "sharedAssetVersion": 1,
        "compatibilityMetadata": {
            "codec": "mjpeg",
            "fps": 10,
            "durationMs": 1000,
            "frameCount": 10,
            "hasAudio": False,
            "rect": {"x": 0, "y": 0, "width": 480, "height": 320},
            "chromaKey": None,
        },
        "visualRefs": [
            {
                "stepKey": "s1",
                "phase": "opening",
                "slot": "backgroundScene",
            }
        ],
    }
    manifest = {
        "manifestVersion": "teebot-lesson-renderer.v3",
        "assets": [asset],
    }

    projected = _manifest_asset_cache_inputs(manifest)
    cache = AssetCache(assets=projected, profile="espTft")

    assert projected[0]["sharedAssetKey"] == "scene.playground-park"
    assert projected[0]["sharedAssetVersion"] == 1
    assert projected[0]["compatibilityMetadata"] == asset["compatibilityMetadata"]
    assert projected[0]["visualRefs"] == asset["visualRefs"]
    cache.assert_profile_renderable()
