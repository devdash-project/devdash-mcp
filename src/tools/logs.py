"""DevDash log tools - Application log retrieval via HTTP API.

These tools retrieve logs from the DevDash application (devdash repository).
DevDash must be running with DevTools enabled for these tools to work.

Run DevDash with DevTools:
    cd devdash && ./build/dev/devdash --profile profiles/haltech-vcan.json
"""

from typing import Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP

from ..config import get_config


def register_logs_tools(mcp: FastMCP) -> None:
    """Register DevDash log tools with the MCP server."""

    @mcp.tool()
    async def devdash_logs_get(
        count: int = 100,
        level: Literal["debug", "info", "warning", "critical"] = "info",
        category: str = "",
    ) -> dict[str, Any]:
        """Retrieve recent application logs from DevDash (devdash repo).

        Fetches logs from the DevDash application's internal log buffer.
        Useful for debugging adapter issues, CAN parsing errors, or
        configuration problems.

        Requires DevDash to be running with DevTools enabled.

        Args:
            count: Number of log entries to retrieve (max 1000, default 100)
            level: Minimum log level to include (debug, info, warning, critical)
            category: Filter by category (e.g., 'devdash.broker', 'devdash.adapter')

        Returns:
            List of log entries with timestamp, level, category, and message
        """
        config = get_config()
        url = f"{config.devtools_base_url}/api/logs"

        params: dict[str, Any] = {
            "count": min(count, 1000),
            "level": level,
        }
        if category:
            params["category"] = category

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
        except httpx.ConnectError:
            return {
                "error": "Cannot connect to DevDash (devdash repo). "
                "Is it running with DevTools enabled? "
                "Run: cd devdash && ./build/dev/devdash --profile profiles/haltech-vcan.json",
            }
        except httpx.HTTPStatusError as e:
            return {
                "error": f"HTTP {e.response.status_code}: {e.response.text}",
            }
        except Exception as e:
            return {"error": str(e)}
