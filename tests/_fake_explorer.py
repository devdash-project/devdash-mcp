"""In-process stand-in for the qml-gauges explorer's WebSocket state server.

Lets the explorer MCP tools be exercised end-to-end (real `_send_request`,
real JSON over a real socket) without a running explorer. Implements the
subset of the protocol the tools use: ``ping``, ``getState``, ``getProperty``,
``setProperty``, ``listProperties``, ``navigate``.

Differences from the real server, on purpose:

* It does **not** broadcast a separate ``propertyChanged`` / ``pageChanged``
  event message before the response. The real explorer does (and the tools
  tolerate it); keeping the fake's replies to one message each keeps test
  assertions simple.
* ``setProperty`` / ``navigate`` record exactly what arrived over the wire
  (``received_set`` / ``received_navigate``) — the whole point of the
  type-coercion fix is *what reaches the explorer*, so tests assert on that.
* ``navigate`` can be told not to switch (``navigate_switches=False``) to
  simulate a page that exists as a file but isn't wired into ``Main.qml``.
* ``pages=[...]`` makes ``getState``'s ``data`` carry a ``pages`` array (the
  proposed qml-gauges enhancement); leaving it ``None`` mimics current builds.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
from typing import Any

from websockets.sync.server import serve

# The websockets server logs "connection open/closed" at INFO; keep test output clean.
logging.getLogger("websockets.server").setLevel(logging.WARNING)


class FakeExplorer:
    def __init__(
        self,
        *,
        page: str = "Welcome",
        properties: dict[str, Any] | None = None,
        property_metadata: list[dict[str, Any]] | None = None,
        navigate_switches: bool = True,
        pages: list[str] | None = None,
    ) -> None:
        self.page = page
        self.properties: dict[str, Any] = dict(properties or {})
        self.property_metadata: list[dict[str, Any]] = list(property_metadata or [])
        self.navigate_switches = navigate_switches
        # When not None, getState's data carries a "pages" array (the future
        # qml-gauges-side enhancement); None mimics current explorer builds.
        self.pages = pages

        # Wire-level audit trails.
        self.received_set: list[tuple[Any, Any]] = []       # (name, value) as received
        self.received_navigate: list[Any] = []              # page as received
        self.requests: list[dict[str, Any]] = []            # every parsed request

        self._server = None
        self._thread: threading.Thread | None = None
        self.port: int | None = None

    # -- protocol ----------------------------------------------------------

    def _dispatch(self, req: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(req)
        action = req.get("action")

        if action == "ping":
            return {"success": True, "data": {"pong": True, "listening": True}}

        if action == "getState":
            data: dict[str, Any] = {
                "page": self.page,
                "pageTitle": self.page,
                "properties": dict(self.properties),
                "propertyMetadata": list(self.property_metadata),
            }
            if self.pages is not None:
                data["pages"] = list(self.pages)
            return {"success": True, "data": data}

        if action == "getProperty":
            name = req.get("name")
            if name in self.properties:
                return {"success": True, "data": {"name": name, "value": self.properties[name]}}
            return {"success": False, "error": f"Property '{name}' not found"}

        if action == "setProperty":
            name, value = req.get("name"), req.get("value")
            self.received_set.append((name, value))
            self.properties[name] = value
            return {"success": True, "data": {"name": name, "value": value}}

        if action == "listProperties":
            return {"success": True, "data": list(self.property_metadata)}

        if action == "navigate":
            page = req.get("page")
            self.received_navigate.append(page)
            if self.navigate_switches:
                self.page = page
            return {"success": True, "data": {"page": page}}

        return {"success": False, "error": f"unknown action {action!r}"}

    def _handle(self, ws) -> None:
        for message in ws:
            try:
                req = json.loads(message)
            except json.JSONDecodeError:
                ws.send(json.dumps({"success": False, "error": "JSON parse error"}))
                continue
            if not isinstance(req, dict):
                ws.send(json.dumps({"success": False, "error": "Request must be an object"}))
                continue
            ws.send(json.dumps(self._dispatch(req)))

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._server = serve(self._handle, "localhost", 0)
        self.port = self._server.socket.getsockname()[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=2)


@contextlib.contextmanager
def fake_explorer(**kwargs: Any):
    """Context manager yielding a started :class:`FakeExplorer`."""
    fx = FakeExplorer(**kwargs)
    fx.start()
    try:
        yield fx
    finally:
        fx.stop()
