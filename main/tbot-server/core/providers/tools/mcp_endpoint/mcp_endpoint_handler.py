"""MCP access point handler"""

import json
import asyncio
import re
import websockets
from config.logger import setup_logging
from .mcp_endpoint_client import MCPEndpointClient

TAG = __name__
logger = setup_logging()


async def connect_mcp_endpoint(mcp_endpoint_url: str, conn=None) -> MCPEndpointClient:
    """Connect to MCP access point"""
    if not mcp_endpoint_url or "Your" in mcp_endpoint_url or mcp_endpoint_url == "null":
        return None

    try:
        websocket = await websockets.connect(mcp_endpoint_url)

        mcp_client = MCPEndpointClient(conn)
        mcp_client.set_websocket(websocket)

        # StartMessageListener
        asyncio.create_task(_message_listener(mcp_client))

        # Send initializationMessage
        await send_mcp_endpoint_initialize(mcp_client)

        # Send initialization complete notification
        await send_mcp_endpoint_notification(mcp_client, "notifications/initialized")

        # Get tool list
        await send_mcp_endpoint_tools_list(mcp_client)

        logger.bind(tag=TAG).info("MCP endpoint connected successfully")
        return mcp_client

    except Exception as e:
        logger.bind(tag=TAG).error(f"Connect to MCP access point failed: {e}")
        return None


async def _message_listener(mcp_client: MCPEndpointClient):
    """Listen for MCP endpoint messages"""
    try:
        async for message in mcp_client.websocket:
            await handle_mcp_endpoint_message(mcp_client, message)
    except websockets.exceptions.ConnectionClosed:
        logger.bind(tag=TAG).info("MCP endpoint connection closed")
    except Exception as e:
        logger.bind(tag=TAG).error(f"MCP access point message listener error: {e}")
    finally:
        await mcp_client.set_ready(False)


async def handle_mcp_endpoint_message(mcp_client: MCPEndpointClient, message: str):
    """Handle MCP endpoint messages"""
    try:
        payload = json.loads(message)
        logger.bind(tag=TAG).debug(f"Received MCP endpoint message: {payload}")

        if not isinstance(payload, dict):
            logger.bind(tag=TAG).error("Invalid MCP endpoint message format")
            return

        # Handle result
        if "result" in payload:
            result = payload["result"]
            # Safely getMessageID, if isNoneThen use0
            msg_id_raw = payload.get("id")
            msg_id = int(msg_id_raw) if msg_id_raw is not None else 0

            # Check for tool call response first
            if msg_id in mcp_client.call_results:
                logger.bind(tag=TAG).debug(
                    f"Received tool call response, ID: {msg_id}, result: {result}"
                )
                await mcp_client.resolve_call_result(msg_id, result)
                return

            if msg_id == 1:  # mcpInitializeID
                logger.bind(tag=TAG).debug("Received MCP access point init response")
                if result is not None and isinstance(result, dict):
                    server_info = result.get("serverInfo")
                    if isinstance(server_info, dict):
                        name = server_info.get("name")
                        version = server_info.get("version")
                        logger.bind(tag=TAG).info(
                            f"MCP access point server info: name={name}, version={version}"
                        )
                else:
                    logger.bind(tag=TAG).warning(
                        "MCP access point initialization response empty or invalid format"
                    )
                return

            elif msg_id == 2:  # mcpToolsListID
                logger.bind(tag=TAG).debug("Received MCP access point tool list response")
                if (
                    result is not None
                    and isinstance(result, dict)
                    and "tools" in result
                ):
                    tools_data = result["tools"]
                    if not isinstance(tools_data, list):
                        logger.bind(tag=TAG).error("Tool list format error")
                        return

                    logger.bind(tag=TAG).info(
                        f"Number of tools supported by MCP access point: {len(tools_data)}"
                    )

                    for i, tool in enumerate(tools_data):
                        if not isinstance(tool, dict):
                            continue

                        name = tool.get("name", "")
                        description = tool.get("description", "")
                        input_schema = {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        }

                        if "inputSchema" in tool and isinstance(
                            tool["inputSchema"], dict
                        ):
                            schema = tool["inputSchema"]
                            input_schema["type"] = schema.get("type", "object")
                            input_schema["properties"] = schema.get("properties", {})
                            input_schema["required"] = [
                                s
                                for s in schema.get("required", [])
                                if isinstance(s, str)
                            ]

                        new_tool = {
                            "name": name,
                            "description": description,
                            "inputSchema": input_schema,
                        }
                        await mcp_client.add_tool(new_tool)
                        logger.bind(tag=TAG).debug(f"MCP endpoint tool #{i+1}: {name}")

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

                    next_cursor = (
                        result.get("nextCursor", "") if result is not None else ""
                    )
                    if next_cursor:
                        logger.bind(tag=TAG).info(
                            f"More tools available, nextCursor: {next_cursor}"
                        )
                        await send_mcp_endpoint_tools_list_continue(
                            mcp_client, next_cursor
                        )
                    else:
                        await mcp_client.set_ready(True)
                        logger.bind(tag=TAG).info(
                            "All MCP endpoint tools fetched, client ready"
                        )

                        # Refresh tool cache, ensureMCPEntry point tool included in function list
                        if (
                            hasattr(mcp_client, "conn")
                            and mcp_client.conn
                            and hasattr(mcp_client.conn, "func_handler")
                            and mcp_client.conn.func_handler
                        ):
                            mcp_client.conn.func_handler.tool_manager.refresh_tools()
                            mcp_client.conn.func_handler.current_support_functions()

                        logger.bind(tag=TAG).info(
                            f"MCP endpoint tools fetched, total {len(mcp_client.tools)} tools"
                        )
                else:
                    logger.bind(tag=TAG).warning(
                        "MCP access point tool list response empty or invalid format"
                    )
                return

        # Handle method calls (requests from the endpoint)
        elif "method" in payload:
            method = payload["method"]
            logger.bind(tag=TAG).info(f"Received MCP access point request: {method}")

        elif "error" in payload:
            error_data = payload["error"]
            error_msg = error_data.get("message", "Unknown error")
            logger.bind(tag=TAG).error(f"Received MCP endpoint error response: {error_msg}")

            # Safely getMessageID, if isNoneThen use0
            msg_id_raw = payload.get("id")
            msg_id = int(msg_id_raw) if msg_id_raw is not None else 0

            if msg_id in mcp_client.call_results:
                await mcp_client.reject_call_result(
                    msg_id, Exception(f"MCP access point error: {error_msg}")
                )

    except json.JSONDecodeError as e:
        logger.bind(tag=TAG).error(f"MCP access point message JSON parse failed: {e}")
    except Exception as e:
        logger.bind(tag=TAG).error(f"Error handling MCP access point message: {e}")
        import traceback

        logger.bind(tag=TAG).error(f"Error details: {traceback.format_exc()}")


