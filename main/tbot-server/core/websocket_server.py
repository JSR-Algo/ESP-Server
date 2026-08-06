import asyncio
import json
import logging
import os
import warnings
from contextlib import suppress

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

import websockets  # noqa: E402

from config.logger import setup_logging  # noqa: E402


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


from config.config_loader import get_config_from_api_async  # noqa: E402
from core.auth import AuthenticationError, AuthManager  # noqa: E402
from core.utils.modules_initialize import initialize_modules  # noqa: E402
from core.utils.util import check_asr_update, check_vad_update  # noqa: E402

TAG = __name__


def _header_values(headers, name):
    raw_items = getattr(headers, "raw_items", None)
    items = raw_items() if callable(raw_items) else headers.items()
    folded = name.casefold()
    return [str(value) for key, value in items if str(key).casefold() == folded]


def _single_header(headers, name, default=None):
    values = _header_values(headers, name)
    if len(values) > 1:
        raise ValueError(f"duplicate {name} header")
    return values[0] if values else default


class WebSocketServer:
    def __init__(
        self,
        config: dict,
        *,
        lesson_sd_online_index=None,
        global_generation_sessions=None,
    ):
        from core.connection_registry import ConnectionRegistry

        self.config = config
        self.logger = setup_logging()
        self.config_lock = None
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
        self._vad = modules.get("vad", None)
        self._asr = modules.get("asr", None)
        self._llm = modules.get("llm", None)
        self._intent = modules.get("intent", None)
        self._memory = modules.get("memory", None)

        auth_config = self.config["server"].get("auth", {})
        self.auth_enable = auth_config.get("enabled", False)
        # Device whitelist
        self.allowed_devices = set(auth_config.get("allowed_devices", []))
        secret_key = self.config["server"]["auth_key"]
        expire_seconds = auth_config.get("expire_seconds", None)
        self.auth = AuthManager(
            secret_key=secret_key,
            expire_seconds=expire_seconds,
            device_revoked_after=auth_config.get("device_revoked_after", {}),
        )
        self.lesson_connections = ConnectionRegistry()
        self.lesson_sd_online_index = lesson_sd_online_index
        self.global_generation_sessions = global_generation_sessions
        self.accept_cap = self._resolve_accept_cap()
        self._active_device_connections = 0
        self.is_draining = False
        self._connection_tasks = set()
        self._server = None
        self._stop_event = None

    async def start(self):
        server_config = self.config["server"]
        host = server_config.get("ip", "0.0.0.0")
        port = int(server_config.get("port", 8000))

        self._stop_event = asyncio.Event()
        async with websockets.serve(
            self._handle_connection,
            host,
            port,
            process_request=self._http_response,
            # Robots use the JSON ping/pong contract. Protocol keepalive can
            # falsely close a healthy connection while an attended SD sync is
            # monopolizing the device's websocket callback for several minutes.
            ping_interval=None,
        ) as server:
            self._server = server
            await self._stop_event.wait()

    def begin_drain(self):
        self.is_draining = True

    async def drain(self, *, timeout=30.0):
        self.begin_drain()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        if self._stop_event is not None:
            self._stop_event.set()
        pending = [task for task in self._connection_tasks if not task.done()]
        if pending:
            done, still_pending = await asyncio.wait(pending, timeout=timeout)
            for task in still_pending:
                task.cancel()
            if still_pending:
                await asyncio.gather(*still_pending, return_exceptions=True)
            for task in done:
                with suppress(Exception):
                    task.result()

    async def _handle_connection(self, websocket: websockets.ServerConnection):
        current_task = asyncio.current_task()
        handler = None
        global_session_registered = False
        if current_task is not None:
            self._connection_tasks.add(current_task)
        try:
            self._copy_query_identity_headers(websocket)
            headers = websocket.request.headers
            try:
                device_id = _single_header(headers, "device-id")
            except ValueError:
                await websocket.send("Authentication failed")
                await websocket.close()
                return
            if device_id is None:
                if not getattr(websocket.request, "path", ""):
                    self.logger.bind(tag=TAG).error("Cannot get request path")
                    await websocket.close()
                    return
                await websocket.send(
                    "Port normal. To test connection, start digital-human test."
                )
                await websocket.close()
                return

            if self.is_draining:
                await self._reject_draining(websocket)
                return

            """Handle new connection, create independent ConnectionHandler each time"""
            # Authenticate first, then connect
            try:
                await self._handle_auth(websocket)
            except AuthenticationError:
                await websocket.send("Authentication failed")
                await websocket.close()
                return
            if self._is_over_accept_cap():
                await self._reject_accept_cap(websocket)
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
            if device_id:
                handler.device_id = device_id
                # Bind the socket before registering: from the moment the handler
                # is reachable in the registry, a supersede must be able to find
                # the socket to close. ``handle_connection`` re-binds the same
                # object a moment later.
                handler.websocket = websocket
                await self._issue_liveness_lease(handler, device_id)
                superseded = await self.lesson_connections.replace(device_id, handler)
                if superseded is not None:
                    self._scrap_superseded_connection(superseded, handler, device_id)
                sessions = self.global_generation_sessions
                if sessions is not None:
                    try:
                        await sessions.register(device_id, handler)
                        global_session_registered = True
                    except Exception as exc:
                        self.logger.bind(tag=TAG).warning(
                            "Global generation session register failed errorType={}",
                            type(exc).__name__,
                        )
            self._active_device_connections += 1
            try:
                await handler.handle_connection(websocket)
            except Exception as e:
                self.logger.bind(tag=TAG).error(f"Error handling connection: {e}")
            finally:
                self._active_device_connections = max(
                    0, self._active_device_connections - 1
                )
                if device_id:
                    await self.lesson_connections.remove_if_current(
                        device_id, handler
                    )
                    if global_session_registered:
                        try:
                            await self.global_generation_sessions.unregister(
                                device_id, handler
                            )
                        except Exception as exc:
                            self.logger.bind(tag=TAG).warning(
                                "Global generation session unregister failed errorType={}",
                                type(exc).__name__,
                            )
                index = getattr(self, "lesson_sd_online_index", None)
                invalidate = getattr(index, "invalidate_connection", None)
                if callable(invalidate):
                    invalidate(handler)
                # Force close connection (if not closed yet)
                try:
                    # Safely checkWebSocketStatusAnd close
                    if hasattr(websocket, "closed") and not websocket.closed or hasattr(websocket, "state") and websocket.state.name != "CLOSED":
                        await websocket.close()
                    else:
                        # If noneclosedAttribute, try close directly
                        await websocket.close()
                except Exception as close_error:
                    self.logger.bind(tag=TAG).error(
                        f"Error when server forcibly closed connection: {close_error}"
                    )
        finally:
            if current_task is not None:
                self._connection_tasks.discard(current_task)

    async def _issue_liveness_lease(self, handler, device_id):
        """T2.5 — stamp the accepted connection with a monotonic liveness lease.

        Best-effort: a ledger outage must never refuse a device. Without a lease
        the connection behaves exactly as it did pre-rollout (see
        ``core/lesson/liveness_lease.classify_lease``: absent lease = accept).
        """
        from core.lesson.liveness_lease import get_lease_ledger

        try:
            handler.liveness_lease = await get_lease_ledger().issue(device_id)
        except Exception as exc:
            handler.liveness_lease = None
            self.logger.bind(tag=TAG).warning(
                "Liveness lease issue failed device={} errorType={}",
                device_id,
                type(exc).__name__,
            )

    def _scrap_superseded_connection(self, superseded, winner, device_id):
        """A newer socket took the device: retire the old one and say so.

        Firmware reopens its passive lesson socket on any drop it detects itself
        (pong timeout, Wi-Fi flap, ``SchedulePassiveLessonReconnect``), so the
        new socket routinely arrives while the old one is still registered and,
        server-side, still open. Left alive it keeps a second lesson runtime and
        a second event forwarder bound to the same assignment.

        Ordering matters. ``superseded_by`` (T2.5) and ``mark_superseded()``
        (T2.4) both land **synchronously**, before this function returns and
        therefore before the new connection can emit anything: the stale
        handler's lesson runtime is already refusing to write to its dead
        socket, and every other writer on that handler — TTS, ping, admin nudge
        — now hits a stand-in that refuses too. The actual close is slow (a
        half-open peer drags the closing handshake to its timeout, then voice
        provider teardown and forwarder drain follow) and is scheduled behind
        that guard rather than in front of it.
        """
        from core.lesson.liveness_lease import Disposition, emit_disposition
        from core.lesson.runtime_counters import CONNECTION_SUPERSEDED, increment

        # T6.2 (T2.4 finding): a reconnect storm was visible only as log lines.
        increment(CONNECTION_SUPERSEDED)

        # Capture the real socket BEFORE marking: ``mark_superseded`` swaps
        # ``superseded.websocket`` for a stand-in whose close() is a no-op, so
        # reading it afterwards would lose the socket that needs closing.
        websocket = getattr(superseded, "websocket", None)

        try:
            superseded.superseded_by = getattr(winner, "session_id", None) or True
        except Exception:  # pragma: no cover - exotic handler object
            pass

        mark = getattr(superseded, "mark_superseded", None)
        if callable(mark):
            try:
                mark()
            except Exception as exc:
                self.logger.bind(tag=TAG).warning(
                    "Superseded connection mark failed device={} errorType={}",
                    device_id,
                    type(exc).__name__,
                )

        winner_lease = getattr(winner, "liveness_lease", None)
        emit_disposition(
            self.logger,
            disposition=Disposition.SCRAP,
            reason="duplicate_connect_superseded",
            device_id=device_id,
            session_id=str(getattr(superseded, "session_id", "") or ""),
            session_epoch=getattr(winner_lease, "session_epoch", None),
            extra={"winnerSessionId": str(getattr(winner, "session_id", "") or "")},
        )

        try:
            task = asyncio.create_task(
                self._close_superseded(superseded, websocket, device_id)
            )
        except RuntimeError:  # pragma: no cover - no running loop (sync test call)
            return
        self._connection_tasks.add(task)
        task.add_done_callback(self._connection_tasks.discard)

    async def _close_superseded(self, superseded, websocket, device_id):
        # Close the socket first, with an explicit reason: the device gets a
        # decisive 1001 without waiting on this handler's teardown, and the
        # handler's own read loop starts unwinding.
        if websocket is not None:
            try:
                await websocket.close(code=1001, reason="superseded by newer connection")
            except Exception as exc:
                self.logger.bind(tag=TAG).warning(
                    "Superseded websocket close failed device={} errorType={}",
                    device_id,
                    type(exc).__name__,
                )
        close = getattr(superseded, "close", None)
        if not callable(close):
            return
        try:
            await close(websocket)
        except Exception as exc:
            self.logger.bind(tag=TAG).warning(
                "Superseded connection close failed device={} errorType={}",
                device_id,
                type(exc).__name__,
            )

    @staticmethod
    def _copy_query_identity_headers(websocket):
        """Accept identity query params, but never promote URL auth tokens."""
        from urllib.parse import parse_qs, urlparse

        request_path = getattr(websocket.request, "path", "")
        if not request_path:
            return
        query_params = parse_qs(urlparse(request_path).query)
        headers = websocket.request.headers
        for name in ("device-id", "client-id"):
            if not _header_values(headers, name) and name in query_params:
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
            if self.config_lock is None:
                self.config_lock = asyncio.Lock()
            async with self.config_lock:
                # Re-get config (use async version)
                new_config = await get_config_from_api_async(self.config)
                if new_config is None:
                    self.logger.bind(tag=TAG).error("Get new config failed")
                    return False
                self.logger.bind(tag=TAG).info("Get new config succeeded")
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
                self.logger.bind(tag=TAG).info("Update config task completed")
                return True
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Failed to update server config: {str(e)}")
            return False

    async def _handle_auth(self, websocket: websockets.ServerConnection):
        # Authenticate first, then connect
        if self.auth_enable:
            headers = websocket.request.headers
            try:
                device_id = _single_header(headers, "device-id")
                client_id = _single_header(headers, "client-id")
                authorization = _single_header(headers, "authorization", "")
            except ValueError as exc:
                raise AuthenticationError("Duplicate authentication header") from exc
            if self.allowed_devices and device_id in self.allowed_devices:
                # IfBelongs toDevices in whitelist, no validationtokendirectly allow
                return
            else:
                # Otherwise Verifytoken
                token = authorization
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

    def _resolve_accept_cap(self):
        server_config = self.config.get("server", {})
        admission = server_config.get("audio_admission", {})
        if not isinstance(admission, dict) or not admission.get("enabled", False):
            return None
        explicit_cap = admission.get("hard_accept_cap")
        if explicit_cap is not None:
            try:
                return max(1, int(explicit_cap))
            except (TypeError, ValueError):
                self.logger.bind(tag=TAG).warning(
                    "Invalid audio_admission.hard_accept_cap={!r}; deriving from benchmark",
                    explicit_cap,
                )
        try:
            cost = float(admission.get("measured_cost_per_stream_core_fraction"))
        except (TypeError, ValueError):
            cost = 0.0
        if cost <= 0:
            self.logger.bind(tag=TAG).warning(
                "Audio admission enabled without measured cost; WS accept cap disabled"
            )
            return None
        try:
            headroom = float(admission.get("headroom_fraction", 0.70))
        except (TypeError, ValueError):
            headroom = 0.70
        headroom = min(1.0, max(0.1, headroom))
        try:
            cores = int(admission.get("cpu_cores") or os.cpu_count() or 1)
        except (TypeError, ValueError):
            cores = os.cpu_count() or 1
        return max(1, int((max(1, cores) * headroom) / cost))

    def _is_over_accept_cap(self):
        return self.accept_cap is not None and self._active_device_connections >= self.accept_cap

    async def _reject_accept_cap(self, websocket):
        admission = self.config.get("server", {}).get("audio_admission", {})
        retry_after_ms = int(admission.get("retry_after_ms", 1000)) if isinstance(admission, dict) else 1000
        payload = {
            "type": "server_busy",
            "reason": "active_stream_cap",
            "active": self._active_device_connections,
            "cap": self.accept_cap,
            "retry_after_ms": retry_after_ms,
        }
        self.logger.bind(tag=TAG).warning(
            "Rejecting websocket over audio admission cap active={} cap={}",
            self._active_device_connections,
            self.accept_cap,
        )
        try:
            await websocket.send(json.dumps(payload))
        finally:
            await websocket.close(code=1013, reason="active stream cap reached")

    async def _reject_draining(self, websocket):
        payload = {
            "type": "server_draining",
            "reason": "server_draining",
            "retry_after_ms": 1000,
        }
        try:
            await websocket.send(json.dumps(payload))
        finally:
            await websocket.close(code=1012, reason="server draining")
