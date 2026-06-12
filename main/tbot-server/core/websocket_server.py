import asyncio
import logging
import warnings

warnings.filterwarnings(
    "ignore",
    message=r"urllib3 v2 only supports OpenSSL 1\.1\.1\+.*",
    category=Warning,
)
warnings.filterwarnings(
    "ignore",
    message=r"'audioop' is deprecated and slated for removal in Python 3\.13",
    category=DeprecationWarning,
)

import websockets
from config.logger import setup_logging


class SuppressInvalidHandshakeFilter(logging.Filter):
    """Filter invalid handshake error logs (such as HTTPS access to WS port)"""

    def filter(self, record):
        msg = record.getMessage()
        suppress_keywords = [
            "opening handshake failed",
            "did not receive a valid HTTP request",
            "connection closed while reading HTTP request",
            "line without CRLF",
        ]
        return not any(keyword in msg for keyword in suppress_keywords)


def _setup_websockets_logger():
    """Configure all websockets related loggers, filter invalid handshake errors"""
    filter_instance = SuppressInvalidHandshakeFilter()
    for logger_name in ["websockets", "websockets.server", "websockets.client"]:
        logger = logging.getLogger(logger_name)
        logger.addFilter(filter_instance)


_setup_websockets_logger()


from config.config_loader import get_config_from_api_async
from core.auth import AuthManager, AuthenticationError
from core.utils.modules_initialize import initialize_modules
from core.utils.util import check_vad_update, check_asr_update

TAG = __name__


