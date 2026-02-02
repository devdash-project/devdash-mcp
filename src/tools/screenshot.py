"""Screenshot tools - X11 window capture using system tools."""

import base64
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


def _run_command(cmd: list[str]) -> tuple[str, int]:
    """Run a shell command and return (stdout, returncode)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout, result.returncode
    except FileNotFoundError:
        return f"Error: Command not found: {cmd[0]}", 1


def _list_x11_windows() -> list[dict[str, Any]]:
    """List all X11 windows using wmctrl or xwininfo."""
    windows: list[dict[str, Any]] = []

    # Try wmctrl first (more reliable)
    stdout, returncode = _run_command(["wmctrl", "-l", "-G"])
    if returncode == 0:
        # Parse wmctrl output: "0x01c0000a  0 x y width height hostname title"
        for line in stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split(None, 7)
            if len(parts) >= 8:
                windows.append({
                    "id": parts[0],
                    "title": parts[7],
                    "width": int(parts[4]),
                    "height": int(parts[5]),
                    "x": int(parts[2]),
                    "y": int(parts[3]),
                })
        return windows

    # Fallback to xwininfo
    stdout, returncode = _run_command(["xwininfo", "-root", "-tree"])
    if returncode != 0:
        return []

    # Parse xwininfo output
    pattern = r'^\s+(0x[0-9a-f]+)\s+"([^"]+)".*?(\d+)x(\d+)'
    for line in stdout.split("\n"):
        match = re.search(pattern, line)
        if match:
            width = int(match.group(3))
            height = int(match.group(4))
            # Skip trivial windows
            if width > 100 and height > 100:
                windows.append({
                    "id": match.group(1),
                    "title": match.group(2),
                    "width": width,
                    "height": height,
                })

    return windows


def _find_window_by_name(name: str) -> str | None:
    """Find window ID by name (case-insensitive substring match)."""
    windows = _list_x11_windows()
    name_lower = name.lower()

    # Exact match first
    for window in windows:
        if window["title"].lower() == name_lower:
            return window["id"]

    # Substring match
    for window in windows:
        if name_lower in window["title"].lower():
            return window["id"]

    return None


def _capture_window(
    window_id: str,
    scale: float = 1.0,
    crop_center: float | None = None,
    crop_left: float | None = None,
) -> bytes | None:
    """Capture screenshot of window using ImageMagick or scrot.

    Args:
        window_id: X11 window ID to capture
        scale: Scale factor (0.1-1.0). Values < 1.0 reduce image size.
        crop_center: Crop to center portion (0.1-1.0). E.g., 0.5 keeps center 50%.
        crop_left: Crop to left portion (0.1-1.0). E.g., 0.6 keeps left 60%.
                   Applied BEFORE crop_center if both specified.
    """
    # Try ImageMagick's import command
    result = subprocess.run(
        ["import", "-window", window_id, "png:-"],
        capture_output=True,
        check=False,
    )

    if result.returncode == 0:
        screenshot_data = result.stdout

        # Apply left crop first (for extracting preview pane from explorer)
        if crop_left is not None and crop_left < 1.0:
            crop_percent = int(crop_left * 100)
            crop_result = subprocess.run(
                [
                    "convert", "png:-",
                    "-gravity", "West",
                    "-crop", f"{crop_percent}%x100%+0+0",
                    "+repage",
                    "png:-",
                ],
                input=screenshot_data,
                capture_output=True,
                check=False,
            )
            if crop_result.returncode == 0:
                screenshot_data = crop_result.stdout

        # Apply center crop (for focusing on gauge within preview pane)
        if crop_center is not None and crop_center < 1.0:
            crop_percent = int(crop_center * 100)
            crop_result = subprocess.run(
                [
                    "convert", "png:-",
                    "-gravity", "center",
                    "-crop", f"{crop_percent}%x{crop_percent}%+0+0",
                    "+repage",
                    "png:-",
                ],
                input=screenshot_data,
                capture_output=True,
                check=False,
            )
            if crop_result.returncode == 0:
                screenshot_data = crop_result.stdout

        # Apply scaling if requested
        if scale < 1.0:
            scale_percent = int(scale * 100)
            resize_result = subprocess.run(
                ["convert", "png:-", "-resize", f"{scale_percent}%", "png:-"],
                input=screenshot_data,
                capture_output=True,
                check=False,
            )
            if resize_result.returncode == 0:
                return resize_result.stdout

        return screenshot_data

    # Fallback to scrot
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    subprocess.run(["scrot", "-u", "-o", tmp_path], check=False)

    tmp_file = Path(tmp_path)
    if tmp_file.exists():
        data = tmp_file.read_bytes()
        tmp_file.unlink()
        return data

    return None


# Keywords for filtering DevDash-related windows
DEVDASH_KEYWORDS = ["gauge", "explorer", "devdash", "qml", "cluster", "headunit"]


def register_screenshot_tools(mcp: FastMCP) -> None:
    """Register screenshot tools with the MCP server."""

    @mcp.tool()
    def screenshot_list_windows() -> dict[str, Any]:
        """List available windows for screenshot capture.

        Returns windows matching DevDash-related keywords (gauge, explorer,
        devdash, qml, cluster, headunit).

        Returns:
            List of windows with id, title, dimensions, and position
        """
        windows = _list_x11_windows()

        # Filter for DevDash-related windows
        filtered = [
            w
            for w in windows
            if any(kw in w["title"].lower() for kw in DEVDASH_KEYWORDS)
        ]

        return {
            "windows": filtered,
            "count": len(filtered),
        }

    def _internal_capture(
        window: str,
        scale: float,
        crop_left: float | None = None,
        crop_center: float | None = None,
    ) -> dict[str, Any]:
        """Internal capture function - not exposed as MCP tool."""
        window_id = _find_window_by_name(window)

        if not window_id:
            windows = _list_x11_windows()
            filtered = [
                w["title"]
                for w in windows
                if any(kw in w["title"].lower() for kw in DEVDASH_KEYWORDS)
            ]
            return {
                "error": f"Window '{window}' not found",
                "available_windows": filtered,
            }

        screenshot_data = _capture_window(
            window_id,
            scale=scale,
            crop_left=crop_left,
            crop_center=crop_center,
        )

        if not screenshot_data:
            return {
                "error": f"Failed to capture screenshot of '{window}' (ID: {window_id}). "
                "Make sure 'import' (ImageMagick) or 'scrot' is installed.",
            }

        return {
            "result": {
                "image": base64.b64encode(screenshot_data).decode("utf-8"),
                "mime_type": "image/png",
                "window_id": window_id,
            }
        }

    @mcp.tool()
    def screenshot_capture(
        window: str,
        scale: float = 0.5,
        crop_center: float | None = None,
    ) -> dict[str, Any]:
        """Capture a PNG screenshot of a window.

        Args:
            window: Window name to capture (case-insensitive substring match).
                    Examples: 'explorer', 'cluster', 'DevDash Gauges Explorer'
            scale: Scale factor (0.1-1.0, default 1.0). Lower values reduce image
                   size and context usage. Recommended: 0.5 for most visual checks.
            crop_center: Crop to center portion before scaling (0.1-1.0, optional).
                         E.g., 0.4 keeps only the center 40% of the image.
                         Useful for focusing on the gauge preview area.

        Returns:
            PNG image data or error message
        """
        # Clamp scale to valid range - minimum 0.3 to prevent context explosion
        scale = max(0.3, min(1.0, scale))

        # Clamp crop_center if provided
        if crop_center is not None:
            crop_center = max(0.1, min(1.0, crop_center))

        result = _internal_capture(
            window=window,
            scale=scale,
            crop_center=crop_center,
        )
        if "error" in result:
            return result
        return result["result"]

    @mcp.tool()
    def screenshot_gauge_preview(
        window: str = "explorer",
    ) -> dict[str, Any]:
        """Capture a compact screenshot focused on the gauge preview area.

        IMPORTANT: This is the primary screenshot tool for verifying gauge visuals.
        It crops to the LEFT 60% (preview pane) then focuses on the center where
        the gauge is rendered.

        The explorer layout is:
        - Left 60%: Preview area with gauge centered
        - Right 40%: Property panel (excluded from capture)

        Uses ~5k tokens instead of ~25k for full screenshots.

        Args:
            window: Window name to capture (default: 'explorer').
                    Case-insensitive substring match.

        Returns:
            PNG image data focused on gauge preview area, or error message
        """
        # Crop to left 60% (preview pane), then center 80% of that, scale 50%
        result = _internal_capture(
            window=window,
            scale=0.5,
            crop_left=0.6,
            crop_center=0.8,
        )
        if "error" in result:
            return result
        return result["result"]
