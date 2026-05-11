"""Regression tests for screenshot focus-before-capture.

These guard the behaviour where the target window is focused before the
compositor capture runs (so the image isn't distorted by effects applied to
unfocused windows — translucency, blur xray, un-composited workspaces) and
prior focus is restored afterwards.

Run from the repo root:  python -m unittest discover -s tests
"""

import unittest
from unittest import mock

from src.tools import screenshot


class HyprlandFocusHelperTests(unittest.TestCase):
    def test_focus_dispatches_focuswindow_by_address(self):
        calls = []

        def fake_run(cmd):
            calls.append(cmd)
            return ("", 0)

        with mock.patch.object(screenshot, "_run_command", fake_run):
            ok = screenshot._hyprland_focus("0xdeadbeef")
        self.assertTrue(ok)
        self.assertEqual(
            calls, [["hyprctl", "dispatch", "focuswindow", "address:0xdeadbeef"]]
        )

    def test_focus_empty_address_is_noop(self):
        with mock.patch.object(screenshot, "_run_command") as run:
            self.assertFalse(screenshot._hyprland_focus(""))
        run.assert_not_called()

    def test_focus_reports_failure_on_nonzero_exit(self):
        with mock.patch.object(screenshot, "_run_command", return_value=("err", 1)):
            self.assertFalse(screenshot._hyprland_focus("0xabc"))

    def test_active_address_parses_json(self):
        with mock.patch.object(
            screenshot,
            "_run_command",
            return_value=('{"address": "0xabc", "title": "x"}', 0),
        ):
            self.assertEqual(screenshot._hyprland_active_address(), "0xabc")

    def test_active_address_handles_no_focused_window(self):
        for stdout in ("", "not json", "null"):
            with mock.patch.object(screenshot, "_run_command", return_value=(stdout, 0)):
                self.assertIsNone(screenshot._hyprland_active_address())


class FocusTargetWindowTests(unittest.TestCase):
    def test_wayland_path_records_prior_and_focuses_target(self):
        with mock.patch.object(screenshot, "SESSION_TYPE", "wayland"), mock.patch.object(
            screenshot, "_hyprland_active_address", return_value="0xPRIOR"
        ), mock.patch.object(
            screenshot, "_hyprland_focus", return_value=True
        ) as focus:
            ok, prior = screenshot._focus_target_window({"id": "0xTARGET"})
        self.assertTrue(ok)
        self.assertEqual(prior, "0xPRIOR")
        focus.assert_called_once_with("0xTARGET")

    def test_restore_focus_noop_on_none(self):
        with mock.patch.object(screenshot, "_hyprland_focus") as hypr, mock.patch.object(
            screenshot, "_x11_focus"
        ) as x11:
            screenshot._restore_focus(None)
        hypr.assert_not_called()
        x11.assert_not_called()


class CaptureFocusedTests(unittest.TestCase):
    def test_focuses_then_settles_then_captures_then_restores(self):
        events = []
        win = {"id": "0xTARGET"}
        with mock.patch.object(
            screenshot,
            "_focus_target_window",
            side_effect=lambda w: (events.append(("focus", w["id"])), (True, "0xPRIOR"))[1],
        ), mock.patch.object(screenshot, "time") as fake_time, mock.patch.object(
            screenshot,
            "_capture_window_unified",
            side_effect=lambda w: (events.append(("capture", w["id"])), b"PNGDATA")[1],
        ), mock.patch.object(
            screenshot,
            "_restore_focus",
            side_effect=lambda t: events.append(("restore", t)),
        ):
            fake_time.sleep.side_effect = lambda s: events.append(("sleep", s))
            data, focused = screenshot._capture_focused(win)
        self.assertEqual(data, b"PNGDATA")
        self.assertTrue(focused)
        self.assertEqual(
            events,
            [
                ("focus", "0xTARGET"),
                ("sleep", screenshot._FOCUS_SETTLE_SECONDS),
                ("capture", "0xTARGET"),
                ("restore", "0xPRIOR"),
            ],
        )

    def test_no_restore_when_target_was_already_focused(self):
        restored = []
        win = {"id": "0xSAME"}
        with mock.patch.object(
            screenshot, "_focus_target_window", return_value=(True, "0xSAME")
        ), mock.patch.object(screenshot, "time"), mock.patch.object(
            screenshot, "_capture_window_unified", return_value=b"X"
        ), mock.patch.object(
            screenshot, "_restore_focus", side_effect=restored.append
        ):
            screenshot._capture_focused(win)
        self.assertEqual(restored, [])

    def test_skips_settle_and_restore_when_focus_fails(self):
        restored = []
        win = {"id": "0xT"}
        with mock.patch.object(
            screenshot, "_focus_target_window", return_value=(False, None)
        ), mock.patch.object(screenshot, "time") as fake_time, mock.patch.object(
            screenshot, "_capture_window_unified", return_value=b"X"
        ), mock.patch.object(
            screenshot, "_restore_focus", side_effect=restored.append
        ):
            data, focused = screenshot._capture_focused(win)
        fake_time.sleep.assert_not_called()
        self.assertEqual(restored, [])
        self.assertFalse(focused)
        self.assertEqual(data, b"X")

    def test_returns_none_data_when_capture_fails(self):
        win = {"id": "0xT"}
        with mock.patch.object(
            screenshot, "_focus_target_window", return_value=(True, "0xP")
        ), mock.patch.object(screenshot, "time"), mock.patch.object(
            screenshot, "_capture_window_unified", return_value=None
        ), mock.patch.object(screenshot, "_restore_focus"):
            data, focused = screenshot._capture_focused(win)
        self.assertIsNone(data)
        self.assertTrue(focused)


if __name__ == "__main__":
    unittest.main()
