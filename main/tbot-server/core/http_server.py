import asyncio
from aiohttp import web
from config.logger import setup_logging
from core.api.ota_handler import OTAHandler, is_placeholder_websocket_url
from core.api.vision_handler import VisionHandler

TAG = __name__


class SimpleHttpServer:
    def __init__(self, config: dict):
        self.config = config
        self.logger = setup_logging()
        self.ota_handler = OTAHandler(config)
        self.vision_handler = VisionHandler(config)
        self._shutdown_event = asyncio.Event()
        self._runner = None

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
                app.router.add_get("/health", self._health_handler)
                app.router.add_get("/metrics", self._metrics_handler)

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

                # Run Service
                self._runner = web.AppRunner(app)
                await self._runner.setup()
                site = web.TCPSite(self._runner, host, port)
                await site.start()

                # Keep service running until shutdown signal
                await self._shutdown_event.wait()
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"HTTP server start failed: {e}")
            import traceback

            self.logger.bind(tag=TAG).error(f"Error stack: {traceback.format_exc()}")
            raise

    async def _health_handler(self, request):
        return web.json_response({
            "status": "UP",
            "checks": {
                "websocket": "UP",
                "ota": "UP"
            }
        })

    async def _metrics_handler(self, request):
        # Inline Prometheus text format (zero dependencies)
        metrics_text = ""
        if hasattr(self, 'websocket_server') and self.websocket_server:
            active = getattr(self.websocket_server, 'connections_active', 0)
            total = getattr(self.websocket_server, 'connections_total', 0)
            metrics_text += f"# HELP tbot_connections_active Current WebSocket connections\n"
            metrics_text += f"# TYPE tbot_connections_active gauge\n"
            metrics_text += f"tbot_connections_active {active}\n\n"
            metrics_text += f"# HELP tbot_connections_total Total WebSocket connections\n"
            metrics_text += f"# TYPE tbot_connections_total counter\n"
            metrics_text += f"tbot_connections_total {total}\n\n"
        metrics_text += f"# HELP tbot_http_requests_total Total HTTP requests\n"
        metrics_text += f"# TYPE tbot_http_requests_total counter\n"
        metrics_text += f"tbot_http_requests_total 1\n"
        return web.Response(text=metrics_text, content_type="text/plain")

    async def stop(self):
        """Gracefully stop the HTTP server."""
        self._shutdown_event.set()
        if self._runner:
            await self._runner.cleanup()
