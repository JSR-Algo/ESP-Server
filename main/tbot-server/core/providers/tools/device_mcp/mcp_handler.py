"""Device-side MCP client supported modules"""

import json
import asyncio
import re
from concurrent.futures import Future
from core.utils.util import get_vision_url, sanitize_tool_name
from core.utils.auth import AuthToken
from config.logger import setup_logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()


def _message_metadata(payload: object) -> str:
    if not isinstance(payload, dict):
        return f"payloadType={type(payload).__name__}"
    method = payload.get("method")
    message_id = payload.get("id")
    params = payload.get("params")
    tool_name = params.get("name") if isinstance(params, dict) else None
    parts = [f"id={message_id}", f"method={method or '-'}"]
    if isinstance(tool_name, str) and tool_name:
        parts.append(f"tool={tool_name}")
    if "result" in payload:
        parts.append(f"resultType={type(payload.get('result')).__name__}")
    if "error" in payload:
        parts.append("error=true")
    return " ".join(parts)


class MCPClient:
    """Device-side MCP client for managing MCP status and tools"""

    def __init__(self):
        self.tools = {}  # sanitized_name -> tool_data
        self.name_mapping = {}
        self.ready = False
        self.call_results = {}  # To store Futures for tool call responses
        self.next_id = 1
        self.lock = asyncio.Lock()
        self._cached_available_tools = None  # Cache for get_available_tools

    def has_tool(self, name: str) -> bool:
        return name in self.tools

    def get_available_tools(self) -> list:
        # Check if the cache is valid
        if self._cached_available_tools is not None:
            return self._cached_available_tools

        # If cache is not valid, regenerate the list
        result = []
        for tool_name, tool_data in self.tools.items():
            function_def = {
                "name": tool_name,
                "description": tool_data["description"],
                "parameters": {
                    "type": tool_data["inputSchema"].get("type", "object"),
                    "properties": tool_data["inputSchema"].get("properties", {}),
                    "required": tool_data["inputSchema"].get("required", []),
                },
            }
            result.append({"type": "function", "function": function_def})

        self._cached_available_tools = result  # Store the generated list in cache
        return result

    async def is_ready(self) -> bool:
        async with self.lock:
            return self.ready

    async def set_ready(self, status: bool):
        async with self.lock:
            self.ready = status

    async def add_tool(self, tool_data: dict):
        async with self.lock:
            sanitized_name = sanitize_tool_name(tool_data["name"])
            self.tools[sanitized_name] = tool_data
            self.name_mapping[sanitized_name] = tool_data["name"]
            self._cached_available_tools = (
                None  # Invalidate the cache when a tool is added
            )

    async def get_next_id(self) -> int:
        async with self.lock:
            current_id = self.next_id
            self.next_id += 1
            return current_id

    async def register_call_result_future(self, id: int, future: Future):
        async with self.lock:
            self.call_results[id] = future

    async def resolve_call_result(self, id: int, result: any):
        async with self.lock:
            if id in self.call_results:
                future = self.call_results.pop(id)
                if not future.done():
                    future.set_result(result)

    async def reject_call_result(self, id: int, exception: Exception):
        async with self.lock:
            if id in self.call_results:
                future = self.call_results.pop(id)
                if not future.done():
                    future.set_exception(exception)

    async def cleanup_call_result(self, id: int):
        async with self.lock:
            if id in self.call_results:
                self.call_results.pop(id)


async def send_mcp_message(conn: "ConnectionHandler", payload: dict):
    """Helper to send MCP messages, encapsulating common logic."""
    if not conn.features.get("mcp"):
        logger.bind(tag=TAG).warning("Client does not support MCP, cannot send MCP message")
        return

    message = json.dumps({"type": "mcp", "payload": payload})

    try:
        await conn.websocket.send(message)
        logger.bind(tag=TAG).debug(f"Successfully sent MCP message {_message_metadata(payload)}")
    except Exception as e:
        logger.bind(tag=TAG).error(
            f"Send MCP message failed {_message_metadata(payload)} errorType={type(e).__name__}"
        )


