"""DevDash telemetry tools - Runtime state via HTTP API.

These tools interact with the DevDash application from the devdash repository.
DevDash must be running with DevTools enabled for these tools to work.

Run DevDash with DevTools:
    cd devdash && ./build/dev/devdash --profile profiles/haltech-vcan.json
"""

import base64
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from ..config import get_config


async def _get(endpoint: str) -> dict[str, Any]:
    """Make a GET request to the DevTools API."""
    config = get_config()
    url = f"{config.devtools_base_url}{endpoint}"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
    except httpx.ConnectError:
        return {
            "error": "Cannot connect to DevDash (devdash repo). "
            "Is it running with DevTools enabled? "
            "Run: cd devdash && ./build/dev/devdash --profile profiles/haltech-vcan.json",
            "url": url,
        }
    except httpx.HTTPStatusError as e:
        return {
            "error": f"HTTP {e.response.status_code}: {e.response.text}",
        }
    except Exception as e:
        return {"error": str(e)}


def register_telemetry_tools(mcp: FastMCP) -> None:
    """Register DevDash telemetry tools with the MCP server."""

    @mcp.tool()
    async def devdash_telemetry_get_state() -> dict[str, Any]:
        """Get current telemetry state from DevDash (devdash repo).

        Returns real-time vehicle data including RPM, speed, temperatures,
        pressures, and other sensor values from the DataBroker.

        Requires DevDash to be running with DevTools enabled.

        Returns:
            Current telemetry values from the DataBroker
        """
        return await _get("/api/state")

    @mcp.tool()
    async def devdash_telemetry_get_warnings() -> dict[str, Any]:
        """Get active warnings and critical alerts from DevDash (devdash repo).

        Returns any active warning conditions such as high temperature,
        low oil pressure, or other alert states.

        Requires DevDash to be running with DevTools enabled.

        Returns:
            List of active warnings with severity and details
        """
        return await _get("/api/warnings")

    @mcp.tool()
    async def devdash_telemetry_list_windows() -> dict[str, Any]:
        """List DevDash windows available for screenshot (devdash repo).

        Uses the DevTools HTTP API to list windows, which may differ
        from X11 window detection.

        Returns:
            List of DevDash windows with names and properties
        """
        return await _get("/api/windows")

    @mcp.tool()
    async def devdash_telemetry_screenshot(window: str) -> dict[str, Any]:
        """Capture screenshot via DevDash DevTools API (devdash repo).

        Captures screenshots through the DevDash application itself,
        which may provide higher quality than X11-based capture.

        Args:
            window: Window name (e.g., 'cluster', 'headunit')

        Returns:
            Base64-encoded PNG image or error
        """
        config = get_config()
        url = f"{config.devtools_base_url}/api/screenshot"

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url, params={"window": window})
                response.raise_for_status()

                return {
                    "image": base64.b64encode(response.content).decode("utf-8"),
                    "mime_type": "image/png",
                    "source": "devtools",
                }
        except httpx.ConnectError:
            return {
                "error": "Cannot connect to DevDash DevTools API (devdash repo)",
            }
        except httpx.HTTPStatusError as e:
            return {
                "error": f"HTTP {e.response.status_code}: {e.response.text}",
            }
        except Exception as e:
            return {"error": str(e)}
