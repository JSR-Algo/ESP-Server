import asyncio
from aiohttp import web
from config.logger import setup_logging
from core.api.ota_handler import OTAHandler, is_placeholder_websocket_url
from core.api.vision_handler import VisionHandler
from core.api.lesson_nudge_handler import LessonNudgeHandler
from core.api.lesson_asset_handler import LessonAssetHandler
from core.api.lesson_assignment_console_handler import LessonAssignmentConsoleHandler

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
        self.lesson_nudge_handler = LessonNudgeHandler(
            config,
            lesson_connections if lesson_connections is not None else {},
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
