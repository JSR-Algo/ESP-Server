"""Resolved SD identity survives the production Course Mode prepare path."""
from copy import deepcopy
import json
from urllib.parse import quote
from unittest.mock import AsyncMock, patch

import pytest

from core.lesson.course_mode_compatibility import course_mode_compatibility_for_manifest
from core.lesson.layered_cinematic_contract import (
    LayeredCinematicContractError, layered_cinematic_asset_key, project_layered_cinematic_phase,
)
from core.lesson.runtime import LessonRuntime
from tests.test_layered_cinematic_contract import (
    _canonical_v5_phase_and_pack, _course_mode_v5_identity, COURSE_MODE_V5_CHECKSUM,
)
from tests.test_lesson_runtime import _FakeAssetCache, _FakeConn, _FakeForwarder


def canonical_compatibility():
    return course_mode_compatibility_for_manifest(
        _course_mode_v5_identity(), manifest_checksum=COURSE_MODE_V5_CHECKSUM)


def runtime_fixture(mode, pack_keys):
    manifest = deepcopy(_course_mode_v5_identity()["manifestIdentityProjection"])
    _, pack = _canonical_v5_phase_and_pack()
    if pack_keys == "shared":
        for asset in pack["assets"]:
            asset["key"] = f"{asset['sharedAssetKey']}@v{asset['sharedAssetVersion']}"
    checksum = COURSE_MODE_V5_CHECKSUM
    if mode != "canonical":
        # Variant of the checked-in fixture, not a new published contract.
        checksum = "a" * 64
        manifest["lessonId"] = "wire-identity-unit-variant"
        if mode in {"legacy", "ordinary"}:
            manifest["courseModeContract"].pop("activities")
            for phase in manifest["cinematicPhases"]:
                phase.pop("activityIds")
        if mode == "ordinary":
            manifest.pop("courseModeContract")
    pack.update(assignmentVersion=1, lessonId=manifest["lessonId"],
                lessonVersion=manifest["lessonVersion"], manifestChecksum=checksum)

    class Cache(_FakeAssetCache):
        def asset_pack_manifest(self, **kwargs):
            return deepcopy(pack)

    conn = _FakeConn(features={"lesson": True, "renderer": [manifest["manifestVersion"]],
        "lessonRendererV5": {"layeredCinematic": True, "sdAssetPack": True}})
    conn.device_id = "wire-identity-unit"
    conn.config = {"lesson": {"renderer_v5_enabled": True, "asset_delivery_mode": "sd_pack",
        "rollout_device_allowlist": [conn.device_id], "frame_ack_timeout_sec": 60}}
    frames = []

    async def capture(payload):
        frames.append(json.loads(payload))

    runtime = LessonRuntime(conn, assignment={"assignmentId": "wire-unit", "assignmentVersion": 1,
        "lessonId": manifest["lessonId"], "lessonVersion": manifest["lessonVersion"], "profile": "espTft"},
        manifest=manifest, manifest_checksum=checksum, asset_cache=Cache(),
        forwarder=_FakeForwarder(), send=capture)
    conn.lesson_runtime = runtime
    return runtime, pack, frames


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["activity", "legacy", "ordinary", "canonical"])
@pytest.mark.parametrize("pack_keys", ["shared", "uuid"])
async def test_runtime_prepare_joins_resolved_pack_key_only_for_course_mode(mode, pack_keys):
    runtime, pack, frames = runtime_fixture(mode, pack_keys)
    source_before = deepcopy(runtime.manifest)
    try:
        # Storage/device qualification is separate; retain real preload projection,
        # phase selection, prepare serialization and emit with local transport.
        with patch.object(runtime, "_preload_sd_asset_pack_before_prepare", AsyncMock(return_value=True)):
            assert await runtime.preload_only()
        await runtime._emit("lesson_prepare", body=runtime._prepare_body())
        frame = frames[-1]
        phase = frame["body"]["cinematicPhase"]
        assert ("courseModeCompatibility" in phase) == (mode != "ordinary")
        assert ("activityIds" in phase) == (mode in {"activity", "canonical"})
        source = next(p for p in runtime.manifest["cinematicPhases"] if p["phaseId"] == phase["phaseId"])
        for layer, original in zip(phase["layers"], source["layers"]):
            expected = layered_cinematic_asset_key(original) if pack_keys == "shared" else original["assetVersionId"]
            if mode != "ordinary":
                assert layer.get("assetVersionId") == expected
                asset = next(a for a in frame["body"]["assetPack"]["assets"] if a["key"] == expected)
                packed_path = asset.get("localPath") or (
                    frame["body"]["assetPack"]["localRoot"].rstrip("/") + "/" + quote(expected, safe="")
                )
                assert (layer["sha256"], layer["bytes"], layer["sdPath"]) == (
                    asset["sha256"], asset["size"], packed_path)
            else:
                assert "assetVersionId" not in layer
        assert runtime.manifest == source_before
        assert runtime.asset_cache.asset_pack_manifest() == pack
    finally:
        await runtime.close()


@pytest.mark.parametrize("pack_keys", ["shared", "uuid"])
@pytest.mark.parametrize("fault", ["missing", "duplicate", "not-ready", "checksum", "sha", "size",
                                  "media", "metadata", "shared-key", "version"])
def test_course_mode_wire_identity_cannot_mask_invalid_pack_authority(pack_keys, fault):
    phase, pack = _canonical_v5_phase_and_pack()
    if pack_keys == "shared":
        for asset in pack["assets"]:
            asset["key"] = f"{asset['sharedAssetKey']}@v{asset['sharedAssetVersion']}"
    if fault == "missing":
        pack["assets"].pop(0)
    elif fault == "duplicate":
        pack["assets"].append(deepcopy(pack["assets"][0]))
    else:
        field, value = {
            "not-ready": ("state", "PENDING"), "checksum": ("checksumOk", False),
            "sha": ("sha256", "0" * 64), "size": ("size", 1), "media": ("mediaType", "image/png"),
            "metadata": ("compatibilityMetadata", {}), "shared-key": ("sharedAssetKey", "wrong"),
            "version": ("sharedAssetVersion", 9),
        }[fault]
        pack["assets"][0][field] = value
    with pytest.raises(LayeredCinematicContractError) as error:
        project_layered_cinematic_phase(phase, pack,
            course_mode_compatibility=canonical_compatibility(),
            course_mode_activity_ids=set(phase["activityIds"]))
    assert error.value.code == ("CINEMATIC_SD_PATH_MISSING" if fault == "missing" else "CINEMATIC_METADATA_MISMATCH")


def test_shared_resolution_keeps_priority_over_uuid_alias_and_never_falls_back_on_mismatch():
    phase, pack = _canonical_v5_phase_and_pack()
    for asset in list(pack["assets"]):
        shared = deepcopy(asset)
        shared["key"] = f"{asset['sharedAssetKey']}@v{asset['sharedAssetVersion']}"
        pack["assets"].append(shared)
    projected = project_layered_cinematic_phase(phase, pack,
        course_mode_compatibility=canonical_compatibility(),
        course_mode_activity_ids=set(phase["activityIds"]))
    assert [layer["assetVersionId"] for layer in projected["layers"]] == [
        layered_cinematic_asset_key(layer) for layer in phase["layers"]]
    pack["assets"][3]["sha256"] = "0" * 64
    with pytest.raises(LayeredCinematicContractError, match="identity does not match"):
        project_layered_cinematic_phase(phase, pack,
            course_mode_compatibility=canonical_compatibility(),
            course_mode_activity_ids=set(phase["activityIds"]))
