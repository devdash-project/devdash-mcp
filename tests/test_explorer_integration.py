"""End-to-end tests for the explorer MCP tools against a fake WebSocket server.

These exercise the real tool functions (pulled out of a real ``FastMCP``
registration) and the real ``_send_request`` path over a real socket — only
the explorer process itself is faked. The headline assertion for the
type-coercion fix is *what reaches the explorer over the wire*: a string
``"false"`` must arrive as a JSON ``false``, not the string ``"false"``.

Run from the repo root:  python -m unittest discover -s tests
"""

import unittest
from unittest import mock

from mcp.server.fastmcp import FastMCP

from src.config import Config
from src.tools import explorer
from src.tools.explorer import register_explorer_tools

try:  # works whether loaded as `tests.test_*` or as a top-level module
    from tests._fake_explorer import fake_explorer
except ImportError:  # pragma: no cover - fallback for `discover -s tests`
    from _fake_explorer import fake_explorer


def _explorer_tools() -> dict:
    """Register the explorer tools on a throwaway FastMCP and return {name: fn}."""
    mcp = FastMCP("test")
    register_explorer_tools(mcp)
    return {t.name: t.fn for t in mcp._tool_manager.list_tools()}


class _PatchedConfigMixin(unittest.TestCase):
    """Point the explorer tools at a given port for the duration of a test."""

    def _use_port(self, port: int) -> None:
        cfg = Config(explorer_ws_host="localhost", explorer_ws_port=port)
        patcher = mock.patch.object(explorer, "get_config", return_value=cfg)
        patcher.start()
        self.addCleanup(patcher.stop)


_BEZEL_METADATA = [
    {"name": "count", "type": "int"},
    {"name": "hasFormShading", "type": "bool"},
    {"name": "screwColor", "type": "color"},
    {"name": "headStyle", "type": "enum", "options": ["plain", "slot"]},
    {"name": "lightAngle", "type": "real"},
]


class SetPropertyWireTypeTests(_PatchedConfigMixin):
    def test_string_false_reaches_explorer_as_json_false(self):
        with fake_explorer(property_metadata=_BEZEL_METADATA) as fx:
            self._use_port(fx.port)
            tools = _explorer_tools()
            resp = tools["qml_explorer_set_property"]("hasFormShading", "false")
            self.assertEqual(fx.received_set[-1], ("hasFormShading", False))
            self.assertIs(fx.received_set[-1][1], False)  # a real bool, not "false"
            self.assertEqual(resp.get("coerced_value"), False)
            self.assertEqual(resp.get("original_value"), "false")

    def test_numeric_string_reaches_explorer_as_number(self):
        with fake_explorer(property_metadata=_BEZEL_METADATA) as fx:
            self._use_port(fx.port)
            tools = _explorer_tools()
            tools["qml_explorer_set_property"]("count", "8")
            self.assertEqual(fx.received_set[-1], ("count", 8))
            self.assertIsInstance(fx.received_set[-1][1], int)
            tools["qml_explorer_set_property"]("lightAngle", "-45")
            self.assertEqual(fx.received_set[-1], ("lightAngle", -45.0))

    def test_invalid_bool_string_is_rejected_and_nothing_is_sent(self):
        with fake_explorer(property_metadata=_BEZEL_METADATA) as fx:
            self._use_port(fx.port)
            tools = _explorer_tools()
            resp = tools["qml_explorer_set_property"]("hasFormShading", "yes")
            self.assertFalse(resp["success"])
            self.assertEqual(resp["declared_type"], "bool")
            self.assertEqual(fx.received_set, [])  # no setProperty reached the explorer

    def test_color_and_enum_strings_pass_through_unchanged(self):
        with fake_explorer(property_metadata=_BEZEL_METADATA) as fx:
            self._use_port(fx.port)
            tools = _explorer_tools()
            r1 = tools["qml_explorer_set_property"]("screwColor", "#ff0000")
            r2 = tools["qml_explorer_set_property"]("headStyle", "slot")
            self.assertEqual(fx.received_set, [("screwColor", "#ff0000"), ("headStyle", "slot")])
            self.assertNotIn("coerced_value", r1)
            self.assertNotIn("coerced_value", r2)

    def test_real_json_bool_passes_through_without_annotation(self):
        with fake_explorer(property_metadata=_BEZEL_METADATA) as fx:
            self._use_port(fx.port)
            tools = _explorer_tools()
            resp = tools["qml_explorer_set_property"]("hasFormShading", False)
            self.assertEqual(fx.received_set[-1], ("hasFormShading", False))
            self.assertNotIn("coerced_value", resp)

    def test_unknown_property_falls_back_to_passthrough(self):
        # No metadata at all -> declared type is None -> string is not coerced.
        with fake_explorer(property_metadata=[]) as fx:
            self._use_port(fx.port)
            tools = _explorer_tools()
            tools["qml_explorer_set_property"]("mysteryProp", "false")
            self.assertEqual(fx.received_set[-1], ("mysteryProp", "false"))


