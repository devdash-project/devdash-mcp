"""DevDash telemetry tools - Runtime state via HTTP API.

These tools interact with the DevDash application from the devdash repository.
DevDash must be running with DevTools enabled for these tools to work.

Run DevDash with DevTools:
    cd devdash && ./build/dev/devdash --profile profiles/haltech-vcan.json
"""

import base64
import os
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from PIL import Image

from ..config import get_config

# Where DevTools-sourced PNGs are written. Same directory the system screenshot
# tools use, so all captures land in one place. Created on first use.
_SCREENSHOT_DIR = Path(tempfile.gettempdir()) / "devdash-mcp-screenshots"


def _save_png_bytes(data: bytes, prefix: str) -> tuple[Path, int, int]:
    """Write `data` (PNG bytes) to a temp file in `_SCREENSHOT_DIR`.

    Returns ``(path, width, height)``. Raises if `data` isn't a valid image.
    """
    width, height = Image.open(BytesIO(data)).size  # validates the bytes
    _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(prefix=f"{prefix}_", suffix=".png", dir=_SCREENSHOT_DIR)
    os.close(fd)
    path = Path(raw_path)
    path.write_bytes(data)
    return path, width, height


def _thumbnail_b64(data: bytes, max_dim: int) -> tuple[str, int, int]:
    """Return ``(base64_png, width, height)`` for a downsampled copy of `data`."""
    img = Image.open(BytesIO(data))
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / float(max(w, h))
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    out = BytesIO()
    img.save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode("utf-8"), img.size[0], img.size[1]


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
    async def devdash_telemetry_screenshot(
        window: str,
        inline_thumbnail: bool = False,
        thumbnail_max_dim: int = 400,
    ) -> dict[str, Any]:
        """Capture a screenshot via the DevDash DevTools API (devdash repo).

        Captures through the DevDash application itself (it renders the window
        and the DevTools endpoint returns the PNG), independent of the desktop
        compositor.

        Pixel data is opt-in — like ``screenshot_capture``, the PNG is written
        to ``/tmp/devdash-mcp-screenshots/`` and the response is the path plus
        dimensions; set ``inline_thumbnail=True`` to also receive a base64
        downsampled preview (intended for surfacing to a human, not for
        programmatic analysis — feed the disk path to the image tools instead).

        Args:
            window: Window name (e.g., 'cluster', 'headunit').
            inline_thumbnail: If True, include a base64 thumbnail. Default
                False (path-only).
            thumbnail_max_dim: Longest-side cap for the thumbnail in pixels
                (default 400; clamped to >= 1). Ignored when inline_thumbnail
                is False.

        Returns:
            On success:
                {
                  "path": "/tmp/devdash-mcp-screenshots/...png",
                  "width": int,
                  "height": int,
                  "source": "devtools",
                  # only when inline_thumbnail=True:
                  "thumbnail_base64": str,
                  "thumbnail_width": int,
                  "thumbnail_height": int,
                  "thumbnail_mime_type": "image/png",
                }
            On failure: {"error": <reason>}.
        """
        config = get_config()
        url = f"{config.devtools_base_url}/api/screenshot"

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url, params={"window": window})
                response.raise_for_status()
                data = response.content
        except httpx.ConnectError:
            return {"error": "Cannot connect to DevDash DevTools API (devdash repo)"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
        except Exception as e:
            return {"error": str(e)}

        try:
            path, width, height = _save_png_bytes(data, "devtools")
        except Exception as e:
            return {"error": f"DevTools returned data that isn't a valid PNG: {e}"}

        result: dict[str, Any] = {
            "path": str(path),
            "width": width,
            "height": height,
            "source": "devtools",
        }
        if inline_thumbnail:
            b64, tw, th = _thumbnail_b64(data, max(1, int(thumbnail_max_dim)))
            result["thumbnail_base64"] = b64
            result["thumbnail_width"] = tw
            result["thumbnail_height"] = th
            result["thumbnail_mime_type"] = "image/png"
        return result
