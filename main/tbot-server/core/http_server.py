import asyncio
import inspect
import json
import os
from typing import Protocol, runtime_checkable

from aiohttp import web

from config.logger import setup_logging
from core.api.device_mcp_admin_handler import DeviceMCPAdminHandler
from core.api.generation_retry_handler import GenerationRetryHandler
from core.api.lesson_asset_handler import LessonAssetHandler
from core.api.lesson_assignment_console_handler import LessonAssignmentConsoleHandler
from core.api.lesson_nudge_handler import LessonNudgeHandler
from core.api.lesson_sd_evict_handler import LessonSdEvictHandler
from core.api.lesson_sd_fanout_handler import LessonSdFanoutHandler
from core.api.lesson_sd_materialize_handler import LessonSdMaterializeHandler
from core.lesson import runtime_counters as lesson_runtime_counters
from core.api.ota_handler import OTAHandler, is_placeholder_websocket_url
from core.api.vision_handler import VisionHandler
from core.lesson.esp_build_identity import (
    approved_identities_from_config,
    esp_build_identity_metrics_fields,
)
from core.lesson.sd_pack_fanout import get_pending_store
from core.lesson.sd_pack_retry_worker import LessonSdOnlineIndex, LessonSdRetryWorker

TAG = __name__


class _GenerationPoller(Protocol):
    def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def run_once(self) -> dict[str, str]: ...

    async def trigger_retry(self) -> dict[str, str]: ...


class _GenerationStatus(Protocol):
    async def snapshot(self) -> dict[str, object]: ...


@runtime_checkable
class _RunnerCleanup(Protocol):
    async def cleanup(self) -> None: ...


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() == "true"


async def _cleanup_runner(runner: object) -> None:
    if isinstance(runner, _RunnerCleanup):
        await runner.cleanup()


