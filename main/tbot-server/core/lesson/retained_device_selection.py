"""Exact device selection transport and observed watermarks for ordinary SD work."""

import json

from core.lesson.retained_pack_contract import (
    DEVICE_CONTRACT, HASH, RECEIPT_FIELDS, _unique_object, revision, uuid_value,
)


async def observe_ordinary_selection(conn, mcp_client):
    if (getattr(conn, 'features', {}) or {}).get('retainedSelection') != DEVICE_CONTRACT:
        return 0
    from core.api.device_mcp_admin_handler import _call_raw_mcp_tool

    raw = await _call_raw_mcp_tool(conn, mcp_client, 'self.lesson_assets.selection_state', {}, timeout=30)
    state = json.loads(raw, object_pairs_hook=_unique_object) if isinstance(raw, str) else raw
    if not isinstance(state, dict) or state.get('contractVersion') != DEVICE_CONTRACT:
        raise ValueError('retained device state unavailable')
    if state.get('state') == 'unowned':
        if (set(state) != {'contractVersion', 'state', 'desiredSelectionRevision'}
                or type(state['desiredSelectionRevision']) is not int or state['desiredSelectionRevision'] != 0):
            raise ValueError('invalid unowned device state')
        return 0
    if state.get('state') != 'released' or set(state) != RECEIPT_FIELDS:
        raise ValueError('retained device has an active or unknown owner')
    for key in ['operationId', 'requestId', 'deviceId', 'consumerIdentity']:
        uuid_value(state[key])
    revision(state['requestRevision'])
    if not isinstance(state['packDescriptorChecksum'], str) or HASH.fullmatch(state['packDescriptorChecksum']) is None:
        raise ValueError('invalid retained descriptor checksum')
    from core.lesson.cache_key_contract import validate_cache_key
    validate_cache_key(state['cacheKey'])
    return revision(state['desiredSelectionRevision'])


async def call_retained_on_connection(conn, operation, tool_name, arguments, *, timeout=30):
    from core.api.device_mcp_admin_handler import DeviceMCPAdminHandler, _call_raw_mcp_tool

    registry = getattr(getattr(conn, 'server', None), 'lesson_connections', None)
    bridge = DeviceMCPAdminHandler(getattr(conn, 'config', {}) or {}, registry)
    key = bridge._connection_registry_key(operation['deviceId'], conn)
    reserve = getattr(registry, 'reserve_current', None)
    session = str(getattr(conn, 'session_id', '') or '')
    if key is None or not session or not callable(reserve):
        raise ValueError('current retained device connection required')
    return await _call_raw_mcp_tool(conn, conn.mcp_client, tool_name, arguments, timeout=timeout,
                                   send_reservation=reserve(key, conn, session))


async def bind_retained_on_connection(conn, operation):
    from core.lesson.retained_pack_contract import parse_device_receipt
    from core.lesson.retained_pack_materializer import materialize_retained_operation

    async def dispatch(op):
        raw = await call_retained_on_connection(conn, op, 'self.lesson_assets.retained_selection', {'operation': op})
        return parse_device_receipt(raw, op)

    return await materialize_retained_operation(operation, config=conn.config, bind_device=dispatch)