class WebSocketServer:
    def __init__(self, config: dict):
        self.config = config
        self.logger = setup_logging()
        self.config_lock = asyncio.Lock()
        voice_mode_config = self.config.get("voice_mode", {})
        init_classic_bootstrap = not (
            isinstance(voice_mode_config, dict)
            and voice_mode_config.get("type") == "google_live"
        )
        skip_manager_bootstrap = self.config.get("read_config_from_api", False)
        try:
            modules = initialize_modules(
                self.logger,
                self.config,
                init_classic_bootstrap and (not skip_manager_bootstrap) and "VAD" in self.config["selected_module"],
                init_classic_bootstrap and (not skip_manager_bootstrap) and "ASR" in self.config["selected_module"],
                init_classic_bootstrap and (not skip_manager_bootstrap) and "LLM" in self.config["selected_module"],
                False,
                init_classic_bootstrap and (not skip_manager_bootstrap) and "Memory" in self.config["selected_module"],
                init_classic_bootstrap and (not skip_manager_bootstrap) and "Intent" in self.config["selected_module"],
            )
        except Exception as e:
            if not self.config.get("read_config_from_api", False):
                raise
            self.logger.bind(tag=TAG).warning(
                f"Bootstrap module initialization failed in manager mode, continuing until device private config is loaded: {e}"
            )
            modules = initialize_modules(
                self.logger,
                self.config,
                False,
                False,
                False,
                False,
                False,
                False,
            )
        self._vad = modules["vad"] if "vad" in modules else None
        self._asr = modules["asr"] if "asr" in modules else None
        self._llm = modules["llm"] if "llm" in modules else None
        self._intent = modules["intent"] if "intent" in modules else None
        self._memory = modules["memory"] if "memory" in modules else None

        auth_config = self.config["server"].get("auth", {})
        self.auth_enable = auth_config.get("enabled", False)
        # Device whitelist
        self.allowed_devices = set(auth_config.get("allowed_devices", []))
        secret_key = self.config["server"]["auth_key"]
        expire_seconds = auth_config.get("expire_seconds", None)
        self.auth = AuthManager(secret_key=secret_key, expire_seconds=expire_seconds)

    async def start(self):
        server_config = self.config["server"]
        host = server_config.get("ip", "0.0.0.0")
        port = int(server_config.get("port", 8000))

        async with websockets.serve(
            self._handle_connection, host, port, process_request=self._http_response
        ):
            await asyncio.Future()

    async def _handle_connection(self, websocket: websockets.ServerConnection):
        self._copy_query_identity_headers(websocket)
        headers = dict(websocket.request.headers)
        if headers.get("device-id", None) is None:
            if not getattr(websocket.request, "path", ""):
                self.logger.bind(tag=TAG).error("Cannot get request path")
                await websocket.close()
                return
            await websocket.send(
                "Port normal. To test connection, start digital-human test."
            )
            await websocket.close()
            return

        """Handle new connection, create independent ConnectionHandler each time"""
        # Authenticate first, then connect
        try:
            await self._handle_auth(websocket)
        except AuthenticationError:
            await websocket.send("Authentication failed")
            await websocket.close()
            return
        # CreateConnectionHandlerPass current whenserverInstance
        from core.connection import ConnectionHandler

        handler = ConnectionHandler(
            self.config,
            self._vad,
            self._asr,
            self._llm,
            self._memory,
            self._intent,
            self,  # Pass inserverInstance
        )
        try:
            await handler.handle_connection(websocket)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Error handling connection: {e}")
        finally:
            # Force close connection (if not closed yet)
            try:
                # Safely checkWebSocketStatusAnd close
                if hasattr(websocket, "closed") and not websocket.closed:
                    await websocket.close()
                elif hasattr(websocket, "state") and websocket.state.name != "CLOSED":
                    await websocket.close()
                else:
                    # If noneclosedAttribute, try close directly
                    await websocket.close()
            except Exception as close_error:
                self.logger.bind(tag=TAG).error(
                    f"Error when server forcibly closed connection: {close_error}"
                )

    @staticmethod
    def _copy_query_identity_headers(websocket):
        """Accept firmware/browser query auth even when device headers are present."""
        from urllib.parse import parse_qs, urlparse

        request_path = getattr(websocket.request, "path", "")
        if not request_path:
            return
        query_params = parse_qs(urlparse(request_path).query)
        for name in ("device-id", "client-id", "authorization"):
            if websocket.request.headers.get(name, None) is None and name in query_params:
                websocket.request.headers[name] = query_params[name][0]

    async def _http_response(self, websocket, request_headers):
        # Check whether is WebSocket Upgrade Request
        if request_headers.headers.get("connection", "").lower() == "upgrade":
            # If is WebSocket Request, return None Allow handshake continue
            return None
        else:
            # If normal HTTP Request, return "server is running"
            return websocket.respond(200, "Server is running\n")

    async def update_config(self) -> bool:
        """Update server config and reinitialize components

        Returns:
            bool: whether update succeeded
        """
        try:
            async with self.config_lock:
                # Re-get config (use async version)
                new_config = await get_config_from_api_async(self.config)
                if new_config is None:
                    self.logger.bind(tag=TAG).error("Get new config failed")
                    return False
                self.logger.bind(tag=TAG).info(f"Get new config succeeded")
                # Check VAD and ASR Whether type needs update
                update_vad = check_vad_update(self.config, new_config)
                update_asr = check_asr_update(self.config, new_config)
                self.logger.bind(tag=TAG).info(
                    f"Check whether VAD and ASR types need update: {update_vad} {update_asr}"
                )
                # Update config
                self.config = new_config
                # AgainInitialize component
                modules = initialize_modules(
                    self.logger,
                    new_config,
                    update_vad,
                    update_asr,
                    "LLM" in new_config["selected_module"],
                    False,
                    "Memory" in new_config["selected_module"],
                    "Intent" in new_config["selected_module"],
                )

                # Update component instance
                if "vad" in modules:
                    self._vad = modules["vad"]
                if "asr" in modules:
                    self._asr = modules["asr"]
                if "llm" in modules:
                    self._llm = modules["llm"]
                if "intent" in modules:
                    self._intent = modules["intent"]
                if "memory" in modules:
                    self._memory = modules["memory"]
                self.logger.bind(tag=TAG).info(f"Update config task completed")
                return True
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Failed to update server config: {str(e)}")
            return False

    async def _handle_auth(self, websocket: websockets.ServerConnection):
        # Authenticate first, then connect
        if self.auth_enable:
            headers = dict(websocket.request.headers)
            device_id = headers.get("device-id", None)
            client_id = headers.get("client-id", None)
            if self.allowed_devices and device_id in self.allowed_devices:
                # IfBelongs toDevices in whitelist, no validationtokendirectly allow
                return
            else:
                # Otherwise Verifytoken
                token = headers.get("authorization", "")
                if token.startswith("Bearer "):
                    token = token[7:]  # Remove'Bearer 'Prefix
                else:
                    raise AuthenticationError("Missing or invalid Authorization header")
                # Authenticate
                auth_success = self.auth.verify_token(
                    token, client_id=client_id, username=device_id
                )
                if not auth_success:
                    raise AuthenticationError("Invalid token")
