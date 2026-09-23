"""Authenticated retained preparation, separate from the unchanged pack endpoint."""

from aiohttp import web

from core.api.lesson_nudge_handler import LessonNudgeHandler
from core.lesson.retained_pack_contract import CONTRACT, parse_operation
from core.lesson.retained_pack_materializer import materialize_retained_operation
from core.lesson.retained_pack_store import RetainedPackStore
from core.lesson.sd_pack_materializer import MaterializationError, _shared_store


def refusal(code, status, retryable=False):
    return web.json_response({'code': code, 'message': code, 'retryable': retryable}, status=status)


class RetainedPackHandler:
    def __init__(self, config, connections=None):
        self.config = config
        self.connections = connections
        self.auth = LessonNudgeHandler(config, connections if connections is not None else {})

    async def _select_device(self, operation):
        from core.api.device_mcp_admin_handler import DeviceMCPAdminHandler, _call_raw_mcp_tool
        from core.lesson.retained_pack_contract import parse_device_receipt

        bridge = DeviceMCPAdminHandler(self.config, self.connections)
        conn = await bridge._find_connection(operation['deviceId'])
        reserve = getattr(self.connections, 'reserve_current', None)
        if conn is None or not callable(reserve) or getattr(conn, 'mcp_client', None) is None:
            raise MaterializationError('RETAINED_DEVICE_CAPABILITY_UNAVAILABLE', 409, True,
                                       'Current device selection connection is unavailable')
        key = bridge._connection_registry_key(operation['deviceId'], conn)
        session = str(getattr(conn, 'session_id', '') or '')
        if key is None or not session:
            raise ValueError('current device connection is required')
        result = await _call_raw_mcp_tool(conn, conn.mcp_client,
            'self.lesson_assets.retained_selection', {'operation': operation}, timeout=30,
            send_reservation=reserve(key, conn, session))
        return parse_device_receipt(result, operation)

    async def handle_get(self, request):
        denied = self.auth._authorize(request)
        if denied is not None:
            return denied
        try:
            retained = RetainedPackStore(_shared_store(self.config))
        except (OSError, ValueError):
            return refusal('RETAINED_STORAGE_UNAVAILABLE', 503, True)
        return web.json_response({'data': {'contractVersion': CONTRACT,
            'consumerIdentity': retained.consumer_identity,
            'preparationSupported': True,
            # Server handoff capability; each bind still requires an exact device receipt.
            'deviceSelectionFencing': callable(getattr(self.connections, 'reserve_current', None))}})

    async def handle_post(self, request):
        denied = self.auth._authorize(request)
        if denied is not None:
            return denied
        try:
            body = await request.json()
        except web.HTTPRequestEntityTooLarge:
            return refusal('REQUEST_ENTITY_TOO_LARGE', 413)
        except (ValueError, TypeError):
            return refusal('INVALID_RETAINED_PACK_CONTRACT', 400)
        try:
            operation = parse_operation(body)
        except ValueError:
            return refusal('INVALID_RETAINED_PACK_CONTRACT', 400)
        try:
            receipt = await materialize_retained_operation(operation, config=self.config,
                bind_device=self._select_device if self.connections is not None else None,
                release_device=self._select_device if self.connections is not None else None)
        except MaterializationError as error:
            return web.json_response(error.to_response(), status=error.status)
        except ValueError as error:
            if str(error) == 'retained operation busy':
                return refusal('RETAINED_OPERATION_BUSY', 409, True)
            return refusal('RETAINED_STATE_UNAVAILABLE', 409, True)
        except OSError:
            return refusal('RETAINED_STORAGE_UNAVAILABLE', 503, True)
        except RuntimeError:
            return refusal('RETAINED_DEVICE_OUTCOME_UNKNOWN', 409, True)
        return web.json_response({'data': receipt})
