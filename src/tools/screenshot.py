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


def _capture_window(window_id: str) -> bytes | None:
    """Capture screenshot of window using ImageMagick or scrot."""
    # Try ImageMagick's import command
    result = subprocess.run(
        ["import", "-window", window_id, "png:-"],
        capture_output=True,
        check=False,
    )

    if result.returncode == 0:
        return result.stdout

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

    @mcp.tool()
    def screenshot_capture(window: str) -> dict[str, Any] | bytes:
        """Capture a PNG screenshot of a window.

        Args:
            window: Window name to capture (case-insensitive substring match).
                    Examples: 'explorer', 'cluster', 'DevDash Gauges Explorer'

        Returns:
            PNG image data or error message
        """
        window_id = _find_window_by_name(window)

        if not window_id:
            # List available windows for helpful error
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

        screenshot_data = _capture_window(window_id)

        if not screenshot_data:
            return {
                "error": f"Failed to capture screenshot of '{window}' (ID: {window_id}). "
                "Make sure 'import' (ImageMagick) or 'scrot' is installed.",
            }

        # Return base64-encoded image for MCP
        return {
            "image": base64.b64encode(screenshot_data).decode("utf-8"),
            "mime_type": "image/png",
            "window_id": window_id,
        }
