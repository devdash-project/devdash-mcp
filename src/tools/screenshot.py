"""Screenshot tools - window capture for X11 and Wayland (Hyprland).

Session type is detected once at server startup from XDG_SESSION_TYPE and the
appropriate code path is used for every capture. On Wayland, windows are
enumerated via `hyprctl clients -j` and the screen is captured via `grim`,
then cropped to the target window's geometry with Pillow. The legacy X11 path
(wmctrl/xwininfo + ImageMagick `import`) is retained for non-Wayland sessions.

External binaries required (Wayland):  grim, hyprctl
External binaries required (X11):      wmctrl or xwininfo, ImageMagick `import` or scrot

By default the screenshot tools return a path to the saved PNG on disk plus
its dimensions; pixel data is opt-in via ``inline_thumbnail`` to keep agent
context small. See tool docstrings for the response shape.
"""

import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from PIL import Image

# Directory for saved screenshot PNGs. Created on first use; not cleaned up
# automatically (caller is responsible for managing scratch space).
SCREENSHOT_DIR = Path(tempfile.gettempdir()) / "devdash-mcp-screenshots"

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


def _refresh_window_geometry(win: dict[str, Any]) -> dict[str, Any]:
    """Re-read the live x/y/width/height of ``win`` (matched by id).

    The geometry first observed for a window can be stale by the time we
    capture: focusing it may have moved it (a tiling reflow, or switching to
    its workspace), and on a tiling compositor the window's size is whatever
    the layout assigns — it isn't the explorer's choice, and it changes as the
    workspace's other windows come and go. ``grim`` needs explicit coordinates,
    so a stale crop region bleeds in neighbouring windows / the wallpaper. We
    therefore re-query right before the capture (after the focus settle).

    Returns ``win`` merged with the fresh geometry, or ``win`` unchanged if the
    window can't be re-located (best effort — better a possibly-stale crop than
    no capture).
    """
    wid = win.get("id")
    if not wid:
        return win
    for w in _list_windows_unified():
        if w.get("id") == wid:
            fresh = {k: w[k] for k in ("x", "y", "width", "height") if k in w}
            return {**win, **fresh}
    return win


def _capture_window_unified(win: dict[str, Any]) -> bytes | None:
    if SESSION_TYPE == "wayland":
        return _capture_hyprland_window(win)
    return _capture_x11_window(win["id"])


# ---- Window focus management -----------------------------------------------
#
# Screenshots must be taken with the target window *focused*. Compositors apply
# effects to unfocused windows — e.g. Hyprland's `inactive_opacity` plus blur
# with `xray = true` makes an unfocused window translucent and bleeds the
# wallpaper through, so a capture of the unfocused explorer looks brighter and
# more colourful than the user sees in direct view. On Wayland a window on an
# inactive workspace isn't composited at all, so `grim` would capture the
# wallpaper. Focusing the window before the capture makes the result match the
# user's direct view regardless of compositor settings; focus is restored
# afterwards so the user's interactive flow isn't disrupted (this can briefly
# flip the active window/workspace).

# Seconds to wait after focusing before capturing, so the compositor finishes
# any focus-in opacity/blur transition (Hyprland's default fade is ~0.2-0.4s).
_FOCUS_SETTLE_SECONDS = 0.3


def _hyprland_active_address() -> str | None:
    """Address (``0x...``) of the currently-focused Hyprland window, or None."""
    stdout, rc = _run_command(["hyprctl", "activewindow", "-j"])
    if rc != 0:
        return None
    try:
        info = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(info, dict):
        return None
    addr = info.get("address")
    return addr or None


def _hyprland_focus(address: str) -> bool:
    if not address:
        return False
    _, rc = _run_command(
        ["hyprctl", "dispatch", "focuswindow", f"address:{address}"]
    )
    return rc == 0


def _x11_active_window_id() -> str | None:
    """Window id of the currently-focused X11 window, or None.

    Uses ``xdotool``; if it isn't installed we simply can't restore focus
    afterwards (best-effort).
    """
    stdout, rc = _run_command(["xdotool", "getactivewindow"])
    if rc != 0:
        return None
    wid = stdout.strip()
    return wid or None


def _x11_focus(window_id: str) -> bool:
    if not window_id:
        return False
    # `wmctrl -i -a` wants a hex id (0x...), which is what our window list
    # provides. `xdotool windowactivate` accepts hex or decimal and is the
    # fallback (and the path for ids from `xdotool getactivewindow`, decimal).
    if window_id.lower().startswith("0x"):
        _, rc = _run_command(["wmctrl", "-i", "-a", window_id])
        if rc == 0:
            return True
    _, rc = _run_command(["xdotool", "windowactivate", window_id])
    return rc == 0


