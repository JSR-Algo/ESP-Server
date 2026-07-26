from scripts.check_python_runtime import require_supported_runtime

if __name__ == "__main__":
    require_supported_runtime()

import asyncio
import inspect
import os
import signal
import sys
import uuid
from contextlib import suppress
from typing import cast

try:
    from aioconsole import ainput
except ModuleNotFoundError:

    async def ainput(prompt: str = ""):
        return await asyncio.to_thread(input, prompt)
from config.config_loader import get_project_dir, load_config_async
from config.logger import setup_logging
from core.http_server import SimpleHttpServer
from core.lesson.global_generation_poller import GlobalGenerationPoller
from core.lesson.global_generation_sessions import GlobalGenerationSessions
from core.lesson.global_generation_status import GlobalGenerationStatus
from core.lesson.global_generation_store import GlobalGenerationStore
from core.lesson.global_generation_sync import GlobalGenerationSync
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


def _generation_enabled(config) -> bool:
    env_value = os.environ.get("LESSON_GENERATION_CMS_URL")
    if isinstance(env_value, str) and env_value.strip():
        return True
    lesson_cfg = config.get("lesson", {}) if isinstance(config, dict) else {}
    return bool(
        isinstance(lesson_cfg, dict)
        and isinstance(lesson_cfg.get("generation_cms_url"), str)
        and lesson_cfg["generation_cms_url"].strip()
    )


def _build_servers(
    config,
    *,
    websocket_server_factory=None,
    http_server_factory=None,
    redis_factory=None,
    store_factory=None,
    sessions_factory=None,
    sync_factory=None,
    poller_factory=None,
    status_factory=None,
):
    websocket_server_factory = websocket_server_factory or WebSocketServer
    http_server_factory = http_server_factory or SimpleHttpServer
    lesson_sd_online_index = LessonSdOnlineIndex(api_base=_lesson_sd_api_base(config))
    if _generation_enabled(config):
        raise RuntimeError("enabled generation servers require _build_servers_async")
    ws_server = websocket_server_factory(
        config,
        lesson_sd_online_index=lesson_sd_online_index,
    )
    ota_server = http_server_factory(
        config,
        ws_server.lesson_connections,
        lesson_sd_online_index=lesson_sd_online_index,
    )
    return ws_server, ota_server


async def _close_async_resource(resource) -> None:
    close = getattr(resource, "aclose", None) or getattr(resource, "close", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result


async def _build_servers_async(
    config,
    *,
    websocket_server_factory=None,
    http_server_factory=None,
    redis_factory=None,
    store_factory=None,
    sessions_factory=None,
    sync_factory=None,
    poller_factory=None,
    status_factory=None,
):
    if not _generation_enabled(config):
        return _build_servers(
            config,
            websocket_server_factory=websocket_server_factory,
            http_server_factory=http_server_factory,
        )

    redis_url = os.environ.get("REDIS_URL")
    if not isinstance(redis_url, str) or not redis_url.strip():
        raise RuntimeError("REDIS_URL is required for global lesson generation")
    if redis_factory is None:
        from redis import asyncio as redis_asyncio

        redis_factory = redis_asyncio.from_url
    store_factory = store_factory or GlobalGenerationStore
    sessions_factory = sessions_factory or GlobalGenerationSessions
    sync_factory = sync_factory or GlobalGenerationSync
    poller_factory = poller_factory or GlobalGenerationPoller
    status_factory = status_factory or GlobalGenerationStatus

    redis = None
    poller = None
    try:
        redis = redis_factory(redis_url.strip(), decode_responses=True)
        store = store_factory(redis)
        sessions = sessions_factory(store.redis)

        async def fanout_adapter(generation, index_checksum, packs):
            return await sessions.fanout(
                generation=generation,
                index_checksum=index_checksum,
                packs=packs,
            )

        generation_sync = sync_factory(config, store, fanout_adapter)
        poller = poller_factory(config, store, generation_sync.apply)
        status = status_factory(store, sessions)
        lesson_sd_online_index = LessonSdOnlineIndex(api_base=_lesson_sd_api_base(config))
        websocket_server_factory = websocket_server_factory or WebSocketServer
        http_server_factory = http_server_factory or SimpleHttpServer
        ws_server = websocket_server_factory(
            config,
            lesson_sd_online_index=lesson_sd_online_index,
            global_generation_sessions=sessions,
        )
        ota_server = http_server_factory(
            config,
            ws_server.lesson_connections,
            lesson_sd_online_index=lesson_sd_online_index,
            generation_poller=poller,
            generation_status=status,
            generation_redis=redis,
            owns_generation_redis=True,
        )
        return ws_server, ota_server
    except BaseException:
        if poller is not None:
            with suppress(BaseException):
                await poller.stop()
        if redis is not None:
            with suppress(BaseException):
                await _close_async_resource(redis)
        raise


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
    ws_server, ota_server = await _build_servers_async(config)
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
            cast(dict, config)["mcp_endpoint"] = mcp_endpoint
        else:
            logger.bind(tag=TAG).error("mcp endpoint does not meet spec")
            cast(dict, config)["mcp_endpoint"] = "your MCP websocket endpoint"

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

        # Global sessions must unregister while Redis is alive. Only after the
        # websocket drain may the HTTP lifecycle stop the poller and close Redis.
        try:
            await ota_server.stop_background_services()
        except Exception:
            logger.bind(tag=TAG).warning("HTTP background service shutdown failed")

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