class SimpleHttpServer:
    def __init__(
        self,
        config: dict,
        lesson_connections=None,
        *,
        lesson_sd_online_index=None,
        generation_poller: _GenerationPoller | None = None,
        generation_status: _GenerationStatus | None = None,
        generation_redis=None,
        owns_generation_redis=False,
    ):
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
        self.lesson_sd_pending_store = get_pending_store()
        lesson_cfg = config.get("lesson", {}) if isinstance(config, dict) else {}
        server_cfg = config.get("server", {}) if isinstance(config, dict) else {}
        if not isinstance(lesson_cfg, dict):
            lesson_cfg = {}
        if not isinstance(server_cfg, dict):
            server_cfg = {}
        self.lesson_sd_online_index = lesson_sd_online_index or LessonSdOnlineIndex(
            api_base=str(
                lesson_cfg.get("api_base") or server_cfg.get("api_url") or ""
            ).rstrip("/")
        )
        self.lesson_sd_retry_worker = LessonSdRetryWorker(
            self.lesson_sd_pending_store,
            self.lesson_sd_online_index,
            connections=self.lesson_connections,
        )
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
            pending_store=self.lesson_sd_pending_store,
            online_index=self.lesson_sd_online_index,
        )
        self.lesson_sd_evict_handler = LessonSdEvictHandler(
            config,
            self.lesson_connections,
            pending_store=self.lesson_sd_pending_store,
        )
        self.lesson_sd_materialize_handler = LessonSdMaterializeHandler(
            config,
            self.lesson_connections,
        )
        self.generation_poller = generation_poller
        self.generation_retry_handler = GenerationRetryHandler(generation_poller)
        self.generation_status = generation_status
        self.generation_redis = generation_redis
        self.owns_generation_redis = bool(owns_generation_redis)
        self._background_started = False
        self._background_stopped = False
        self._background_stop_lock = asyncio.Lock()
        self._poller_started = False
        self._poller_stopped = False
        self._legacy_worker_started = False
        self._legacy_worker_stopped = False
        self._generation_redis_closed = False

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
        runner = None
        try:
            server_config = self.config["server"]
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
                            "/internal/devices/{deviceId}/lesson-assets/evict-cache-key",
                            self.lesson_sd_evict_handler.handle_post,
                        ),
                        web.post(
                            "/internal/devices/{deviceId}/mcp-call",
                            self.device_mcp_admin_handler.handle_post,
                        ),
                        # Admin/backend: after lesson pack lands in ESP cache, fan-out
                        # self.lesson_assets.sync_to_sd to online robots (queue offline).
                        web.post(
                            "/internal/lesson-assets/generation/retry",
                            self.generation_retry_handler.handle_post,
                        ),
                        web.post(
                            "/internal/lesson-assets/sd-fanout",
                            self.lesson_sd_fanout_handler.handle_post,
                        ),
                        web.post(
                            "/internal/lesson-assets/materialize",
                            self.lesson_sd_materialize_handler.handle_post,
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
                if self.generation_status is not None:
                    app.add_routes(
                        [
                            web.get(
                                "/public/lesson-assets/generation",
                                self.handle_generation_status,
                            )
                        ]
                    )

                # Run Service
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, host, port)
                await site.start()
                self.start_background_services()

                # Keep service running
                try:
                    while True:
                        await asyncio.sleep(3600)  # Every 1 Check once per hour
                finally:
                    await self.stop_background_services()
                    await _cleanup_runner(runner)
                    runner = None
        except asyncio.CancelledError:
            if runner is not None:
                await self.stop_background_services()
                await _cleanup_runner(runner)
            raise
        except Exception:
            if runner is not None:
                await self.stop_background_services()
                await _cleanup_runner(runner)
            self.logger.bind(tag=TAG).error("HTTP server start failed")
            raise

    def start_background_services(self) -> None:
        if self._background_started:
            return
        self._background_started = True
        poller = self.generation_poller
        if poller is not None:
            self._poller_started = True
            poller.start()
        if _env_enabled("TBOT_ENABLE_BACKGROUND_WORKERS") and _env_enabled(
            "LESSON_SD_LEGACY_DEVICE_WORKER_ENABLED"
        ):
            self.lesson_sd_retry_worker.start()
            self._legacy_worker_started = True

    async def stop_background_services(self) -> None:
        async with self._background_stop_lock:
            if self._background_stopped:
                return
            failed = False
            poller = self.generation_poller
            if poller is not None and not self._poller_stopped:
                try:
                    await poller.stop()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    failed = True
                else:
                    self._poller_stopped = True
            if self._legacy_worker_started and not self._legacy_worker_stopped:
                try:
                    await self.lesson_sd_retry_worker.stop()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    failed = True
                else:
                    self._legacy_worker_stopped = True
            if self.owns_generation_redis and not self._generation_redis_closed:
                close = getattr(self.generation_redis, "aclose", None) or getattr(
                    self.generation_redis, "close", None
                )
                try:
                    if callable(close):
                        result = close()
                        if inspect.isawaitable(result):
                            await result
                except asyncio.CancelledError:
                    raise
                except Exception:
                    failed = True
                else:
                    self._generation_redis_closed = True
            poller_done = poller is None or self._poller_stopped
            legacy_done = not self._legacy_worker_started or self._legacy_worker_stopped
            redis_done = not self.owns_generation_redis or self._generation_redis_closed
            self._background_stopped = poller_done and legacy_done and redis_done
            if failed:
                raise RuntimeError("generation_background_stop_failed") from None

    async def handle_generation_status(self, _request):
        try:
            status = self.generation_status
            if status is None:
                raise RuntimeError("generation status unavailable")
            payload = await status.snapshot()
        except asyncio.CancelledError:
            raise
        except Exception:
            return web.json_response(
                {"error": "generation_status_unavailable"},
                status=503,
                headers={"Cache-Control": "no-store"},
            )
        return web.json_response(
            payload,
            status=200,
            headers={"Cache-Control": "public, max-age=5"},
        )

    @staticmethod
    def _alarm_snapshot(alarm):
        """Read one connection's alarm without letting it fail the whole sweep.

        These endpoints fan out over every live connection, including ones being
        torn down or superseded mid-request; one bad connection must degrade to a
        missing row, not a 500 for every other device.
        """
        snapshot = getattr(alarm, "snapshot", None)
        if not callable(snapshot):
            return None
        try:
            candidate = snapshot()
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        return candidate if isinstance(candidate, dict) else None

    def _preload_voice_alarm_snapshots(self):
        devices = []
        for device_id in sorted(str(device_id) for device_id in self.lesson_connections):
            connection = self.lesson_connections.get(device_id)
            snapshot = self._alarm_snapshot(getattr(connection, "lesson_voice_alarm", None))
            if snapshot is not None:
                devices.append({"deviceId": device_id, **snapshot})
        return devices

    def _authorize_internal(self, request):
        """X-Mint-Secret gate shared by every /internal/ route.

        The lesson_* handlers each authorize through LessonNudgeHandler._authorize
        (see LessonSdMaterializeHandler / GenerationRetryHandler); the routes served
        directly off this class must use the same gate or they sit unauthenticated
        in an otherwise authenticated namespace.
        """
        return self.lesson_nudge_handler._authorize(request)

    async def handle_preload_voice_alarm_snapshot(self, request):
        auth_error = self._authorize_internal(request)
        if auth_error is not None:
            return auth_error

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

    async def handle_preload_voice_alarm_reset(self, request):
        auth_error = self._authorize_internal(request)
        if auth_error is not None:
            return auth_error

        reset_count = 0
        for connection in tuple(self.lesson_connections.values()):
            alarm = getattr(connection, "lesson_voice_alarm", None)
            reset = getattr(alarm, "reset", None)
            if not callable(reset):
                continue
            try:
                reset()
            except asyncio.CancelledError:
                raise
            except Exception:
                continue
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

        for device_id in sorted(str(device_id) for device_id in self.lesson_connections):
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
            alarm_payload = self._alarm_snapshot(
                getattr(connection, "lesson_voice_alarm", None)
            )
            if alarm_payload is not None:
                alarms += 1

            forwarder_dropped_total += forwarder_dropped
            safety_forwarder_dropped_total += safety_dropped
            client_id = getattr(connection, "client_id", None)
            headers = getattr(connection, "headers", None)
            if not client_id and headers is not None and hasattr(headers, "get"):
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
                # T6.2: process-level reconnect-storm signals (T2.4 finding).
                **lesson_runtime_counters.snapshot(),
            },
            "devices": devices,
        }

    async def handle_lesson_runtime_metrics(self, request):
        auth_error = self._authorize_internal(request)
        if auth_error is not None:
            return auth_error

        return web.Response(
            status=200,
            text=json.dumps(self._runtime_forwarder_metrics(), sort_keys=True),
            content_type="application/json",
        )