async def send_mcp_endpoint_initialize(mcp_client: MCPEndpointClient):
    """Send MCP access point init message"""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,  # mcpInitializeID
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "roots": {"listChanged": True},
                "sampling": {},
            },
            "clientInfo": {
                "name": "TbotMCPEndpointClient",
                "version": "1.0.0",
            },
        },
    }
    message = json.dumps(payload)
    logger.bind(tag=TAG).info("Send MCP access point init message")
    await mcp_client.send_message(message)


async def send_mcp_endpoint_notification(mcp_client: MCPEndpointClient, method: str):
    """Send MCP endpoint notification message"""
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": {},
    }
    message = json.dumps(payload)
    logger.bind(tag=TAG).debug(f"Send MCP access point notification: {method}")
    await mcp_client.send_message(message)


async def send_mcp_endpoint_tools_list(mcp_client: MCPEndpointClient):
    """Send MCP access point tool list request"""
    payload = {
        "jsonrpc": "2.0",
        "id": 2,  # mcpToolsListID
        "method": "tools/list",
    }
    message = json.dumps(payload)
    logger.bind(tag=TAG).debug("Send MCP access point tool list request")
    await mcp_client.send_message(message)


async def send_mcp_endpoint_tools_list_continue(
    mcp_client: MCPEndpointClient, cursor: str
):
    """Send MCP endpoint tool list request with cursor"""
    payload = {
        "jsonrpc": "2.0",
        "id": 2,  # mcpToolsListID (same ID for continuation)
        "method": "tools/list",
        "params": {"cursor": cursor},
    }
    message = json.dumps(payload)
    logger.bind(tag=TAG).info(f"Send MCP access point tool list request with cursor: {cursor}")
    await mcp_client.send_message(message)


async def call_mcp_endpoint_tool(
    mcp_client: MCPEndpointClient, tool_name: str, args: str = "{}", timeout: int = 30
):
    """
    Call specified MCP access point tool and wait for response
    """
    if not await mcp_client.is_ready():
        raise RuntimeError("MCP access point client not ready")

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
                                raise ValueError(f"Cannot parse any valid JSON object: {args}")
                        else:
                            raise ValueError(f"Parameter JSON parse failed: {args}")
                    except Exception as e:
                        logger.bind(tag=TAG).error(
                            f"Parameter JSON parse failed: {str(e)}, raw parameters: {args}"
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

    message = json.dumps(payload)
    logger.bind(tag=TAG).info(
        f"Send MCP access point tool call request: {actual_name}, args: {json.dumps(arguments, ensure_ascii=False)}"
    )
    await mcp_client.send_message(message)

    try:
        # Wait for response or timeout
        raw_result = await asyncio.wait_for(result_future, timeout=timeout)
        logger.bind(tag=TAG).info(
            f"MCP access point tool call {actual_name} succeeded, raw result: {raw_result}"
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