def _focus_target_window(win: dict[str, Any]) -> tuple[bool, str | None]:
    """Focus ``win``. Returns ``(focused_ok, prior_focus_token)``.

    ``prior_focus_token`` identifies the previously-focused window for the
    current session type (Hyprland address / X11 window id), suitable to pass
    to :func:`_restore_focus`, or ``None`` if it couldn't be determined.
    """
    target_id = win.get("id", "")
    if SESSION_TYPE == "wayland":
        prior = _hyprland_active_address()
        return _hyprland_focus(target_id), prior
    prior = _x11_active_window_id()
    return _x11_focus(target_id), prior


def _restore_focus(token: str | None) -> None:
    """Best-effort: re-focus the window identified by ``token``. No-op if None."""
    if not token:
        return
    if SESSION_TYPE == "wayland":
        _hyprland_focus(token)
    else:
        _x11_focus(token)


def _capture_focused(win: dict[str, Any]) -> tuple[bytes | None, bool]:
    """Focus ``win``, capture it, then restore prior focus.

    Returns ``(png_bytes_or_None, focused_ok)``. Focusing the window before the
    capture avoids compositor effects applied to *unfocused* windows
    (translucency, blur xray bleeding the wallpaper through, an inactive
    workspace that isn't composited). If focusing fails we still attempt the
    capture (the image may be distorted; ``focused_ok`` is ``False``). Prior
    focus is restored afterwards on a best-effort basis, unless the target was
    already the focused window.

    The window's geometry is re-read *after* the focus settle (focusing can
    move/resize a tiled window) so the crop region matches where the window
    actually is at capture time, not where it was first seen.
    """
    focused_ok, prior_focus = _focus_target_window(win)
    if focused_ok:
        # Let the compositor finish any focus-in opacity/blur transition.
        time.sleep(_FOCUS_SETTLE_SECONDS)
    win = _refresh_window_geometry(win)
    data = _capture_window_unified(win)
    if focused_ok and prior_focus and prior_focus != win.get("id", ""):
        _restore_focus(prior_focus)
    return data, focused_ok


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

    def _save_png(data: bytes, prefix: str) -> tuple[Path, int, int]:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        img = Image.open(BytesIO(data))
        w, h = img.size
        fd, raw_path = tempfile.mkstemp(prefix=f"{prefix}_", suffix=".png", dir=SCREENSHOT_DIR)
        os.close(fd)
        path = Path(raw_path)
        path.write_bytes(data)
        return path, w, h

    def _make_thumbnail(path: Path, max_dim: int) -> tuple[str, int, int]:
        img = Image.open(path)
        w, h = img.size
        if max(w, h) <= max_dim:
            scaled = img.copy()
        else:
            scale = max_dim / float(max(w, h))
            scaled = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        out = BytesIO()
        scaled.save(out, format="PNG")
        sw, sh = scaled.size
        return base64.b64encode(out.getvalue()).decode("utf-8"), sw, sh

    def _apply_roi(data: bytes, roi: dict[str, int]) -> bytes:
        img = Image.open(BytesIO(data))
        w, h = img.size
        x = max(0, min(w, int(roi["x"])))
        y = max(0, min(h, int(roi["y"])))
        rw = max(1, min(w - x, int(roi["width"])))
        rh = max(1, min(h - y, int(roi["height"])))
        out = BytesIO()
        img.crop((x, y, x + rw, y + rh)).save(out, format="PNG")
        return out.getvalue()

    def _internal_capture(
        window: str,
        scale: float,
        crop_left: float | None = None,
        crop_center: float | None = None,
        roi: dict[str, int] | None = None,
        prefix: str = "screenshot",
        inline_thumbnail: bool = False,
        thumbnail_max_dim: int = 400,
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

        # Capture with the window focused so compositor effects on unfocused
        # windows (translucency, blur xray, un-composited inactive workspaces)
        # don't distort the result; prior focus is restored afterwards.
        data, focused_ok = _capture_focused(win)
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
        if roi is not None:
            data = _apply_roi(data, roi)

        path, width, height = _save_png(data, prefix)
        result: dict[str, Any] = {
            "path": str(path),
            "width": width,
            "height": height,
            "window_id": win.get("id", ""),
            "focused": focused_ok,
        }
        if not focused_ok:
            result["focus_warning"] = (
                "Could not focus the target window before capture; the image "
                "may show compositor effects applied to unfocused windows "
                "(translucency / blur / un-composited workspace). Check that "
                "hyprctl (Wayland) or wmctrl/xdotool (X11) is installed."
            )
        if inline_thumbnail:
            b64, tw, th = _make_thumbnail(path, thumbnail_max_dim)
            result["thumbnail_base64"] = b64
            result["thumbnail_width"] = tw
            result["thumbnail_height"] = th
            result["thumbnail_mime_type"] = "image/png"
        return result

    @mcp.tool()
    def screenshot_capture(
        window: str,
        scale: float = 0.5,
        crop_center: float | None = None,
        roi: dict[str, int] | None = None,
        inline_thumbnail: bool = False,
        thumbnail_max_dim: int = 400,
    ) -> dict[str, Any]:
        """Capture a PNG screenshot of a window.

        Works on X11 and Wayland (Hyprland). Session type is auto-detected at
        server startup from XDG_SESSION_TYPE.

        Pixel data is opt-in. By default this tool writes the PNG to disk and
        returns its path plus dimensions. Set ``inline_thumbnail=True`` to also
        receive a base64-encoded downsampled preview (intended for surfacing
        results to a human, not for programmatic analysis — use the disk path
        plus the image tools for analysis).

        The target window is focused before the capture (and the previously
        focused window restored afterwards) so the image matches the user's
        direct view rather than the compositor's treatment of an unfocused
        window — translucency, blur xray bleeding the wallpaper through, or an
        inactive workspace that isn't composited at all. Briefly flips the
        active window/workspace as a side effect. ``focused`` in the result
        reports whether this succeeded.

        Composition: focus window -> re-read window geometry -> window capture
        -> restore focus -> optional left-/center-crop -> optional scale ->
        optional roi crop -> save to disk -> optional thumbnail.

        Note: the captured window can be a *different size on different calls* —
        a tiling window manager sizes the explorer from its layout, not from
        the explorer's ``width:``/``height:``, and that layout shifts as other
        windows come and go (or as this tool's focus dance flips workspaces).
        Don't hardcode ``roi`` pixel coordinates across captures; derive them
        from the ``width``/``height`` returned by the *previous* capture.

        Args:
            window: Window name (case-insensitive substring match on title or
                    class).
            scale: Scale factor (0.3-1.0, default 0.5).
            crop_center: Crop to center portion before ROI (0.1-1.0, optional).
            roi: {"x", "y", "width", "height"} in post-scale/crop pixel
                 coordinates. If provided, the saved PNG is cropped to this
                 region. Clamped to the image bounds.
            inline_thumbnail: If True, include a base64 thumbnail in the
                 response. Default False (path-only).
            thumbnail_max_dim: Longest-side cap for the thumbnail in pixels
                 (default 400). Ignored if inline_thumbnail is False.

        Returns:
            On success:
                {
                  "path": "/tmp/devdash-mcp-screenshots/...png",
                  "width": int,
                  "height": int,
                  "window_id": str,
                  "focused": bool,          # window was focused before capture
                  "focus_warning": str,     # only when "focused" is False
                  # only when inline_thumbnail=True:
                  "thumbnail_base64": str,
                  "thumbnail_width": int,
                  "thumbnail_height": int,
                  "thumbnail_mime_type": "image/png",
                }
            On failure (window not found, capture tool missing): {"error": <reason>}.
        """
        scale = max(0.3, min(1.0, scale))
        if crop_center is not None:
            crop_center = max(0.1, min(1.0, crop_center))

        return _internal_capture(
            window=window,
            scale=scale,
            crop_center=crop_center,
            roi=roi,
            prefix="capture",
            inline_thumbnail=inline_thumbnail,
            thumbnail_max_dim=thumbnail_max_dim,
        )

    @mcp.tool()
    def screenshot_gauge_preview(
        window: str = "explorer",
        roi: dict[str, int] | None = None,
        inline_thumbnail: bool = False,
        thumbnail_max_dim: int = 400,
    ) -> dict[str, Any]:
        """Capture a compact screenshot focused on the gauge preview area.

        Crops to LEFT 60% (preview pane), then CENTER 80% (gauge), scaled 0.5.
        Works on X11 and Wayland (Hyprland) — session auto-detected.

        Like ``screenshot_capture``, the target window is focused before the
        capture (and prior focus restored afterwards) so the image matches the
        user's direct view rather than the compositor's unfocused-window
        treatment.

        Pixel data is opt-in. Default response is path + dimensions; pass
        ``inline_thumbnail=True`` to also include a base64 preview.

        Note: the captured window can be a *different size on different calls*
        (a tiling window manager sizes the explorer from its layout, not from
        the explorer's ``width:``), so the post-pipeline image size varies too.
        Don't hardcode ``roi`` pixel coordinates across captures; derive them
        from the ``width``/``height`` returned by the *previous* capture.

        Args:
            window: Window name (default 'explorer'). Substring match on title
                    or class.
            roi: Optional {"x", "y", "width", "height"} crop applied AFTER the
                 default preview/center/scale pipeline, in pixel coordinates
                 of the cropped+scaled image. Clamped to bounds.
            inline_thumbnail: If True, include a base64 thumbnail. Default
                 False (path-only).
            thumbnail_max_dim: Longest-side cap for the thumbnail (default 400).

        Returns:
            See screenshot_capture for the response shape.
        """
        return _internal_capture(
            window=window,
            scale=0.5,
            crop_left=0.6,
            crop_center=0.8,
            roi=roi,
            prefix="preview",
            inline_thumbnail=inline_thumbnail,
            thumbnail_max_dim=thumbnail_max_dim,
        )
