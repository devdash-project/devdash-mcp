"""End-to-end tests for the screenshot MCP tools with the OS calls faked.

Exercises the real ``screenshot_capture`` / ``screenshot_gauge_preview`` /
``screenshot_list_windows`` tool functions (pulled out of a real ``FastMCP``
registration) on the Wayland code path, with ``hyprctl`` mocked via
``_run_command`` and ``grim`` mocked via ``subprocess.run`` (it "writes" a
real PNG of the requested size). The headline assertion for the focus fix is
that the target window is focused before ``grim`` runs and prior focus is
restored after.

Run from the repo root:  python -m unittest discover
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp.server.fastmcp import FastMCP
from PIL import Image

from src.tools import screenshot
from src.tools.screenshot import register_screenshot_tools


_EXPLORER_WINDOW = {
    "address": "0xEXPLORER",
    "title": "DevDash Gauges Explorer",
    "class": "io.devdash.qml-gauges-explorer",
    "initialClass": "io.devdash.qml-gauges-explorer",
    "at": [100, 100],
    "size": [800, 600],
}
_TERMINAL_ADDRESS = "0xTERMINAL"


def _screenshot_tools() -> dict:
    mcp = FastMCP("test")
    register_screenshot_tools(mcp)
    return {t.name: t.fn for t in mcp._tool_manager.list_tools()}


class FakeDesktop:
    """Stand-in for hyprctl + grim. Records the focus/capture call sequence."""

    def __init__(self, *, clients=None, focus_succeeds=True, grim_succeeds=True, active_address=_TERMINAL_ADDRESS):
        self.clients = clients if clients is not None else [dict(_EXPLORER_WINDOW)]
        self.focus_succeeds = focus_succeeds
        self.grim_succeeds = grim_succeeds
        self.active_address = active_address
        self.events: list[tuple] = []  # ("focus", addr) / ("sleep",) / ("capture", geom)

    # patched in for screenshot._run_command (hyprctl)
    def run_command(self, cmd: list[str]) -> tuple[str, int]:
        if cmd == ["hyprctl", "clients", "-j"]:
            return json.dumps(self.clients), 0
        if cmd == ["hyprctl", "activewindow", "-j"]:
            return json.dumps({"address": self.active_address, "title": "active"}), 0
        if cmd[:3] == ["hyprctl", "dispatch", "focuswindow"]:
            self.events.append(("focus", cmd[3].split("address:", 1)[-1]))
            return ("", 0 if self.focus_succeeds else 1)
        return ("", 1)

    # patched in for screenshot.subprocess.run (grim)
    def subprocess_run(self, cmd, **kwargs):
        if cmd and cmd[0] == "grim":
            geom = cmd[2]  # "x,y WxH"
            w, h = (int(v) for v in geom.split(" ", 1)[1].split("x", 1))
            self.events.append(("capture", geom))
            if self.grim_succeeds:
                Image.new("RGB", (w, h), (10, 20, 30)).save(cmd[3], format="PNG")
                return subprocess.CompletedProcess(cmd, 0, b"", b"")
            return subprocess.CompletedProcess(cmd, 1, b"", b"grim failed")
        return subprocess.CompletedProcess(cmd, 1, b"", b"")


class _ScreenshotEnvMixin(unittest.TestCase):
    def _install(self, desktop: FakeDesktop) -> None:
        fake_time = mock.MagicMock()
        fake_time.sleep.side_effect = lambda *_: desktop.events.append(("sleep",))
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        patches = [
            mock.patch.object(screenshot, "SESSION_TYPE", "wayland"),
            mock.patch.object(screenshot, "_run_command", desktop.run_command),
            mock.patch.object(screenshot.subprocess, "run", desktop.subprocess_run),
            mock.patch.object(screenshot, "time", fake_time),
            mock.patch.object(screenshot, "SCREENSHOT_DIR", Path(tmpdir.name)),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])


class CaptureFocusSequenceTests(_ScreenshotEnvMixin):
    def test_focuses_target_then_captures_then_restores(self):
        desktop = FakeDesktop()
        self._install(desktop)
        result = _screenshot_tools()["screenshot_capture"]("explorer", scale=1.0)
        self.assertNotIn("error", result)
        self.assertEqual(result["window_id"], "0xEXPLORER")
        self.assertEqual((result["width"], result["height"]), (800, 600))
        self.assertTrue(result["focused"])
        self.assertNotIn("focus_warning", result)
        self.assertTrue(Path(result["path"]).is_file())
        self.assertEqual(
            desktop.events,
            [("focus", "0xEXPLORER"), ("sleep",), ("capture", "100,100 800x600"), ("focus", _TERMINAL_ADDRESS)],
        )

    def test_focus_failure_still_captures_and_warns(self):
        desktop = FakeDesktop(focus_succeeds=False)
        self._install(desktop)
        result = _screenshot_tools()["screenshot_capture"]("explorer", scale=1.0)
        self.assertNotIn("error", result)
        self.assertFalse(result["focused"])
        self.assertIn("focus_warning", result)
        self.assertEqual(desktop.events, [("focus", "0xEXPLORER"), ("capture", "100,100 800x600")])

    def test_no_restore_when_target_already_focused(self):
        desktop = FakeDesktop(active_address="0xEXPLORER")
        self._install(desktop)
        _screenshot_tools()["screenshot_capture"]("explorer", scale=1.0)
        self.assertEqual(desktop.events, [("focus", "0xEXPLORER"), ("sleep",), ("capture", "100,100 800x600")])

    def test_window_not_found(self):
        desktop = FakeDesktop(clients=[])
        self._install(desktop)
        result = _screenshot_tools()["screenshot_capture"]("explorer")
        self.assertIn("error", result)
        self.assertIn("not found", result["error"])
        self.assertEqual(desktop.events, [])

    def test_grim_failure_is_reported(self):
        desktop = FakeDesktop(grim_succeeds=False)
        self._install(desktop)
        result = _screenshot_tools()["screenshot_capture"]("explorer")
        self.assertIn("error", result)
        self.assertIn("Failed to capture", result["error"])


class GaugePreviewTests(_ScreenshotEnvMixin):
    def test_crop_and_scale_pipeline(self):
        desktop = FakeDesktop()
        self._install(desktop)
        result = _screenshot_tools()["screenshot_gauge_preview"]("explorer")
        # 800x600 -> left 60% -> 480x600 -> center 80% -> 384x480 -> scale 0.5 -> 192x240
        self.assertEqual((result["width"], result["height"]), (192, 240))
        self.assertTrue(result["focused"])
        self.assertTrue(Path(result["path"]).is_file())


class ListWindowsTests(_ScreenshotEnvMixin):
    def test_lists_only_devdash_windows(self):
        desktop = FakeDesktop(clients=[
            dict(_EXPLORER_WINDOW),
            {"address": "0xCHROME", "title": "news - Chrome", "class": "Google-chrome", "at": [0, 0], "size": [100, 100]},
        ])
        self._install(desktop)
        result = _screenshot_tools()["screenshot_list_windows"]()
        titles = [w["title"] for w in result["windows"]]
        self.assertIn("DevDash Gauges Explorer", titles)
        self.assertNotIn("news - Chrome", titles)
        self.assertEqual(result["session_type"], "wayland")


if __name__ == "__main__":
    unittest.main()
