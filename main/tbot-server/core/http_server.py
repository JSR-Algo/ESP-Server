import asyncio
import json
from aiohttp import web
from config.logger import setup_logging
from core.api.ota_handler import OTAHandler, is_placeholder_websocket_url
from core.api.vision_handler import VisionHandler
from core.api.lesson_nudge_handler import LessonNudgeHandler
from core.api.lesson_asset_handler import LessonAssetHandler
from core.api.lesson_assignment_console_handler import LessonAssignmentConsoleHandler
from core.api.device_mcp_admin_handler import DeviceMCPAdminHandler
from core.api.lesson_sd_fanout_handler import LessonSdFanoutHandler
from core.lesson.esp_build_identity import (
    approved_identities_from_config,
    esp_build_identity_metrics_fields,
)

TAG = __name__


class SimpleHttpServer:
    def __init__(self, config: dict, lesson_connections=None):
        self.config = config
        self.logger = setup_logging()
        self.ota_handler = OTAHandler(config)
        self.vision_handler = VisionHandler(config)
        self.lesson_asset_handler = LessonAssetHandler(config)
        self.lesson_assignment_console_handler = LessonAssignmentConsoleHandler(
            config,
            lesson_connections if lesson_connections is not None else {},
        )
        self.lesson_connections = lesson_connections if lesson_connections is not None else {}
        self.lesson_nudge_handler = LessonNudgeHandler(
            config,
            self.lesson_connections,
        )
        self.device_mcp_admin_handler = DeviceMCPAdminHandler(
            config,
            self.lesson_connections,
        )
        self.lesson_sd_fanout_handler = LessonSdFanoutHandler(
            config,
            self.lesson_connections,
        )

    def _get_websocket_url(self, local_ip: str, port: int) -> str:
        """GetwebsocketAddress

        Args:
            local_ip: LocalIPAddress
            port: Port number

        Returns:
            str: websocketAddress
        """
        server_config = self.config["server"]
        websocket_config = server_config.get("websocket")

        if not is_placeholder_websocket_url(websocket_config):
            return websocket_config
        return f"ws://{local_ip}:{port}/tbot/v1/"

    async def start(self):
        try:
            server_config = self.config["server"]
            read_config_from_api = self.config.get("read_config_from_api", False)
            host = server_config.get("ip", "0.0.0.0")
            port = int(server_config.get("http_port", 8003))

            if port:
                app = web.Application()

                # Keep local OTA route available even when config is read from manager-api.
                # Firmware can be compiled to this local server while role/model config
                # still comes from the manager.
                app.add_routes(
                    [
                        web.get("/tbot/ota/", self.ota_handler.handle_get),
                        web.post("/tbot/ota/", self.ota_handler.handle_post),
                        web.options(
                            "/tbot/ota/", self.ota_handler.handle_options
                        ),
                        # Download API, only provide data/bin/*.bin Download
                        web.get(
                            "/tbot/ota/download/{filename}",
                            self.ota_handler.handle_download,
                        ),
                        web.options(
                            "/tbot/ota/download/{filename}",
                            self.ota_handler.handle_options,
                        ),
                    ]
                )
                # Add Route
                app.add_routes(
                    [
                        web.get("/mcp/vision/explain", self.vision_handler.handle_get),
                        web.post(
                            "/mcp/vision/explain", self.vision_handler.handle_post
                        ),
                        web.options(
                            "/mcp/vision/explain", self.vision_handler.handle_options
                        ),
                    ]
                )
                app.add_routes(
                    [
                        web.post(
                            "/internal/devices/{deviceId}/lesson-nudge",
                            self.lesson_nudge_handler.handle_post,
                        ),
                        web.post(
                            "/internal/devices/{deviceId}/lesson-child-response",
                            self.lesson_nudge_handler.handle_child_response_post,
                        ),
                        web.post(
                            "/internal/devices/{deviceId}/mcp-call",
                            self.device_mcp_admin_handler.handle_post,
                        ),
                        # Admin/backend: after lesson pack lands in ESP cache, fan-out
                        # self.lesson_assets.sync_to_sd to online robots (queue offline).
                        web.post(
                            "/internal/lesson-assets/sd-fanout",
                            self.lesson_sd_fanout_handler.handle_post,
                        ),
                        web.get(
                            "/internal/lesson-assets/sd-fanout/pending",
                            self.lesson_sd_fanout_handler.handle_get_pending,
                        ),
                        web.get(
                            "/internal/lesson-runtime/preload-voice-alarm",
                            self.handle_preload_voice_alarm_snapshot,
                        ),
                        web.post(
                            "/internal/lesson-runtime/preload-voice-alarm/reset",
                            self.handle_preload_voice_alarm_reset,
                        ),
                        web.get(
                            "/internal/lesson-runtime/metrics",
                            self.handle_lesson_runtime_metrics,
                        ),
                        web.get(
                            "/tbot/lesson-assets/{cacheToken}/{assetKey}",
                            self.lesson_asset_handler.handle_get,
                        ),
                        web.get(
                            "/tbot/assign/",
                            self.lesson_assignment_console_handler.handle_get,
                        ),
                    ]
                )

                # Run Service
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, host, port)
                await site.start()

                # Keep service running
                while True:
                    await asyncio.sleep(3600)  # Every 1 Check once per hour
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"HTTP server start failed: {e}")
            import traceback

            self.logger.bind(tag=TAG).error(f"Error stack: {traceback.format_exc()}")
            raise

    def _preload_voice_alarm_snapshots(self):
        devices = []
        for device_id in sorted(str(device_id) for device_id in self.lesson_connections.keys()):
            connection = self.lesson_connections.get(device_id)
            alarm = getattr(connection, "lesson_voice_alarm", None)
            if alarm is None or not hasattr(alarm, "snapshot"):
                continue
            snapshot = alarm.snapshot()
            if isinstance(snapshot, dict):
                devices.append({"deviceId": device_id, **snapshot})
        return devices

    async def handle_preload_voice_alarm_snapshot(self, _request):
        devices = self._preload_voice_alarm_snapshots()
        payload = {
            "connections": len(self.lesson_connections),
            "alarms": len(devices),
            "devices": devices,
        }
        return web.Response(
            status=200,
            text=json.dumps(payload, sort_keys=True),
            content_type="application/json",
        )

    async def handle_preload_voice_alarm_reset(self, _request):
        reset_count = 0
        for connection in self.lesson_connections.values():
            alarm = getattr(connection, "lesson_voice_alarm", None)
            reset = getattr(alarm, "reset", None)
            if not callable(reset):
                continue
            reset()
            reset_count += 1
        payload = {
            "connections": len(self.lesson_connections),
            "reset": reset_count,
        }
        return web.Response(
            status=200,
            text=json.dumps(payload, sort_keys=True),
            content_type="application/json",
        )

    @staticmethod
    def _counter_value(value):
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            return 0
        return max(0, numeric)

    def _runtime_forwarder_metrics(self):
        devices = []
        approved_identities = approved_identities_from_config(self.config)
        forwarder_dropped_total = 0
        safety_forwarder_dropped_total = 0
        alarms = 0

        for device_id in sorted(str(device_id) for device_id in self.lesson_connections.keys()):
            connection = self.lesson_connections.get(device_id)
            runtime = getattr(connection, "lesson_runtime", None)
            forwarder = getattr(runtime, "forwarder", None)
            safety_forwarder = getattr(connection, "safety_event_forwarder", None)
            forwarder_dropped = self._counter_value(
                getattr(forwarder, "dropped_events_total", 0)
            )
            safety_dropped = self._counter_value(
                getattr(safety_forwarder, "dropped_events_total", 0)
            )
            alarm_payload = None
            alarm = getattr(connection, "lesson_voice_alarm", None)
            snapshot = getattr(alarm, "snapshot", None)
            if callable(snapshot):
                candidate = snapshot()
                if isinstance(candidate, dict):
                    alarm_payload = candidate
                    alarms += 1

            forwarder_dropped_total += forwarder_dropped
            safety_forwarder_dropped_total += safety_dropped
            client_id = getattr(connection, "client_id", None)
            headers = getattr(connection, "headers", None)
            if not client_id and hasattr(headers, "get"):
                client_id = headers.get("client-id") or headers.get("Client-Id")
            device = {
                "deviceId": device_id,
                "forwarderDroppedEventsTotal": forwarder_dropped,
                "safetyForwarderDroppedEventsTotal": safety_dropped,
                "alarm": alarm_payload,
            }
            if client_id:
                device["clientId"] = str(client_id)
            device.update(esp_build_identity_metrics_fields(headers, approved_identities))
            devices.append(device)

        return {
            "connections": len(self.lesson_connections),
            "alarms": alarms,
            "counters": {
                "forwarder.dropped_events_total": forwarder_dropped_total,
                "safety_forwarder.dropped_events_total": safety_forwarder_dropped_total,
            },
            "devices": devices,
        }

    async def handle_lesson_runtime_metrics(self, _request):
        return web.Response(
            status=200,
            text=json.dumps(self._runtime_forwarder_metrics(), sort_keys=True),
            content_type="application/json",
        )
