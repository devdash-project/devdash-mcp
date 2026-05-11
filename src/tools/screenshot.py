"""Screenshot tools - window capture for X11 and Wayland (Hyprland).

Session type is detected once at server startup from XDG_SESSION_TYPE and the
appropriate code path is used for every capture. On Wayland, windows are
enumerated via `hyprctl clients -j` and the screen is captured via `grim`,
then cropped to the target window's geometry with Pillow. The legacy X11 path
(wmctrl/xwininfo + ImageMagick `import`) is retained for non-Wayland sessions.

External binaries required (Wayland):  grim, hyprctl
External binaries required (X11):      wmctrl or xwininfo, ImageMagick `import` or scrot
"""

import base64
import json
import logging
import os
import re
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from PIL import Image

logger = logging.getLogger(__name__)


# ---- Session-type detection (cached at import) -----------------------------

def _detect_session_type() -> str:
    """Return 'wayland' or 'x11' from XDG_SESSION_TYPE. Defaults to 'x11'."""
    raw = (os.environ.get("XDG_SESSION_TYPE") or "").strip().lower()
    if raw == "wayland":
        return "wayland"
    return "x11"


SESSION_TYPE = _detect_session_type()
logger.info("Screenshot session type: %s", SESSION_TYPE)


# ---- Generic shell helper --------------------------------------------------

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


# ---- Wayland (Hyprland) path -----------------------------------------------

def _list_hyprland_windows() -> list[dict[str, Any]]:
    """List Hyprland windows via `hyprctl clients -j`."""
    stdout, rc = _run_command(["hyprctl", "clients", "-j"])
    if rc != 0:
        return []
    try:
        clients = json.loads(stdout)
    except json.JSONDecodeError:
        return []

    out: list[dict[str, Any]] = []
    for c in clients:
        at = c.get("at") or [0, 0]
        size = c.get("size") or [0, 0]
        out.append({
            "id": c.get("address", ""),
            "title": c.get("title", ""),
            "class": c.get("class", "") or c.get("initialClass", ""),
            "x": int(at[0]),
            "y": int(at[1]),
            "width": int(size[0]),
            "height": int(size[1]),
        })
    return out


def _find_hyprland_window(name: str) -> dict[str, Any] | None:
    """Find a Hyprland window by case-insensitive substring match on title or class."""
    needle = name.lower()
    windows = _list_hyprland_windows()
    # Skip zero-size windows (hidden/minimized).
    windows = [w for w in windows if w["width"] > 0 and w["height"] > 0]
    # Exact match on title first.
    for w in windows:
        if w["title"].lower() == needle:
            return w
    # Substring match on title or class.
    for w in windows:
        if needle in w["title"].lower() or needle in w["class"].lower():
            return w
    return None


def _capture_hyprland_window(win: dict[str, Any]) -> bytes | None:
    """Capture a Hyprland window region using `grim`."""
    geom = f"{win['x']},{win['y']} {win['width']}x{win['height']}"
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ["grim", "-g", geom, tmp_path],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return Path(tmp_path).read_bytes()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---- X11 path (legacy) -----------------------------------------------------

def _list_x11_windows() -> list[dict[str, Any]]:
    """List X11 windows using wmctrl or xwininfo."""
    windows: list[dict[str, Any]] = []

    stdout, returncode = _run_command(["wmctrl", "-l", "-G"])
    if returncode == 0:
        for line in stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split(None, 7)
            if len(parts) >= 8:
                windows.append({
                    "id": parts[0],
                    "title": parts[7],
                    "class": "",
                    "width": int(parts[4]),
                    "height": int(parts[5]),
                    "x": int(parts[2]),
                    "y": int(parts[3]),
                })
        return windows

    stdout, returncode = _run_command(["xwininfo", "-root", "-tree"])
    if returncode != 0:
        return []

    pattern = r'^\s+(0x[0-9a-f]+)\s+"([^"]+)".*?(\d+)x(\d+)'
    for line in stdout.split("\n"):
        match = re.search(pattern, line)
        if match:
            width = int(match.group(3))
            height = int(match.group(4))
            if width > 100 and height > 100:
                windows.append({
                    "id": match.group(1),
                    "title": match.group(2),
                    "class": "",
                    "width": width,
                    "height": height,
                    "x": 0,
                    "y": 0,
                })
    return windows


def _find_x11_window_id(name: str) -> str | None:
    """Find X11 window ID by case-insensitive substring match."""
    windows = _list_x11_windows()
    needle = name.lower()
    for w in windows:
        if w["title"].lower() == needle:
            return w["id"]
    for w in windows:
        if needle in w["title"].lower():
            return w["id"]
    return None


