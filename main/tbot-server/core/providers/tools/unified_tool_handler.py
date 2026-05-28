"""Unified tool handler"""

import json
from typing import Dict, List, Any, Optional
from config.logger import setup_logging
from plugins_func.loadplugins import auto_import_modules

from .base import ToolType
from plugins_func.register import Action, ActionResponse
from .unified_tool_manager import ToolManager
from .server_plugins import ServerPluginExecutor
from .server_mcp import ServerMCPExecutor
from .device_iot import DeviceIoTExecutor
from .device_mcp import DeviceMCPExecutor
from .mcp_endpoint import MCPEndpointExecutor
from core.handle.sendAudioHandle import send_display_message
from core.handle.tbotToolHandler import (
    validate_mcp_tool_call,
    audit_log_tool_call,
    check_mcp_rate_limit,
)


class UnifiedToolHandler:
    """Unified tool handler"""

    def __init__(self, conn):
        self.conn = conn
        self.config = conn.config
        self.logger = setup_logging()

        # Create tool manager
        self.tool_manager = ToolManager(conn)

        # Create executors
        self.server_plugin_executor = ServerPluginExecutor(conn)
        self.server_mcp_executor = ServerMCPExecutor(conn)
        self.device_iot_executor = DeviceIoTExecutor(conn)
        self.device_mcp_executor = DeviceMCPExecutor(conn)
        self.mcp_endpoint_executor = MCPEndpointExecutor(conn)

        # RegisterExecutor
        self.tool_manager.register_executor(
            ToolType.SERVER_PLUGIN, self.server_plugin_executor
        )
        self.tool_manager.register_executor(
            ToolType.SERVER_MCP, self.server_mcp_executor
        )
        self.tool_manager.register_executor(
            ToolType.DEVICE_IOT, self.device_iot_executor
        )
        self.tool_manager.register_executor(
            ToolType.DEVICE_MCP, self.device_mcp_executor
        )
        self.tool_manager.register_executor(
            ToolType.MCP_ENDPOINT, self.mcp_endpoint_executor
        )

        # Initialization flag
        self.finish_init = False

    async def _initialize(self):
        """Async initialization"""
        try:
            # Auto import plugin modules
            auto_import_modules("plugins_func.functions")

            # Initialize serverMCP
            await self.server_mcp_executor.initialize()

            # Initialize MCP access point
            await self._initialize_mcp_endpoint()

            # InitializeHome Assistant(if needed)
            self._initialize_home_assistant()

            self.finish_init = True
            self.logger.debug("Unified tool handler initialized")

            # Output all currently supported tool list
            self.current_support_functions()

        except Exception as e:
            self.logger.error(f"Unified tool handler initialization failed: {e}")

    async def _initialize_mcp_endpoint(self):
        """Initialize MCP access point"""
        try:
            from .mcp_endpoint import connect_mcp_endpoint

            # Get from configMCPAccess pointURL
            mcp_endpoint_url = self.config.get("mcp_endpoint", "")

            if (
                mcp_endpoint_url
                and "Your" not in mcp_endpoint_url
                and mcp_endpoint_url != "null"
            ):
                self.logger.info(f"Initializing MCP access point: {mcp_endpoint_url}")
                mcp_endpoint_client = await connect_mcp_endpoint(
                    mcp_endpoint_url, self.conn
                )

                if mcp_endpoint_client:
                    # willMCPEndpoint clientSaveinto connection object
                    self.conn.mcp_endpoint_client = mcp_endpoint_client
                    self.logger.info("MCP endpoint initialized successfully")
                else:
                    self.logger.warning("MCP endpoint initialization failed")

        except Exception as e:
            self.logger.error(f"MCP access point init failed: {e}")

    def _initialize_home_assistant(self):
        """Initialize Home Assistant prompt"""
        try:
            from plugins_func.functions.hass_init import append_devices_to_prompt

            append_devices_to_prompt(self.conn)
        except ImportError:
            pass  # Ignore ImportError
        except Exception as e:
            self.logger.error(f"Failed to initialize Home Assistant: {e}")

    def get_functions(self) -> List[Dict[str, Any]]:
        """Get function descriptions for all tools"""
        return self.tool_manager.get_function_descriptions()

    def current_support_functions(self) -> List[str]:
        """Get current supported function name list"""
        func_names = self.tool_manager.get_supported_tool_names()
        self.logger.info(f"Currently supported function list: {func_names}")
        return func_names

    def upload_functions_desc(self):
        """Refresh function description list"""
        self.tool_manager.refresh_tools()
        self.logger.info("Function description list refreshed")

    def has_tool(self, tool_name: str) -> bool:
        """Check whether specified tool exists"""
        return self.tool_manager.has_tool(tool_name)

    async def handle_llm_function_call(
        self, conn, function_call_data: Dict[str, Any]
    ) -> Optional[ActionResponse]:
        """Handle LLM function call"""
        try:
            # Handle multi-function calls
            if "function_calls" in function_call_data:
                responses = []
                for call in function_call_data["function_calls"]:
                    result = await self.tool_manager.execute_tool(
                        call["name"], call.get("arguments", {})
                    )
                    responses.append(result)
                return self._combine_responses(responses)

            # Handle single-function call
            function_name = function_call_data["name"]
            arguments = function_call_data.get("arguments", {})

            # Ifargumentsis string, try parse asJSON
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments else {}
                except json.JSONDecodeError:
                    self.logger.error(f"Cannot parse function arguments: {arguments}")
                    return ActionResponse(
                        action=Action.ERROR,
                        response="Cannot parse function parameters",
                    )

            self.logger.debug(f"Call function: {function_name}, parameters: {arguments}")

            # Send tool call displayMessageTo device
            try:
                await send_display_message(self.conn, f"% {function_name}")
            except Exception as e:
                self.logger.warning(f"Failed to send tool-call display message: {e}")

            # ── MCP security gate ──
            tool_type = self.tool_manager.get_tool_type(function_name)
            if tool_type in (ToolType.SERVER_MCP, ToolType.DEVICE_MCP, ToolType.MCP_ENDPOINT):
                # 1. Rate limit check (per-connection)
                if hasattr(conn, "_mcp_tool_call_times"):
                    allowed, updated = check_mcp_rate_limit(conn._mcp_tool_call_times)
                    conn._mcp_tool_call_times = updated
                    if not allowed:
                        self.logger.warning(
                            f"MCP tool '{function_name}' rejected: rate limit exceeded"
                        )
                        return ActionResponse(
                            action=Action.ERROR,
                            response="Too many tool calls. Please slow down.",
                        )

                # 2. Allowlist / blocklist / argument validation
                allowed, reason = validate_mcp_tool_call(function_name, arguments)
                if not allowed:
                    self.logger.warning(
                        f"MCP tool '{function_name}' rejected: {reason}"
                    )
                    return ActionResponse(action=Action.ERROR, response=reason)

                # 3. Audit log
                device_id = getattr(conn, "device_id", None)
                audit_log_tool_call(function_name, arguments, device_id)

            # Execute tool call
            result = await self.tool_manager.execute_tool(function_name, arguments)
            return result

        except Exception as e:
            self.logger.error(f"Handle function call error: {e}")
            return ActionResponse(action=Action.ERROR, response=str(e))

    def _combine_responses(self, responses: List[ActionResponse]) -> ActionResponse:
        """Merge responses from multiple function calls"""
        if not responses:
            return ActionResponse(action=Action.NONE, response="No response")

        # If anyError,return firstError
        for response in responses:
            if response.action == Action.ERROR:
                return response

        # Merge all successfulResponse
        contents = []
        responses_text = []

        for response in responses:
            if response.content:
                contents.append(response.content)
            if response.response:
                responses_text.append(response.response)

        # Determine final action type
        final_action = Action.RESPONSE
        for response in responses:
            if response.action == Action.REQLLM:
                final_action = Action.REQLLM
                break

        return ActionResponse(
            action=final_action,
            result="; ".join(contents) if contents else None,
            response="; ".join(responses_text) if responses_text else None,
        )

    async def register_iot_tools(self, descriptors: List[Dict[str, Any]]):
        """Register IoT device tool"""
        self.device_iot_executor.register_iot_tools(descriptors)
        self.tool_manager.refresh_tools()
        self.logger.info(f"Registered tools for {len(descriptors)} IoT devices")

    def get_tool_statistics(self) -> Dict[str, int]:
        """Get tool statistics"""
        return self.tool_manager.get_tool_statistics()

    async def cleanup(self):
        """Clean resources"""
        try:
            await self.server_mcp_executor.cleanup()

            # CleanMCPAccess point connection
            if (
                hasattr(self.conn, "mcp_endpoint_client")
                and self.conn.mcp_endpoint_client
            ):
                await self.conn.mcp_endpoint_client.close()

            self.logger.info("Tool handler cleanup complete")
        except Exception as e:
            self.logger.error(f"Tool processor cleanup failed: {e}")
