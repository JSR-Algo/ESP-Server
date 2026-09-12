"""SD readiness requires a robot response even without advertised MCP support."""

import asyncio
import json

import pytest

from core.lesson.runtime import LessonRuntime, RENDERER_V5, _renderer_v5_request_enabled
from core.lesson.sample import SampleAssetCache
from core.lesson.sd_pack_mcp_payload import build_firmware_sync_pack
from tests import test_layered_cinematic_contract as layered
from tests import test_lesson_runtime as legacy
from tests.sd_mcp_device import install_sd_mcp_device


class ReadyV5Cache(legacy._FakeAssetCache):
    def asset_pack_manifest(self, **kwargs):
        pack = super().asset_pack_manifest(**kwargs)
        pack["localRoot"] = f"sd://tbot/lesson-assets/{self.cache_key}"
        pack["assets"] = layered._pack()["assets"]
        for asset, layer in zip(pack["assets"], layered._phase()["layers"]):
            path = pack["localRoot"] + "/" + asset["key"].replace("@", "%40")
            asset.update(localPath=path, sdPath=path, critical=True, layer=layer["layer"],
                         onlineUrl="https://assets.example/" + asset["key"])
        return pack

    def synthesize_preload_status(self, assignment_version):
        return {"ready": True, "assets": [
            {**asset, "critical": True} for asset in layered._pack()["assets"]
        ]}


def v5_runtime(mcp_feature, reports):
    manifest = legacy._build_manifest()
    manifest.update(manifestVersion=RENDERER_V5, protocolVersion=RENDERER_V5,
                    cinematicPhases=[layered._phase()])
    manifest["cinematicPhases"][0]["phaseId"] = "flyIn"
    manifest["assets"].append({"id": "robot.teach@v1", "visualRefs": [
        {"stepKey": manifest["steps"][0]["id"], "phase": "opening", "slot": "robotOverlay"}
    ]})
    features = {"lesson": True, "renderer": [RENDERER_V5],
                "lessonRendererV5": {"layeredCinematic": True, "sdAssetPack": True}}
    if mcp_feature != "absent":
        features["mcp"] = mcp_feature
    conn = legacy._FakeConn(features=features)
    conn.device_id = "robot-layered"
    conn.config = {"lesson": {
        "renderer_v5_enabled": True, "rollout_device_allowlist": [conn.device_id],
        "asset_delivery_mode": "sd_pack", "sd_sync_ready_timeout_sec": 0,
    }}

    async def report(body):
        reports.append(dict(body))

    runtime = LessonRuntime(conn, assignment=legacy._build_assignment(), manifest=manifest,
                            asset_cache=ReadyV5Cache(), forwarder=legacy._FakeForwarder(),
                            manifest_checksum=legacy._manifest_checksum(),
                            preload_status_reporter=report)
    conn.lesson_runtime = runtime
    assert _renderer_v5_request_enabled(conn, [RENDERER_V5])
    assert runtime._renderer_v5_enabled()
    assert runtime.asset_cache.synthesize_preload_status(runtime.assignment_version)["ready"]
    build_firmware_sync_pack(runtime.asset_cache.asset_pack_manifest(
        assignment_version=runtime.assignment_version, lesson_id=runtime.lesson_id,
        lesson_version=runtime.lesson_version, manifest_checksum=runtime.manifest_checksum,
    ))
    return runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("mcp_feature", ["absent", False, True])
async def test_v5_missing_mcp_client_fails_before_ready_or_protocol(mcp_feature):
    reports = []
    runtime = v5_runtime(mcp_feature, reports)
    runtime.conn.mcp_client = None
    try:
        await runtime.start()
        await asyncio.sleep(0)
        assert reports and all(item["state"] == "FAILED" for item in reports)
        assert all(item.get("checksumOk") is not True for item in reports)
        frames = [json.loads(frame) for frame in runtime.conn.websocket.sent]
        assert [frame["type"] for frame in frames] == ["lesson_error"]
        assert frames[0]["body"]["code"] == "ASSET_PACK_NOT_READY"
        assert frames[0]["body"]["retryable"] is True
        assert runtime.state == "FAILED"
        assert runtime.last_error.code == "ASSET_PACK_NOT_READY"
        assert runtime._sd_asset_pack_online_fallback is False
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mcp_feature", ["absent", False, True])
async def test_builtin_sample_missing_mcp_client_cannot_attest(mcp_feature):
    runtime = v5_runtime(mcp_feature, [])
    runtime.conn.mcp_client = None
    runtime.asset_cache = SampleAssetCache(sd_pack=True, asset_base="https://cdn.example")
    assert runtime.asset_cache.firmware_sample_sync_request() is not None
    try:
        assert await runtime._sync_sd_asset_pack_to_robot() is False
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_v5_ready_client_returns_exact_attestation_before_ready_and_prepare():
    reports = []
    runtime = v5_runtime(True, reports)
    calls = install_sd_mcp_device(runtime.conn)
    try:
        await runtime.start()
        await asyncio.sleep(0)
        assert len(calls) == 1
        assert calls[0]["params"]["arguments"]["assetPack"]["cacheKey"] == runtime.asset_cache.cache_key
        assert reports and all(item["state"] == "READY" and item["checksumOk"] is True for item in reports)
        assert [json.loads(frame)["type"] for frame in runtime.conn.websocket.sent] == ["lesson_prepare"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_v5_unready_client_fails_before_ready_or_prepare():
    reports = []
    runtime = v5_runtime(True, reports)
    runtime.conn.mcp_client = legacy._NotReadyLessonAssetMcpClient()
    try:
        await runtime.start()
        await asyncio.sleep(0)
        assert reports and all(item["state"] == "FAILED" for item in reports)
        assert all(item.get("checksumOk") is not True for item in reports)
        assert [json.loads(frame)["type"] for frame in runtime.conn.websocket.sent] == ["lesson_error"]
        assert runtime.last_error.code == "ASSET_PACK_NOT_READY"
    finally:
        await runtime.close()