class GetStateAndPropertyTests(_PatchedConfigMixin):
    def test_get_state_round_trips(self):
        with fake_explorer(page="GaugeArc", properties={"foo": 42}, property_metadata=_BEZEL_METADATA) as fx:
            self._use_port(fx.port)
            tools = _explorer_tools()
            state = tools["qml_explorer_get_state"]()
            self.assertTrue(state["success"])
            self.assertEqual(state["data"]["page"], "GaugeArc")
            self.assertEqual(state["data"]["properties"], {"foo": 42})

    def test_get_property_hit_and_miss(self):
        with fake_explorer(properties={"foo": 7}) as fx:
            self._use_port(fx.port)
            tools = _explorer_tools()
            hit = tools["qml_explorer_get_property"]("foo")
            self.assertEqual((hit["success"], hit["value"]), (True, 7))
            miss = tools["qml_explorer_get_property"]("nope")
            self.assertFalse(miss["success"])


class NavigateTests(_PatchedConfigMixin):
    def _patch_pages(self, pages, discovered=True):
        patcher = mock.patch.object(
            explorer, "_discover_explorer_pages", return_value=(pages, discovered)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_navigate_to_known_page_succeeds_and_switches(self):
        with fake_explorer(page="Welcome") as fx:
            self._use_port(fx.port)
            self._patch_pages(["Welcome", "GaugeArc", "BezelScrews"])
            tools = _explorer_tools()
            resp = tools["qml_explorer_navigate"]("GaugeArc")
            self.assertTrue(resp["success"])
            self.assertEqual(resp["page"], "GaugeArc")
            self.assertEqual(fx.received_navigate, ["GaugeArc"])
            self.assertEqual(fx.page, "GaugeArc")

    def test_unknown_page_is_rejected_without_contacting_explorer(self):
        with fake_explorer(page="Welcome") as fx:
            self._use_port(fx.port)
            self._patch_pages(["Welcome", "GaugeArc"])
            tools = _explorer_tools()
            resp = tools["qml_explorer_navigate"]("DoesNotExist")
            self.assertFalse(resp["success"])
            self.assertIn("GaugeArc", resp["error"])
            self.assertEqual(fx.received_navigate, [])

    def test_navigation_that_does_not_take_is_reported_as_failure(self):
        # Page is a known file, but the explorer doesn't switch to it.
        with fake_explorer(page="Welcome", navigate_switches=False) as fx:
            self._use_port(fx.port)
            self._patch_pages(["Welcome", "GaugeArc"])
            tools = _explorer_tools()
            resp = tools["qml_explorer_navigate"]("GaugeArc")
            self.assertFalse(resp["success"])
            self.assertEqual(resp["current_page"], "Welcome")
            self.assertEqual(fx.received_navigate, ["GaugeArc"])  # request was sent


class DiscoverPagesTests(unittest.TestCase):
    def test_discovers_pages_from_a_pages_directory(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            pages = Path(tmp) / "explorer" / "qml" / "pages"
            pages.mkdir(parents=True)
            for fn in ("WelcomePage.qml", "GaugeArcPage.qml", "BezelScrewsPage.qml", "NotAPage.txt", "Page.qml"):
                (pages / fn).write_text("// stub\n")
            cfg = Config(qml_gauges_path=tmp)
            with mock.patch.object(explorer, "get_config", return_value=cfg):
                names, discovered = explorer._discover_explorer_pages()
        self.assertTrue(discovered)
        self.assertEqual(names, ["BezelScrews", "GaugeArc", "Welcome"])

    def test_falls_back_when_path_unset(self):
        with mock.patch.object(explorer, "get_config", return_value=Config(qml_gauges_path="")):
            names, discovered = explorer._discover_explorer_pages()
        self.assertFalse(discovered)
        self.assertEqual(names, explorer._FALLBACK_EXPLORER_PAGES)


class LaunchEnvTests(unittest.TestCase):
    """qml_explorer_launch forces Qt's log output to the captured stderr."""

    def test_launch_sets_qt_force_stderr_logging(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_home:
            cfg = Config(qml_gauges_path="/nonexistent/qml-gauges")
            fake_proc = mock.Mock()
            fake_proc.poll.return_value = None
            fake_proc.pid = 99999

            not_running = subprocess_result(returncode=1, stdout="")
            with mock.patch.object(explorer, "get_config", return_value=cfg), \
                 mock.patch.object(explorer.subprocess, "run", return_value=not_running), \
                 mock.patch.object(explorer.os.path, "exists", return_value=True), \
                 mock.patch.object(explorer.tempfile, "gettempdir", return_value=tmp_home), \
                 mock.patch.object(explorer.subprocess, "Popen", return_value=fake_proc) as popen, \
                 mock.patch("time.sleep"), \
                 mock.patch.dict(explorer._LAUNCHED_LOG_PATHS, {}, clear=True):
                tools = _explorer_tools()
                resp = tools["qml_explorer_launch"]()

        self.assertTrue(resp["success"])
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env.get("QT_FORCE_STDERR_LOGGING"), "1")
        # The captured stream is the explorer's stdout, with stderr folded in.
        self.assertIs(popen.call_args.kwargs["stderr"], explorer.subprocess.STDOUT)


def subprocess_result(*, returncode: int, stdout: str):
    """A minimal stand-in for subprocess.CompletedProcess (only fields we read)."""
    import subprocess as _sp

    return _sp.CompletedProcess(args=["pgrep"], returncode=returncode, stdout=stdout, stderr="")


if __name__ == "__main__":
    unittest.main()