def _capture_x11_window(window_id: str) -> bytes | None:
    """Capture an X11 window with ImageMagick `import`, fall back to scrot."""
    result = subprocess.run(
        ["import", "-window", window_id, "png:-"],
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
    subprocess.run(["scrot", "-u", "-o", tmp_path], check=False)
    f = Path(tmp_path)
    if f.exists():
        data = f.read_bytes()
        f.unlink()
        return data
    return None


# ---- Cross-session unified helpers -----------------------------------------

def _list_windows_unified() -> list[dict[str, Any]]:
    if SESSION_TYPE == "wayland":
        return _list_hyprland_windows()
    return _list_x11_windows()


def _find_window_unified(name: str) -> dict[str, Any] | None:
    """Find a window across either session type, returning a dict with geometry."""
    if SESSION_TYPE == "wayland":
        return _find_hyprland_window(name)
    win_id = _find_x11_window_id(name)
    if not win_id:
        return None
    for w in _list_x11_windows():
        if w["id"] == win_id:
            return w
    return {"id": win_id, "title": name, "class": "", "x": 0, "y": 0, "width": 0, "height": 0}


def _capture_window_unified(win: dict[str, Any]) -> bytes | None:
    if SESSION_TYPE == "wayland":
        return _capture_hyprland_window(win)
    return _capture_x11_window(win["id"])


# ---- PIL post-processing ---------------------------------------------------

def _apply_crop_left(data: bytes, fraction: float) -> bytes:
    if fraction is None or fraction >= 1.0:
        return data
    img = Image.open(BytesIO(data))
    w, h = img.size
    new_w = max(1, int(w * fraction))
    out = BytesIO()
    img.crop((0, 0, new_w, h)).save(out, format="PNG")
    return out.getvalue()


def _apply_crop_center(data: bytes, fraction: float) -> bytes:
    if fraction is None or fraction >= 1.0:
        return data
    img = Image.open(BytesIO(data))
    w, h = img.size
    new_w = max(1, int(w * fraction))
    new_h = max(1, int(h * fraction))
    x = (w - new_w) // 2
    y = (h - new_h) // 2
    out = BytesIO()
    img.crop((x, y, x + new_w, y + new_h)).save(out, format="PNG")
    return out.getvalue()


def _apply_scale(data: bytes, scale: float) -> bytes:
    if scale is None or scale >= 1.0:
        return data
    img = Image.open(BytesIO(data))
    w, h = img.size
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    out = BytesIO()
    img.resize((new_w, new_h), Image.LANCZOS).save(out, format="PNG")
    return out.getvalue()


# ---- Keyword filter for the list tool --------------------------------------

DEVDASH_KEYWORDS = ["gauge", "explorer", "devdash", "qml", "cluster", "headunit"]


def register_screenshot_tools(mcp: FastMCP) -> None:
    """Register screenshot tools with the MCP server."""

    @mcp.tool()
    def screenshot_list_windows() -> dict[str, Any]:
        """List available windows for screenshot capture.

        Returns windows matching DevDash-related keywords (gauge, explorer,
        devdash, qml, cluster, headunit). Routes via X11 or Hyprland based on
        XDG_SESSION_TYPE at server startup.

        Returns:
            List of windows with id, title, dimensions, and position
        """
        windows = _list_windows_unified()
        filtered = [
            w
            for w in windows
            if any(kw in w["title"].lower() or kw in w.get("class", "").lower()
                   for kw in DEVDASH_KEYWORDS)
        ]
        return {
            "session_type": SESSION_TYPE,
            "windows": filtered,
            "count": len(filtered),
        }

    def _internal_capture(
        window: str,
        scale: float,
        crop_left: float | None = None,
        crop_center: float | None = None,
    ) -> dict[str, Any]:
        win = _find_window_unified(window)
        if not win:
            available = [
                w["title"]
                for w in _list_windows_unified()
                if any(kw in w["title"].lower() or kw in w.get("class", "").lower()
                       for kw in DEVDASH_KEYWORDS)
            ]
            return {
                "error": f"Window '{window}' not found (session_type={SESSION_TYPE})",
                "available_windows": available,
            }

        data = _capture_window_unified(win)
        if not data:
            tool_hint = (
                "Make sure 'grim' is installed." if SESSION_TYPE == "wayland"
                else "Make sure 'import' (ImageMagick) or 'scrot' is installed."
            )
            return {"error": f"Failed to capture screenshot of '{window}'. {tool_hint}"}

        if crop_left is not None:
            data = _apply_crop_left(data, crop_left)
        if crop_center is not None:
            data = _apply_crop_center(data, crop_center)
        if scale < 1.0:
            data = _apply_scale(data, scale)

        return {
            "result": {
                "image": base64.b64encode(data).decode("utf-8"),
                "mime_type": "image/png",
                "window_id": win.get("id", ""),
            }
        }

    @mcp.tool()
    def screenshot_capture(
        window: str,
        scale: float = 0.5,
        crop_center: float | None = None,
    ) -> dict[str, Any]:
        """Capture a PNG screenshot of a window.

        Works on X11 and Wayland (Hyprland). Session type is auto-detected
        at server startup from XDG_SESSION_TYPE.

        Args:
            window: Window name (case-insensitive substring match on title or
                    on Wayland class).
            scale: Scale factor (0.3-1.0, default 0.5).
            crop_center: Crop to center portion before scaling (0.1-1.0, optional).

        Returns:
            PNG image data (base64) or error.
        """
        scale = max(0.3, min(1.0, scale))
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

        Crops to LEFT 60% (preview pane), then CENTER 80% (gauge), scaled to 50%.
        Works on X11 and Wayland (Hyprland) — session is auto-detected.

        Args:
            window: Window name (default 'explorer'). Substring match on title
                    or window class.

        Returns:
            PNG image data (base64) or error.
        """
        result = _internal_capture(
            window=window,
            scale=0.5,
            crop_left=0.6,
            crop_center=0.8,
        )
        if "error" in result:
            return result
        return result["result"]
