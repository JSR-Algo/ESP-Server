from scripts.check_python_runtime import require_supported_runtime

if __name__ == "__main__":
    require_supported_runtime()

import asyncio
import os
import signal
import sys
import uuid
from contextlib import suppress

try:
    from aioconsole import ainput
except ModuleNotFoundError:

    async def ainput(prompt: str = ""):
        return await asyncio.to_thread(input, prompt)
from config.config_loader import get_project_dir, load_config_async
from config.logger import setup_logging
from core.http_server import SimpleHttpServer
from core.lesson.sd_pack_retry_worker import LessonSdOnlineIndex
from core.utils.gc_manager import get_gc_manager
from core.utils.util import check_ffmpeg_installed, get_local_ip, validate_mcp_endpoint
from core.websocket_server import WebSocketServer

# Pre-import Google Live client at server startup. This forces the heavy
# google.genai SDK import (~95-105s on first import: protobuf, grpc, auth
# transitives) to happen BEFORE any device connects. Without this, the first
# device to use voice_mode=google_live triggers the lazy import during its
# WebSocket handshake, which exceeds the device-side WS idle timeout and the
# device disconnects mid-init ("Đang kết nối → chờ → văng").
# Cost is paid once at server boot where time doesn't matter.
with suppress(ImportError):
    import core.voice.google_live.client  # noqa: F401 — triggers eager genai import

TAG = __name__
logger = setup_logging()
PLACEHOLDER_MARKERS = ("your-", "your ", "You", "Your")
AUTH_KEY_FILE = "data/.auth_key"


def install_uvloop_policy() -> None:
    import uvloop

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


def _contains_placeholder(value) -> bool:
    if not isinstance(value, str):
        return False
    return any(marker in value for marker in PLACEHOLDER_MARKERS)

def _resolve_auth_key(config) -> str:
    """Return one stable key for OTA token minting and WebSocket verify.

    Public OTA and WS can be exposed through different tunnels or processes.
    A random in-memory fallback makes a token minted by one process unverifiable
    by another, so persist the generated fallback under data/ when no explicit
    config or manager secret is available.
    """
    auth_key = config.get("server", {}).get("auth_key", "")
    if auth_key and not _contains_placeholder(auth_key):
        return auth_key

    auth_key = config.get("manager-api", {}).get("secret", "")
    if auth_key and not _contains_placeholder(auth_key):
        return auth_key

    key_path = os.path.join(get_project_dir(), AUTH_KEY_FILE)
    try:
        with open(key_path, encoding="utf-8") as file:
            auth_key = file.read().strip()
        if auth_key and not _contains_placeholder(auth_key):
            return auth_key
    except FileNotFoundError:
        pass

    auth_key = uuid.uuid4().hex
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    with open(key_path, "w", encoding="utf-8") as file:
        file.write(auth_key + "\n")
    with suppress(OSError):
        os.chmod(key_path, 0o600)
    return auth_key


def _lesson_sd_api_base(config) -> str:
    lesson_cfg = config.get("lesson", {}) if isinstance(config, dict) else {}
    server_cfg = config.get("server", {}) if isinstance(config, dict) else {}
    if not isinstance(lesson_cfg, dict):
        lesson_cfg = {}
    if not isinstance(server_cfg, dict):
        server_cfg = {}
    return str(lesson_cfg.get("api_base") or server_cfg.get("api_url") or "").rstrip("/")


def _build_servers(config):
    lesson_sd_online_index = LessonSdOnlineIndex(api_base=_lesson_sd_api_base(config))
    ws_server = WebSocketServer(
        config,
        lesson_sd_online_index=lesson_sd_online_index,
    )
    ota_server = SimpleHttpServer(
        config,
        ws_server.lesson_connections,
        lesson_sd_online_index=lesson_sd_online_index,
    )
    return ws_server, ota_server


async def wait_for_exit() -> None:
    """
    Block until Ctrl-C / SIGTERM arrives.
    - Unix: use add_signal_handler
    - Windows: rely on KeyboardInterrupt
    """
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    if sys.platform != "win32":  # Unix / macOS
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)
        await stop_event.wait()
    else:
        # Keep process alive until Ctrl-C breaks asyncio.run().
        with suppress(KeyboardInterrupt):
            await asyncio.Future()