async def handle_mcp_message(
    conn: "ConnectionHandler", mcp_client: MCPClient, payload: dict
):
    """Handle MCP messages, including initialization, tool list, tool call responses, etc."""
    logger.bind(tag=TAG).debug(f"Handle MCP message {_message_metadata(payload)}")

    if not isinstance(payload, dict):
        logger.bind(tag=TAG).error("MCP message missing payload field or invalid format")
        return

    # Handle result
    if "result" in payload:
        result = payload["result"]
        msg_id = int(payload.get("id", 0))

        # Check for tool call response first
        if msg_id in mcp_client.call_results:
            logger.bind(tag=TAG).debug(
                f"Received tool call response id={msg_id} resultType={type(result).__name__}"
            )
            await mcp_client.resolve_call_result(msg_id, result)
            return

        if msg_id == 1:  # mcpInitializeID
            logger.bind(tag=TAG).debug("Received MCP initialization response")
            server_info = result.get("serverInfo")
            if isinstance(server_info, dict):
                name = server_info.get("name")
                version = server_info.get("version")
                logger.bind(tag=TAG).debug(
                    f"Client MCP server info: name={name}, version={version}"
                )

            await asyncio.sleep(1)
            logger.bind(tag=TAG).debug("Initialization complete, start requesting MCP tool list")
            await send_mcp_tools_list_request(conn)

            return

        elif msg_id == 2:  # mcpToolsListID
            logger.bind(tag=TAG).debug("Received MCP tool list response")
            if isinstance(result, dict) and "tools" in result:
                tools_data = result["tools"]
                if not isinstance(tools_data, list):
                    logger.bind(tag=TAG).error("Tool list format error")
                    return

                logger.bind(tag=TAG).info(
                    f"Number of tools supported by client device: {len(tools_data)}"
                )

                for i, tool in enumerate(tools_data):
                    if not isinstance(tool, dict):
                        continue

                    name = tool.get("name", "")
                    description = tool.get("description", "")
                    input_schema = {"type": "object", "properties": {}, "required": []}

                    if "inputSchema" in tool and isinstance(tool["inputSchema"], dict):
                        schema = tool["inputSchema"]
                        input_schema["type"] = schema.get("type", "object")
                        input_schema["properties"] = schema.get("properties", {})
                        input_schema["required"] = [
                            s for s in schema.get("required", []) if isinstance(s, str)
                        ]

                    new_tool = {
                        "name": name,
                        "description": description,
                        "inputSchema": input_schema,
                    }
                    await mcp_client.add_tool(new_tool)
                    logger.bind(tag=TAG).debug(f"Client tool #{i+1}: {name}")

                # Replace all toolsDescriptionTools inName
                for tool_data in mcp_client.tools.values():
                    if "description" in tool_data:
                        description = tool_data["description"]
                        # Iterate all toolsNameReplace
                        for (
                            sanitized_name,
                            original_name,
                        ) in mcp_client.name_mapping.items():
                            description = description.replace(
                                original_name, sanitized_name
                            )
                        tool_data["description"] = description

                next_cursor = result.get("nextCursor", "")
                if next_cursor:
                    logger.bind(tag=TAG).debug("More MCP tools available")
                    await send_mcp_tools_list_continue_request(conn, next_cursor)
                else:
                    await mcp_client.set_ready(True)
                    logger.bind(tag=TAG).debug("All tools fetched, MCP client ready")

                    # Refresh tool cache, ensureMCPTool included in function list
                    if hasattr(conn, "func_handler") and conn.func_handler:
                        conn.func_handler.tool_manager.refresh_tools()
                        conn.func_handler.current_support_functions()
                    schedule_sd_sync = getattr(conn, "schedule_cached_lesson_sd_sync", None)
                    if callable(schedule_sd_sync):
                        schedule_sd_sync()
            return

    # Handle method calls (requests from the client)
    elif "method" in payload:
        method = payload["method"]
        logger.bind(tag=TAG).info(f"Received MCP client request: {method}")

    elif "error" in payload:
        error_data = payload["error"]
        error_msg = (
            error_data.get("message", "Unknown error")
            if isinstance(error_data, dict)
            else "Unknown error"
        )
        logger.bind(tag=TAG).error(f"Received MCP error response id={payload.get('id', 0)}")

        msg_id = int(payload.get("id", 0))
        if msg_id in mcp_client.call_results:
            await mcp_client.reject_call_result(
                msg_id, Exception(f"MCP error: {error_msg}")
            )


async def send_mcp_initialize_message(conn: "ConnectionHandler"):
    """Send MCP initialization message"""

    vision_url = get_vision_url(conn.config)

    # KeyGeneratetoken
    auth = AuthToken(conn.config["server"]["auth_key"])
    token = auth.generate_token(conn.headers.get("device-id"))

    vision = {
        "url": vision_url,
        "token": token,
    }

    payload = {
        "jsonrpc": "2.0",
        "id": 1,  # mcpInitializeID
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "roots": {"listChanged": True},
                "sampling": {},
                "vision": vision,
            },
            "clientInfo": {
                "name": "TbotClient",
                "version": "1.0.0",
            },
        },
    }
    logger.bind(tag=TAG).debug("Send MCP initialization message")
    await send_mcp_message(conn, payload)


