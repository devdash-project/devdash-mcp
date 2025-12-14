"""QML Gauges Explorer tools - WebSocket communication with the explorer app.

These tools interact with the QML Gauges Explorer from the qml-gauges repository.
The explorer must be running for these tools to work.

Launch the explorer:
    cd qml-gauges && ./build/explorer/qml-gauges-explorer
"""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

try:
    from websockets.sync.client import connect as ws_connect

    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

from ..config import get_config


def _send_request(request: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    """Send a request to the explorer WebSocket server."""
    if not WEBSOCKETS_AVAILABLE:
        return {
            "success": False,
            "error": "websockets library not installed. Run: pip install websockets",
        }

    config = get_config()
    url = config.explorer_ws_url

    try:
        with ws_connect(url, open_timeout=timeout, close_timeout=timeout) as ws:
            ws.send(json.dumps(request))
            response = ws.recv(timeout=timeout)
            return json.loads(response)
    except ConnectionRefusedError:
        return {
            "success": False,
            "error": f"Cannot connect to QML Gauges Explorer at {url}. "
            "Launch it with: cd qml-gauges && ./build/explorer/qml-gauges-explorer",
        }
    except TimeoutError:
        return {"success": False, "error": f"Timeout connecting to explorer at {url}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# Valid component pages in the explorer
EXPLORER_PAGES = [
    "Welcome",
    "GaugeArc",
    "GaugeBezel",
    "GaugeCenterCap",
    "GaugeFace",
    "GaugeTick",
    "GaugeTickLabel",
    "DigitalReadout",
    "GaugeNeedle",
    "GaugeTickRing",
    "GaugeValueArc",
    "GaugeZoneArc",
    "RollingDigitReadout",
    "RadialGauge",
]


def register_explorer_tools(mcp: FastMCP) -> None:
    """Register QML Gauges Explorer tools with the MCP server."""

    @mcp.tool()
    def qml_explorer_get_state() -> dict[str, Any]:
        """Get the current state of the QML Gauges Explorer (qml-gauges repo).

        Returns the current page, available properties, and their values.
        Requires the explorer to be running.

        Returns:
            Current explorer state including page and property values
        """
        return _send_request({"action": "getState"})

    @mcp.tool()
    def qml_explorer_navigate(page: str) -> dict[str, Any]:
        """Navigate the QML Gauges Explorer to a specific component page (qml-gauges repo).

        Args:
            page: Component page name. Valid pages: Welcome, GaugeArc, GaugeBezel,
                  GaugeCenterCap, GaugeFace, GaugeTick, GaugeTickLabel, DigitalReadout,
                  GaugeNeedle, GaugeTickRing, GaugeValueArc, GaugeZoneArc,
                  RollingDigitReadout, RadialGauge

        Returns:
            Navigation result with success status
        """
        if page not in EXPLORER_PAGES:
            return {
                "success": False,
                "error": f"Invalid page '{page}'. Valid pages: {', '.join(EXPLORER_PAGES)}",
            }
        return _send_request({"action": "navigate", "page": page})

    @mcp.tool()
    def qml_explorer_get_property(name: str) -> dict[str, Any]:
        """Get the current value of a specific property in the QML Gauges Explorer (qml-gauges repo).

        Args:
            name: Property name (e.g., 'tickShape', 'color', 'hasGlow')

        Returns:
            Property value and metadata
        """
        return _send_request({"action": "getProperty", "name": name})

    @mcp.tool()
    def qml_explorer_set_property(name: str, value: Any) -> dict[str, Any]:
        """Set a property value on the current component in the QML Gauges Explorer (qml-gauges repo).

        Args:
            name: Property name (e.g., 'tickShape', 'color', 'hasGlow')
            value: Value to set (type depends on property: string, number, boolean, color hex)

        Returns:
            Result with success status
        """
        return _send_request({"action": "setProperty", "name": name, "value": value})

    @mcp.tool()
    def qml_explorer_list_properties() -> dict[str, Any]:
        """List all available properties for the current component page in QML Gauges Explorer (qml-gauges repo).

        Returns full documentation for each property including name, type,
        range, default value, and description.

        Returns:
            List of property definitions with metadata
        """
        return _send_request({"action": "listProperties"})