async def monitor_stdin():
    """Monitor standard input, consume Enter key"""
    while True:
        await ainput()  # Async wait for input, consume Enter.


async def main():
    check_ffmpeg_installed()
    config = await load_config_async()
    logger.bind(tag=TAG).info(
        "voice_mode={}",
        config.get("voice_mode", {}).get("type", "classic_pipeline"),
    )

    # auth_key is used for OTA token generation and WebSocket verification.
    config["server"]["auth_key"] = _resolve_auth_key(config)

    # Add stdin monitor task.
    stdin_task = asyncio.create_task(monitor_stdin())

    # Start global GC manager.
    gc_manager = get_gc_manager(interval_seconds=300)
    await gc_manager.start()

    # Start websocket and HTTP servers with one shared lesson SD online index.
    ws_server, ota_server = _build_servers(config)
    ws_task = asyncio.create_task(ws_server.start())
    ota_task = asyncio.create_task(ota_server.start())

    read_config_from_api = config.get("read_config_from_api", False)
    port = int(config["server"].get("http_port", 8003))
    if not read_config_from_api:
        logger.bind(tag=TAG).info(
            "OTAInterface is\t\thttp://{}:{}/tbot/ota/",
            get_local_ip(),
            port,
        )
    logger.bind(tag=TAG).info(
        "Vision analysis endpoint is\thttp://{}:{}/mcp/vision/explain",
        get_local_ip(),
        port,
    )
    mcp_endpoint = config.get("mcp_endpoint", None)
    if mcp_endpoint is not None and not _contains_placeholder(mcp_endpoint):
        # Validate MCP endpoint format.
        if validate_mcp_endpoint(mcp_endpoint):
            logger.bind(tag=TAG).info("mcp endpoint is\t{}", mcp_endpoint)
            # Convert MCP endpoint into call endpoint.
            mcp_endpoint = mcp_endpoint.replace("/mcp/", "/call/")
            config["mcp_endpoint"] = mcp_endpoint
        else:
            logger.bind(tag=TAG).error("mcp endpoint does not meet spec")
            config["mcp_endpoint"] = "your MCP websocket endpoint"

    # Read websocket config with safe default.
    websocket_port = 8000
    websocket_url = None
    server_config = config.get("server", {})
    if isinstance(server_config, dict):
        websocket_port = int(server_config.get("port", 8000))
        configured_websocket = server_config.get("websocket")
        if configured_websocket and not _contains_placeholder(configured_websocket):
            websocket_url = configured_websocket
    if not websocket_url:
        websocket_url = f"ws://{get_local_ip()}:{websocket_port}/tbot/v1/"

    logger.bind(tag=TAG).info(
        "WebsocketAddress is\t{}",
        websocket_url,
    )

    logger.bind(tag=TAG).info(
        "=======Address above is websocket protocol address. Do not visit with browser======="
    )
    logger.bind(tag=TAG).info(
        "To test websocket, start digital-human module and open browser interaction test"
    )
    logger.bind(tag=TAG).info(
        "=============================================================\n"
    )

    try:
        await wait_for_exit()  # Block until exit signal arrives.
    except asyncio.CancelledError:
        print("Task canceled, cleaning resources...")
    finally:
        # Stop accepting new device sockets and let active sessions drain before
        # task cancellation. This keeps rolling deploys from dropping every child
        # session at once.
        try:
            await ws_server.drain(
                timeout=float(
                    config.get("server", {}).get("graceful_drain_timeout_sec", 30)
                )
            )
        except Exception as exc:
            logger.bind(tag=TAG).warning("WebSocket drain failed: {}", exc)

        # Stop global GC manager.
        await gc_manager.stop()

        # Cancel all tasks.
        stdin_task.cancel()
        ws_task.cancel()
        if ota_task:
            ota_task.cancel()

        # Wait for task termination with timeout.
        await asyncio.wait(
            [stdin_task, ws_task, ota_task] if ota_task else [stdin_task, ws_task],
            timeout=3.0,
            return_when=asyncio.ALL_COMPLETED,
        )
        print("Server closed, program exited.")


if __name__ == "__main__":
    try:
        install_uvloop_policy()
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Manually interrupted, program terminated.")
