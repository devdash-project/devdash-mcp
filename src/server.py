"""DevDash MCP Server - Main entry point.

This MCP server provides tools for DevDash development and debugging:
- Explorer: Interact with the QML Gauges Explorer (navigate, get/set properties)
- Screenshot: Capture window screenshots via X11
- Telemetry: Access DevDash runtime state and warnings
- Logs: Retrieve application logs
"""

import logging
import sys

from mcp.server.fastmcp import FastMCP

from .config import get_config
from .tools import (
    register_explorer_tools,
    register_screenshot_tools,
    register_telemetry_tools,
    register_logs_tools,
)

# Configure logging to stderr (required for MCP STDIO transport)
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Create the MCP server
mcp = FastMCP("devdash")


def register_all_tools() -> None:
    """Register all tool modules with the MCP server."""
    register_explorer_tools(mcp)
    register_screenshot_tools(mcp)
    register_telemetry_tools(mcp)
    register_logs_tools(mcp)

    logger.info("All DevDash tools registered")


@mcp.resource("devdash://info")
def get_server_info() -> str:
    """Get information about the DevDash MCP server configuration."""
    config = get_config()
    return f"""DevDash MCP Server

Configuration:
  Explorer WebSocket: {config.explorer_ws_url}
  DevTools HTTP API: {config.devtools_base_url}

Available tool categories:

qml-gauges repo (QML Gauges Explorer via WebSocket):
  - qml_explorer_get_state: Get current page and property values
  - qml_explorer_navigate: Navigate to a component page
  - qml_explorer_get_property: Get a property value
  - qml_explorer_set_property: Set a property value
  - qml_explorer_list_properties: List available properties with metadata

devdash repo (DevDash runtime via HTTP API):
  - devdash_telemetry_get_state: Get current vehicle telemetry
  - devdash_telemetry_get_warnings: Get active warnings
  - devdash_telemetry_list_windows: List DevDash windows
  - devdash_telemetry_screenshot: Capture via DevTools API
  - devdash_logs_get: Retrieve logs with filtering

System (X11 window capture - works with any window):
  - screenshot_list_windows: List available windows
  - screenshot_capture: Capture window as PNG

Environment Variables:
  DEVDASH_EXPLORER_WS_PORT: Explorer WebSocket port (default: 9876)
  DEVDASH_EXPLORER_WS_HOST: Explorer WebSocket host (default: localhost)
  DEVDASH_DEVTOOLS_PORT: DevTools HTTP port (default: 18080)
  DEVDASH_DEVTOOLS_HOST: DevTools HTTP host (default: 127.0.0.1)
"""


def main() -> None:
    """Main entry point for the MCP server."""
    config = get_config()
    logger.info(f"DevDash MCP server starting...")
    logger.info(f"  Explorer WebSocket: {config.explorer_ws_url}")
    logger.info(f"  DevTools HTTP API: {config.devtools_base_url}")

    register_all_tools()

    logger.info("Starting DevDash MCP server...")
    mcp.run()


if __name__ == "__main__":
    main()
