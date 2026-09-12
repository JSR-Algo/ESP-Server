"""Explicit successful robot transport for tests of post-attestation behavior."""

import json


def install_sd_mcp_device(conn):
    original_send = conn.websocket.send
    calls = []
    pending = {}

    class Client:
        name_mapping = {
            "self_lesson_assets_sync_to_sd": "self.lesson_assets.sync_to_sd",
            "self_lesson_assets_sync_sample_to_sd": "self.lesson_assets.sync_sample_to_sd",
        }

        async def is_ready(self):
            return True

        def has_tool(self, name):
            return name in {"self_lesson_assets_sync_to_sd", "self_lesson_assets_sync_sample_to_sd"}

        async def get_next_id(self):
            return len(calls) + 1

        async def register_call_result_future(self, call_id, future):
            pending[call_id] = future

        async def cleanup_call_result(self, call_id):
            pending.pop(call_id, None)

    async def send(raw):
        frame = json.loads(raw)
        if frame.get("type") != "mcp":
            return await original_send(raw)
        request = frame["payload"]
        assert request["method"] == "tools/call"
        calls.append(request)
        arguments = request["params"]["arguments"]
        name = request["params"]["name"]
        if name == "self.lesson_assets.sync_to_sd":
            assert set(arguments) == {"assetPack"}
            pack = arguments["assetPack"]
            result = {"ready": True, "activated": True, "cacheKey": pack["cacheKey"],
                      "manifestChecksum": pack["manifestChecksum"],
                      "downloadedCount": len(pack["assets"]), "skippedCount": 0, "failedCount": 0}
        elif name == "self.lesson_assets.sync_sample_to_sd":
            assert set(arguments) == {"base_url"}
            from core.lesson.sample import _SAMPLE_ASSET_RECORDS, _SAMPLE_FIRMWARE_SD_ROOT
            result = {"directory": _SAMPLE_FIRMWARE_SD_ROOT,
                      "downloadedCount": len(_SAMPLE_ASSET_RECORDS), "files": [
                          {"file": item["path"].rsplit("/", 1)[-1], "bytes": 1,
                           "sha256": item["sha256"]} for item in _SAMPLE_ASSET_RECORDS
                      ]}
        else:
            raise AssertionError(f"unexpected MCP tool: {name}")
        pending.pop(request["id"]).set_result({"content": [{"text": json.dumps(result)}]})

    conn.features["mcp"] = True
    conn.mcp_client = Client()
    conn.websocket.send = send
    return calls
