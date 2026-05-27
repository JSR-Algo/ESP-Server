"""Tool system type definitions"""

from enum import Enum

from dataclasses import dataclass
from typing import Any, Dict, Optional
from plugins_func.register import Action


class ToolType(Enum):
    """Tool type enum"""

    SERVER_PLUGIN = "server_plugin"  # Server plugin
    SERVER_MCP = "server_mcp"  # Server sideMCP
    DEVICE_IOT = "device_iot"  # Device sideIoT
    DEVICE_MCP = "device_mcp"  # Device sideMCP
    MCP_ENDPOINT = "mcp_endpoint"  # MCPAccess point


@dataclass
class ToolDefinition:
    """Tool definition"""

    name: str  # ToolName
    description: Dict[str, Any]  # ToolDescription(OpenAIFunction call format)
    tool_type: ToolType  # Tool Type
    parameters: Optional[Dict[str, Any]] = None  # Extra Parameters