async def send_mcp_tools_list_request(conn: "ConnectionHandler"):
    """Send MCP tool list request"""
    payload = {
        "jsonrpc": "2.0",
        "id": 2,  # mcpToolsListID
        "method": "tools/list",
    }
    logger.bind(tag=TAG).debug("Send MCP tool list request")
    await send_mcp_message(conn, payload)


async def send_mcp_tools_list_continue_request(conn: "ConnectionHandler", cursor: str):
    """Send MCP tool list request with cursor"""
    payload = {
        "jsonrpc": "2.0",
        "id": 2,  # mcpToolsListID (same ID for continuation)
        "method": "tools/list",
        "params": {"cursor": cursor},
    }
    logger.bind(tag=TAG).info("Send MCP tool list continuation request")
    await send_mcp_message(conn, payload)


async def call_mcp_tool(
    conn: "ConnectionHandler",
    mcp_client: MCPClient,
    tool_name: str,
    args: str = "{}",
    timeout: int = 30,
):
    """
    Call specified tool and wait for response
    """
    if not await mcp_client.is_ready():
        raise RuntimeError("MCP client not ready yet")

    if not mcp_client.has_tool(tool_name):
        raise ValueError(f"Tool {tool_name} does not exist")

    tool_call_id = await mcp_client.get_next_id()
    result_future = asyncio.Future()
    await mcp_client.register_call_result_future(tool_call_id, result_future)

    # Handle Parameters
    try:
        if isinstance(args, str):
            # Ensure string is validJSON
            if not args.strip():
                arguments = {}
            else:
                try:
                    # Try direct parse
                    arguments = json.loads(args)
                except json.JSONDecodeError:
                    # If parse fails, try merge multipleJSONObject
                    try:
                        # Use regex to match allJSONObject
                        json_objects = re.findall(r"\{[^{}]*\}", args)
                        if len(json_objects) > 1:
                            # Merge AllJSONObject
                            merged_dict = {}
                            for json_str in json_objects:
                                try:
                                    obj = json.loads(json_str)
                                    if isinstance(obj, dict):
                                        merged_dict.update(obj)
                                except json.JSONDecodeError:
                                    continue
                            if merged_dict:
                                arguments = merged_dict
                            else:
                                raise ValueError("Cannot parse any valid JSON object")
                        else:
                            raise ValueError("Parameter JSON parse failed")
                    except Exception as e:
                        logger.bind(tag=TAG).error(
                            f"Parameter JSON parse failed errorType={type(e).__name__}"
                        )
                        raise ValueError(f"Parameter JSON parse failed: {str(e)}")
        elif isinstance(args, dict):
            arguments = args
        else:
            raise ValueError(f"Parameter type error, expected string or dictionary, actual type: {type(args)}")

        # Ensure parameter isDictionary type
        if not isinstance(arguments, dict):
            raise ValueError(f"Parameters must be dictionary type, actual type: {type(arguments)}")

    except Exception as e:
        if not isinstance(e, ValueError):
            raise ValueError(f"Parameter processing failed: {str(e)}")
        raise e

    actual_name = mcp_client.name_mapping.get(tool_name, tool_name)
    payload = {
        "jsonrpc": "2.0",
        "id": tool_call_id,
        "method": "tools/call",
        "params": {"name": actual_name, "arguments": arguments},
    }

    logger.bind(tag=TAG).info(
        f"Send client MCP tool call request tool={actual_name} argumentCount={len(arguments)}"
    )
    try:
        await send_mcp_message(conn, payload)
    except Exception:
        await mcp_client.cleanup_call_result(tool_call_id)
        raise

    try:
        # Wait for response or timeout
        raw_result = await asyncio.wait_for(result_future, timeout=timeout)
        logger.bind(tag=TAG).info(
            f"Client MCP tool call succeeded tool={actual_name} resultType={type(raw_result).__name__}"
        )

        if isinstance(raw_result, dict):
            if raw_result.get("isError") is True:
                error_msg = raw_result.get(
                    "error", "Tool call returned error, but no specific error info provided"
                )
                raise RuntimeError(f"Tool call error: {error_msg}")

            content = raw_result.get("content")
            if isinstance(content, list) and len(content) > 0:
                if isinstance(content[0], dict) and "text" in content[0]:
                    # Return text directlyContent, do notJSONParse
                    return content[0]["text"]
        # If result not expected format, convert it to string
        return str(raw_result)
    except asyncio.TimeoutError:
        await mcp_client.cleanup_call_result(tool_call_id)
        raise TimeoutError("Tool call request timed out")
    except Exception as e:
        await mcp_client.cleanup_call_result(tool_call_id)
        raise e
