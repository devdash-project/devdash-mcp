"""QML Gauges Explorer tools - WebSocket communication with the explorer app.

These tools interact with the QML Gauges Explorer from the qml-gauges repository.
Includes tools for building, launching, and managing the explorer process,
as well as property inspection and modification via WebSocket.
"""

import json
import math
import os
import re
import signal
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

try:
    from websockets.sync.client import connect as ws_connect

    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

from ..config import get_config


# Process-global state for the explorer instance launched by qml_explorer_launch.
# Keyed by PID. When the MCP server restarts, this is empty -> logs are
# unavailable for any pre-existing explorer (correct: we have no log file).
_LAUNCHED_LOG_PATHS: dict[int, Path] = {}


# QSG_RENDER_TIMING lines look like (Qt 6.x, threaded renderloop):
#   qt.scenegraph.time.renderloop: [window 0x...][gui thread] syncAndRender:
#     frame rendered in <N>ms, polish=..., sync=..., render=..., swap=...,
#     perWindowFrameDelta=<D>
# Integer-ms only. ``perWindowFrameDelta`` is the wall-clock gap between
# successive frames for the same window (i.e. 1000 / FPS).
_QSG_FRAME_LINE = re.compile(
    r"qt\.scenegraph\.time\.renderloop:.*?frame rendered in (\d+)ms"
    r".*?perWindowFrameDelta=(\d+)"
)


def _parse_qsg_render_timing(text: str) -> tuple[list[float], list[float]]:
    """Extract (render_cost_ms, frame_interval_ms) from a QSG_RENDER_TIMING block.

    Both values come from the threaded renderloop's ``syncAndRender``
    summary line; integer-millisecond precision. Frames with
    ``perWindowFrameDelta=0`` are dropped from the interval series — those
    are first-frame / coalesced-paint artefacts, not real frame pacing.
    """
    render_costs: list[float] = []
    intervals: list[float] = []
    for line in text.splitlines():
        match = _QSG_FRAME_LINE.search(line)
        if not match:
            continue
        render_costs.append(float(match.group(1)))
        delta = float(match.group(2))
        if delta > 0:
            intervals.append(delta)
    return render_costs, intervals


def _percentile(samples: list[float], pct: float) -> float:
    """Inclusive nearest-rank percentile. Empty list -> raises."""
    if not samples:
        raise ValueError("percentile of empty sample")
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(0, min(len(ordered) - 1, int(round(pct / 100.0 * (len(ordered) - 1)))))
    return ordered[rank]


def _send_request(request: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    """Send a request to the explorer WebSocket server."""
    if not WEBSOCKETS_AVAILABLE:
        return {
            "success": False,
            "error": "websockets library not installed. Run: pip install websockets",
        }

    config = get_config()
    url = config.explorer_ws_url

    try:
        with ws_connect(url, open_timeout=timeout, close_timeout=timeout) as ws:
            ws.send(json.dumps(request))
            response = ws.recv(timeout=timeout)
            return json.loads(response)
    except ConnectionRefusedError:
        return {
            "success": False,
            "error": f"Cannot connect to QML Gauges Explorer at {url}. "
            "Launch it with: cd qml-gauges && ./build/explorer/qml-gauges-explorer",
        }
    except TimeoutError:
        return {"success": False, "error": f"Timeout connecting to explorer at {url}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# --- Property value coercion -------------------------------------------------
#
# MCP tool arguments arrive as JSON, but a caller sometimes supplies a *string*
# ("false", "12") where the QML property expects a real bool / number. The QML
# side does `target[name] = value` through the JS engine, where a non-empty
# string is truthy — so a bare "false" string would set a bool property to
# *true*. We coerce string inputs to the property's declared type here, at the
# server boundary, before they reach the explorer. Non-string JSON values
# (real bools, numbers, lists) already carry their type across the wire and are
# passed through untouched.

# Allowed string spellings when coercing to a bool property (case-insensitive,
# surrounding whitespace ignored). Anything else is an explicit error rather
# than a silent truthy/falsy guess.
_BOOL_STRINGS_TRUE = frozenset({"true"})
_BOOL_STRINGS_FALSE = frozenset({"false"})


class PropertyCoercionError(ValueError):
    """A string value couldn't be coerced to a property's declared type."""


def _coerce_value(value: Any, declared_type: str | None) -> Any:
    """Coerce an MCP-supplied value to a QML property's declared type.

    Non-string values pass through unchanged — JSON already preserved bool /
    int / float / list / dict / None across the wire. String values are
    coerced based on ``declared_type`` (from the page's property metadata):

      * ``bool``  -> ``"true"`` / ``"false"`` (case-insensitive, surrounding
        whitespace ignored). Any other string raises ``PropertyCoercionError``
        instead of being silently treated as truthy.
      * ``int``   -> parsed integer; non-numeric or non-integral strings raise.
      * ``real``  -> parsed float; non-numeric / NaN / infinity raise.
      * ``color`` / ``string`` / ``enum`` / unknown / ``None`` -> left as the
        original string (Qt parses colour names and hex; enum values are
        strings; and when the type is unknown we don't guess).

    Raises:
        PropertyCoercionError: the string can't represent the declared type.
    """
    if not isinstance(value, str):
        # bool is a subclass of int in Python, but json.dumps already
        # serialised it correctly; numbers and containers likewise.
        return value

    kind = (declared_type or "").strip().lower()

    if kind == "bool":
        token = value.strip().lower()
        if token in _BOOL_STRINGS_TRUE:
            return True
        if token in _BOOL_STRINGS_FALSE:
            return False
        allowed = sorted(_BOOL_STRINGS_TRUE | _BOOL_STRINGS_FALSE)
        raise PropertyCoercionError(
            f"cannot set a bool property from {value!r}; "
            f"expected one of {allowed} (case-insensitive)"
        )

    if kind in ("int", "real"):
        token = value.strip()
        try:
            number = float(token)
        except ValueError:
            raise PropertyCoercionError(
                f"cannot set a {kind} property from {value!r} (not a number)"
            ) from None
        if not math.isfinite(number):
            raise PropertyCoercionError(
                f"cannot set a {kind} property from {value!r} (NaN/infinity)"
            )
        if kind == "int":
            if number != int(number):
                raise PropertyCoercionError(
                    f"cannot set an int property from {value!r} (not an integer)"
                )
            return int(number)
        return number

    # color / string / enum / unknown / None: pass the string through unchanged.
    return value


def _property_type_from_metadata(name: str) -> str | None:
    """Return the declared type of ``name`` on the current page, or ``None``.

    Reads the page's property metadata (the same array the explorer's
    PropertyPanel is built from, exposed via ``getState``). Returns ``None`` if
    the explorer is unreachable or the property isn't described there — in that
    case the caller falls back to passing the value through without coercion.
    """
    state = _send_request({"action": "getState"})
    if not (isinstance(state, dict) and state.get("success")):
        return None
    data = state.get("data") or {}
    for entry in data.get("propertyMetadata") or []:
        if isinstance(entry, dict) and entry.get("name") == name:
            declared = entry.get("type")
            return declared if isinstance(declared, str) else None
    return None


# --- Explorer page discovery -------------------------------------------------
#
# The explorer's component pages are one ``<Name>Page.qml`` file each under
# explorer/qml/pages/ (so ``BezelScrewsPage.qml`` is page "BezelScrews"). The
# list is discovered at call time so adding a page never requires restarting
# the MCP server. Sources, in priority order:
#
#   1. The local qml-gauges checkout's ``explorer/qml/pages/*Page.qml`` files
#      (when ``DEVDASH_QML_GAUGES_PATH`` points at a checkout).
#   2. The running explorer's ``getState`` response, *if* it carries a
#      ``data.pages`` array. Current explorer builds don't — see the
#      qml-gauges-side recommendation to add one (it's the only source that
#      works when the MCP server talks to a remote explorer with no local
#      source tree). This path is a no-op until that lands.
#   3. The hardcoded :data:`_FALLBACK_EXPLORER_PAGES` below — last resort only.
#
# TODO(qml-gauges): have StateServer::handleRequest's "getState" include
# ``data["pages"]`` (the keys of Main.qml's pageIndexMap). Then this module
# needs no qml-gauges-side coupling at all, even without a local checkout.
_FALLBACK_EXPLORER_PAGES = [
    "Welcome",
    "BezelScrews",
    "GaugeArc",
    "GaugeBezel",
    "GaugeCenterCap",
    "GaugeFace",
    "GaugeTick",
    "GaugeTickLabel",
    "Bezel3D",
    "CenterCap3D",
    "DigitalReadout",
    "GaugeNeedle",
    "GaugeTickRing",
    "GaugeValueArc",
    "GaugeZoneArc",
    "RollingDigitReadout",
    "RadialGauge",
    "RadialGauge3D",
    "IndustrialGauge",
]


def _pages_from_state() -> list[str]:
    """Return the page list the running explorer advertises, or ``[]``.

    Reads ``getState``'s ``data.pages`` array. Current explorer builds don't
    populate it (see the module-level TODO), so this returns ``[]`` for them —
    callers fall through to the next discovery source.
    """
    state = _send_request({"action": "getState"})
    if not (isinstance(state, dict) and state.get("success")):
        return []
    pages = (state.get("data") or {}).get("pages")
    if isinstance(pages, list) and pages and all(isinstance(p, str) and p for p in pages):
        return sorted(pages)
    return []


def _discover_explorer_pages() -> tuple[list[str], bool]:
    """Return ``(page_names, discovered)``.

    Tries, in order: (1) ``<qml_gauges_path>/explorer/qml/pages/*Page.qml``
    stems (``Page`` suffix stripped), (2) the running explorer's ``getState``
    ``data.pages`` array, (3) the hardcoded :data:`_FALLBACK_EXPLORER_PAGES`.
    ``discovered`` is ``True`` for (1) and (2); ``False`` only when the
    hardcoded fallback is returned.
    """
    base = get_config().qml_gauges_path
    if base:
        pages_dir = Path(base) / "explorer" / "qml" / "pages"
        try:
            names = sorted(
                stem[:-4]
                for stem in (p.stem for p in pages_dir.glob("*Page.qml"))
                if stem.endswith("Page") and stem[:-4]
            )
        except OSError:
            names = []
        if names:
            return names, True

    from_state = _pages_from_state()
    if from_state:
        return from_state, True

    return list(_FALLBACK_EXPLORER_PAGES), False


def register_explorer_tools(mcp: FastMCP) -> None:
    """Register QML Gauges Explorer tools with the MCP server."""

    @mcp.tool()
    def qml_explorer_status() -> dict[str, Any]:
        """Check if the QML Gauges Explorer is running (qml-gauges repo).

        Liveness is verified actively every call — ``pgrep`` for the explorer
        binary plus a real WebSocket round-trip — so a stale instance left over
        from a *previous* MCP session (one this server never launched) is still
        reported as ``running: True``. In that case ``managed_by_session`` is
        ``False``: the explorer is alive but this server has no record of it, so
        ``qml_explorer_launch`` will just attach to it (no log capture) and
        ``qml_explorer_logs_get`` won't work. Run ``qml_explorer_kill`` then
        ``qml_explorer_launch`` to bring it under this session's management.

        Returns:
            {
              "running": bool,                 # any explorer process alive
              "pids": [str, ...],               # all explorer PIDs (pgrep)
              "websocket_connected": bool,      # WS state server answered
              "managed_by_session": bool|None,  # True iff a running PID was
                                                # launched by THIS server;
                                                # None when nothing is running
              "session_pids": [int, ...],       # running PIDs this server launched
              "foreign_pids": [int, ...],       # running PIDs it did not
              "hint": str,                      # present only when action is useful
            }
        """
        result: dict[str, Any] = {
            "running": False,
            "pids": [],
            "websocket_connected": False,
            "managed_by_session": None,
            "session_pids": [],
            "foreign_pids": [],
        }

        # Check for running processes
        try:
            pgrep_result = subprocess.run(
                ["pgrep", "-f", "qml-gauges-explorer"],
                capture_output=True,
                text=True,
            )
            if pgrep_result.returncode == 0:
                result["running"] = True
                result["pids"] = [p for p in pgrep_result.stdout.strip().split("\n") if p]
        except Exception:
            pass

        # Partition running PIDs into those this session launched and the rest.
        running_pids_int = {int(p) for p in result["pids"] if p.isdigit()}
        session_known = set(_LAUNCHED_LOG_PATHS.keys())
        session_pids = sorted(running_pids_int & session_known)
        foreign_pids = sorted(running_pids_int - session_known)
        result["session_pids"] = session_pids
        result["foreign_pids"] = foreign_pids
        if result["running"]:
            result["managed_by_session"] = bool(session_pids)
            if foreign_pids and not session_pids:
                result["hint"] = (
                    "An explorer is running that this MCP session did not launch "
                    "(likely a leftover from a prior session). qml_explorer_logs_get "
                    "won't have its output. Run qml_explorer_kill then "
                    "qml_explorer_launch to manage it from this session."
                )

        # Check WebSocket connectivity
        if WEBSOCKETS_AVAILABLE:
            config = get_config()
            try:
                with ws_connect(config.explorer_ws_url, open_timeout=1.0, close_timeout=1.0) as ws:
                    ws.send(json.dumps({"action": "getState"}))
                    ws.recv(timeout=1.0)
                    result["websocket_connected"] = True
            except Exception:
                pass

        return result

    @mcp.tool()
    def qml_explorer_get_state() -> dict[str, Any]:
        """Get the current state of the QML Gauges Explorer (qml-gauges repo).

        Returns the current page, available properties, and their values.
        Requires the explorer to be running.

        Returns:
            Current explorer state including page and property values
        """
        return _send_request({"action": "getState"})

    @mcp.tool()
    def qml_explorer_navigate(page: str) -> dict[str, Any]:
        """Navigate the QML Gauges Explorer to a component page (qml-gauges repo).

        Valid page names are discovered at call time from the explorer's
        ``explorer/qml/pages/*Page.qml`` files (page "Foo" ↔ ``FooPage.qml``),
        so a newly added page works without restarting the MCP server. An
        unrecognised name is rejected, with the current list included in the
        error. After the navigate request, the new page is confirmed via
        ``getState``; if the explorer didn't actually switch — e.g. the page
        file exists but isn't wired into ``Main.qml``'s ``pageIndexMap`` — an
        error is returned rather than a false success.

        Args:
            page: Component page name (e.g. 'RadialGauge', 'BezelScrews').

        Returns:
            On success: ``{"success": True, "page": <page>, "pages": [...]}``.
            On failure: ``{"success": False, "error": <reason>, "pages": [...]}``
            (and ``"current_page"`` when the explorer stayed on a different page).
        """
        pages, discovered = _discover_explorer_pages()
        if page not in pages:
            qualifier = (
                "Available" if discovered
                else "Known (fallback list — explorer source tree not found)"
            )
            return {
                "success": False,
                "error": f"Unknown page '{page}'. {qualifier} pages: {', '.join(pages)}",
                "pages": pages,
            }

        nav = _send_request({"action": "navigate", "page": page})
        # The immediate reply may be the navigate ack OR the broadcast
        # "pageChanged" event (the explorer broadcasts synchronously, before it
        # sends the response) — either is fine. Only an explicit success:false
        # is a hard failure (bad request, or the WS is unreachable).
        if isinstance(nav, dict) and nav.get("success") is False:
            return nav

        state = _send_request({"action": "getState"})
        current = None
        if isinstance(state, dict) and state.get("success"):
            current = (state.get("data") or {}).get("page")
        if current is not None and current != page:
            return {
                "success": False,
                "error": (
                    f"Navigation request was accepted but the explorer is still "
                    f"on '{current}' — '{page}' may not be registered in the "
                    f"explorer's Main.qml pageIndexMap."
                ),
                "pages": pages,
                "current_page": current,
            }
        return {"success": True, "page": page, "pages": pages}

    @mcp.tool()
    def qml_explorer_get_property(name: str) -> dict[str, Any]:
        """Get the current resolved value of a property, including when bound.

        Falls back to the page state if the explorer's getProperty handler
        reports "not found" — this happens for properties bound to animations
        or computed expressions, which the explorer's getProperty API can't
        introspect but which still appear (already resolved) in getState.

        Args:
            name: Property name (e.g., 'tickShape', 'color', 'hasGlow')

        Returns:
            On success:
                {
                  "success": True,
                  "value": <current resolved value>,
                  "is_bound": True | False,
                  "binding_source": str | None,
                }
            On failure:
                {"success": False, "error": <reason>}

        NOTE on is_bound: `is_bound: False` does NOT guarantee the property is
        not bound — it only means the value was resolved via the explorer's
        direct getProperty lookup rather than the getState fallback. Since the
        explorer's PropertyPanel publishes resolved values to the state server
        for *all* panel properties (bound or not), a theme-bound property such
        as `faceColor` will return `is_bound: False`. Treat the flag as "value
        came from direct lookup" rather than "property has no binding."

        BREAKING CHANGE (devdash-mcp 0.3.0): previous releases returned the raw
        response from the explorer's WebSocket protocol, which had a different
        shape and could not resolve bound properties. See CHANGELOG.md.
        """
        primary = _send_request({"action": "getProperty", "name": name})

        if primary.get("success") and "data" in primary:
            data = primary["data"]
            return {
                "success": True,
                "value": data.get("value"),
                "is_bound": False,
                "binding_source": None,
            }

        # Fallback: scan the page state for a resolved value (handles
        # properties bound to animations / expressions).
        state = _send_request({"action": "getState"})
        if state.get("success") and "data" in state:
            props = state["data"].get("properties") or {}
            if name in props:
                return {
                    "success": True,
                    "value": props[name],
                    "is_bound": True,
                    "binding_source": None,
                }

        return {
            "success": False,
            "error": primary.get("error")
            or f"Property '{name}' not found on current page",
        }

    def _resolve_current_value(name: str) -> tuple[bool, Any, bool]:
        """Return (found, value, was_bound). Mirrors qml_explorer_get_property logic."""
        primary = _send_request({"action": "getProperty", "name": name})
        if primary.get("success") and "data" in primary:
            return True, primary["data"].get("value"), False
        state = _send_request({"action": "getState"})
        if state.get("success") and "data" in state:
            props = state["data"].get("properties") or {}
            if name in props:
                return True, props[name], True
        return False, None, False

    @mcp.tool()
    def qml_explorer_freeze_property(property_name: str) -> dict[str, Any]:
        """Capture a property's current resolved value and re-set it as a plain value.

        Useful for verification workflows where a page binds properties to an
        animation (e.g. GaugeTick.angle). Reading the resolved value and
        writing it back breaks the binding, pinning the property until
        explicitly changed.

        Args:
            property_name: Property to freeze.

        Returns:
            {
              "success": True,
              "property": <name>,
              "frozen_value": <value that was reapplied>,
              "was_bound": True | False,    # True if value came from page state
                                            # rather than the direct getProperty path
            }
            or {"success": False, "error": ...} if the property couldn't be
            resolved (typically a never-set, animation-bound property whose
            current value the explorer doesn't expose).
        """
        found, value, was_bound = _resolve_current_value(property_name)
        if not found:
            return {
                "success": False,
                "error": (
                    f"Property '{property_name}' is not resolvable from the MCP layer. "
                    "If it's bound to an animation and has never been set, set it once "
                    "to any in-range value first to bring it into the page state."
                ),
            }
        write = _send_request(
            {"action": "setProperty", "name": property_name, "value": value}
        )
        if not write.get("event") and not write.get("success", False):
            return {
                "success": False,
                "error": write.get("error", "setProperty failed"),
            }
        return {
            "success": True,
            "property": property_name,
            "frozen_value": value,
            "was_bound": was_bound,
        }

    @mcp.tool()
    def qml_explorer_freeze_all_properties() -> dict[str, Any]:
        """Freeze every resolvable property on the current page.

        Iterates the current page's propertyMetadata, resolves each property's
        current value, and re-sets it to break any active binding. Returns the
        frozen value for every property that could be resolved, and a list of
        names that could not.

        Returns:
            {
              "success": True,
              "page": <page name>,
              "frozen": {<property_name>: <frozen_value>, ...},
              "skipped": [<property_name>, ...],   # not resolvable from MCP
            }
        """
        state = _send_request({"action": "getState"})
        if not (state.get("success") and "data" in state):
            return {
                "success": False,
                "error": state.get("error", "getState failed"),
            }

        data = state["data"]
        metadata = data.get("propertyMetadata") or []
        frozen: dict[str, Any] = {}
        skipped: list[str] = []
        for entry in metadata:
            name = entry.get("name")
            if not name:
                continue
            found, value, _was_bound = _resolve_current_value(name)
            if not found:
                skipped.append(name)
                continue
            _send_request({"action": "setProperty", "name": name, "value": value})
            frozen[name] = value

        return {
            "success": True,
            "page": data.get("page"),
            "frozen": frozen,
            "skipped": skipped,
        }

    @mcp.tool()
    def qml_explorer_reset_property(name: str) -> dict[str, Any]:
        """Restore a property's QML binding after it was pinned to a plain value.

        ``qml_explorer_set_property`` (and ``qml_explorer_freeze_property`` /
        ``qml_explorer_freeze_all_properties``, which use it) detach whatever
        binding a property had — a theme-token expression, an animation, a
        computed expression — by assigning it a literal. This asks the explorer
        to re-establish that binding, so the property tracks it again. It's the
        clean "unfreeze" that previously meant restarting the explorer.

        Requires explorer support for the ``resetProperty`` WebSocket action.
        Builds without it (the explorer's StateServer has no such handler yet —
        see the qml-gauges-side recommendation) return an explicit error and
        change nothing; once that handler lands this tool works unchanged.

        Args:
            name: Property to rebind.

        Returns:
            On success: ``{"success": True, "property": <name>, ...}`` (plus
            any ``data`` the explorer returned).
            On failure: ``{"success": False, "error": <reason>,
            "explorer_support": bool}`` — ``explorer_support`` is ``False`` when
            this explorer build doesn't implement the action.
        """
        if not name:
            return {"success": False, "error": "Missing 'name'", "explorer_support": True}
        resp = _send_request({"action": "resetProperty", "name": name})
        if not isinstance(resp, dict):
            return {"success": False, "error": "Malformed response from explorer",
                    "explorer_support": True}
        # Success path tolerates the explorer's broadcast-then-respond ordering:
        # a leading "propertyChanged" event message counts as success too.
        if resp.get("success") or resp.get("event"):
            out: dict[str, Any] = {"success": True, "property": name}
            if "data" in resp:
                out["data"] = resp["data"]
            return out
        err = resp.get("error") or "resetProperty failed"
        if "nknown action" in err:  # "Unknown action: 'resetProperty'"
            return {
                "success": False,
                "explorer_support": False,
                "error": (
                    "This explorer build doesn't implement the 'resetProperty' "
                    "action. It needs a qml-gauges-side change: add a "
                    "'resetProperty' branch to StateServer::handleRequest that "
                    "emits resetPropertyRequested(name), and have the explorer "
                    "re-establish that editor's binding (e.g. PropertyPanel "
                    "re-applies the metadata default via Qt.binding, or the "
                    "page re-runs its initial binding for that property). Until "
                    "then, restart the explorer to clear pinned properties."
                ),
            }
        return {"success": False, "explorer_support": True, "error": err}

    @mcp.tool()
    def qml_explorer_set_property(name: str, value: Any) -> dict[str, Any]:
        """Set a property value on the current component in the QML Gauges Explorer (qml-gauges repo).

        Value typing: prefer JSON values of the property's natural type —
        ``true`` / ``false`` for ``bool`` properties, numbers for ``real`` /
        ``int`` properties, strings for ``color`` / ``string`` / ``enum``
        properties. As a convenience, string forms are also accepted and
        coerced to the property's declared type (looked up from the page's
        metadata): ``"true"`` / ``"false"`` (case-insensitive) become booleans
        and numeric strings become numbers. A string that can't represent the
        declared type — e.g. ``"yes"`` for a ``bool`` property, or ``"x"`` for
        a ``real`` property — is rejected with an error rather than being
        silently miscoerced. (Historically a bare ``"false"`` was truthy on the
        QML side and would set a ``bool`` property to *true*.)

        Args:
            name: Property name (e.g., 'tickShape', 'color', 'hasGlow').
            value: New value. JSON bool / number / string; string forms of
                   booleans and numbers are coerced to the property's declared
                   type.

        Returns:
            On success: the explorer's response. When a string argument was
            coerced to another type, the response also carries
            ``coerced_value`` (the value actually sent) and ``original_value``.
            On a coercion failure: ``{"success": False, "error": <reason>,
            "declared_type": <type or None>}`` — nothing is sent to the
            explorer.
        """
        declared_type = _property_type_from_metadata(name)
        try:
            coerced = _coerce_value(value, declared_type)
        except PropertyCoercionError as exc:
            return {
                "success": False,
                "error": f"Cannot set property '{name}': {exc}",
                "declared_type": declared_type,
            }
        response = _send_request(
            {"action": "setProperty", "name": name, "value": coerced}
        )
        if isinstance(response, dict) and coerced != value:
            # Surface the coercion so the caller can see what was actually sent.
            return {**response, "coerced_value": coerced, "original_value": value}
        return response

    @mcp.tool()
    def qml_explorer_list_properties() -> dict[str, Any]:
        """List all available properties for the current component page in QML Gauges Explorer (qml-gauges repo).

        Returns full documentation for each property including name, type,
        range, default value, and description.

        Returns:
            List of property definitions with metadata
        """
        return _send_request({"action": "listProperties"})

    @mcp.tool()
    def qml_explorer_build() -> dict[str, Any]:
        """Build the QML Gauges Explorer (qml-gauges repo).

        Runs cmake configure and build for the explorer. This is required
        before launching the explorer if the code has changed.

        Requires DEVDASH_QML_GAUGES_PATH to be set in .env or environment.

        Returns:
            Build result with success status and output
        """
        config = get_config()
        qml_gauges_path = config.qml_gauges_path

        if not qml_gauges_path:
            return {
                "success": False,
                "error": "DEVDASH_QML_GAUGES_PATH not configured. "
                "Copy .env.example to .env and set the path to your qml-gauges repository.",
            }

        build_path = os.path.join(qml_gauges_path, "build")

        try:
            # Run cmake configure if needed
            if not os.path.exists(os.path.join(build_path, "CMakeCache.txt")):
                configure_result = subprocess.run(
                    ["cmake", "-B", "build"],
                    cwd=qml_gauges_path,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if configure_result.returncode != 0:
                    return {
                        "success": False,
                        "error": "CMake configure failed",
                        "stderr": configure_result.stderr,
                    }

            # Run cmake build
            build_result = subprocess.run(
                ["cmake", "--build", "build", "-j"],
                cwd=qml_gauges_path,
                capture_output=True,
                text=True,
                timeout=300,
            )

            if build_result.returncode == 0:
                return {
                    "success": True,
                    "message": "Build completed successfully",
                    "output": build_result.stdout[-2000:] if len(build_result.stdout) > 2000 else build_result.stdout,
                }
            else:
                return {
                    "success": False,
                    "error": "Build failed",
                    "stderr": build_result.stderr[-2000:] if len(build_result.stderr) > 2000 else build_result.stderr,
                }

        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Build timed out after 5 minutes"}
        except FileNotFoundError:
            return {"success": False, "error": "cmake not found in PATH"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def qml_explorer_launch(render_timing: bool = False) -> dict[str, Any]:
        """Launch the QML Gauges Explorer (qml-gauges repo).

        Starts the explorer with the correct library paths. The explorer
        provides a WebSocket server on port 9876 for property inspection
        and modification.

        Requires DEVDASH_QML_GAUGES_PATH to be set in .env or environment.

        Args:
            render_timing: When True, sets ``QSG_RENDER_TIMING=1`` in the
                explorer's environment, causing Qt's scene graph to emit a
                per-frame timing log line on stderr (captured to the same
                log file ``qml_explorer_logs_get`` reads). This is the
                prerequisite for ``qml_explorer_measure_frame_time``. Off
                by default — the per-frame logging adds noise and a small
                amount of overhead, so opt in only when measuring.

        Returns:
            Launch result with PID if successful
        """
        config = get_config()

        if not config.qml_gauges_path:
            return {
                "success": False,
                "error": "DEVDASH_QML_GAUGES_PATH not configured. "
                "Copy .env.example to .env and set the path to your qml-gauges repository.",
            }

        # Check if already running
        try:
            result = subprocess.run(
                ["pgrep", "-f", "qml-gauges-explorer"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                pids = result.stdout.strip().split("\n")
                return {
                    "success": True,
                    "message": "Explorer is already running",
                    "pids": pids,
                }
        except Exception:
            pass

        # Check executable exists
        if not os.path.exists(config.explorer_executable):
            return {
                "success": False,
                "error": f"Explorer not found at {config.explorer_executable}. Run qml_explorer_build first.",
            }

        # Launch with correct library path
        env = os.environ.copy()
        existing_lib_path = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = f"{config.explorer_lib_path}:{existing_lib_path}"

        # Force Qt's message handler (qDebug / qInfo / qWarning, plus any QML
        # console.log) onto stderr. When stderr is redirected to a file rather
        # than a tty — which it is here, we capture it for qml_explorer_logs_get
        # — Qt's default handler otherwise routes to the systemd journal, so the
        # captured log file stays empty. QT_FORCE_STDERR_LOGGING overrides that.
        env["QT_FORCE_STDERR_LOGGING"] = "1"

        if render_timing:
            # QSG_RENDER_TIMING makes the scene graph emit one
            # "qt.scenegraph.time.renderloop: ... frame rendered in Nms,
            # ... perWindowFrameDelta=D" line per frame. The corresponding
            # logging category has to be enabled too, or Qt suppresses it
            # at info level. QT_LOGGING_RULES wins over any user config.
            env["QSG_RENDER_TIMING"] = "1"
            existing_rules = env.get("QT_LOGGING_RULES", "")
            rule = "qt.scenegraph.time.*=true"
            env["QT_LOGGING_RULES"] = (
                f"{existing_rules};{rule}" if existing_rules else rule
            )

        try:
            log_dir = Path(tempfile.gettempdir()) / "devdash-mcp-explorer-logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            fd, raw_log_path = tempfile.mkstemp(
                prefix="explorer_", suffix=".log", dir=log_dir
            )
            os.close(fd)
            log_path = Path(raw_log_path)
            log_handle = open(log_path, "wb")

            process = subprocess.Popen(
                [config.explorer_executable],
                cwd=config.qml_gauges_path,
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            # Popen retains the fd; close ours so kernel cleans up when
            # the process exits.
            log_handle.close()

            # Give it a moment to start
            import time
            time.sleep(1)

            # Check if it's still running
            if process.poll() is None:
                _LAUNCHED_LOG_PATHS[process.pid] = log_path
                return {
                    "success": True,
                    "message": "Explorer launched successfully",
                    "pid": process.pid,
                    "log_path": str(log_path),
                }
            else:
                return {
                    "success": False,
                    "error": "Explorer exited immediately after launch. Check library dependencies.",
                    "log_path": str(log_path),
                }

        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def qml_explorer_logs_get(tail_lines: int = 200) -> dict[str, Any]:
        """Retrieve recent stdout/stderr from the explorer.

        Returns the last N lines from the log file populated by the explorer
        process launched by this MCP session. If the explorer was not launched
        by this session (e.g. the user started it manually, or the MCP server
        restarted), this tool returns an explicit error rather than partial
        data — there is no silent fallback.

        Args:
            tail_lines: Number of trailing lines to return (default 200,
                        clamped to [1, 10000]).

        Returns:
            On success:
                {
                  "success": True,
                  "pid": int,
                  "log_path": str,
                  "lines": [str, ...],   # last `tail_lines` lines
                  "line_count_total": int,
                  "truncated": bool,     # True if file had more than tail_lines
                }
            On failure:
                {"success": False, "error": <reason>}
        """
        tail_lines = max(1, min(10000, int(tail_lines)))

        if not _LAUNCHED_LOG_PATHS:
            return {
                "success": False,
                "error": (
                    "Explorer logs unavailable: not launched by this MCP "
                    "session. Relaunch via qml_explorer_kill + "
                    "qml_explorer_launch to enable log capture."
                ),
            }

        # Pick the most-recently-launched still-known PID.
        # We don't aggressively prune; if a PID is dead but its log file
        # still exists, that's still the right log to surface.
        pid, log_path = max(_LAUNCHED_LOG_PATHS.items(), key=lambda kv: kv[0])

        if not log_path.exists():
            return {
                "success": False,
                "error": f"Log file vanished at {log_path}",
            }

        try:
            raw = log_path.read_bytes()
        except OSError as e:
            return {"success": False, "error": f"Failed to read log: {e}"}

        text = raw.decode("utf-8", errors="replace")
        all_lines = text.splitlines()
        total = len(all_lines)
        kept = all_lines[-tail_lines:]
        return {
            "success": True,
            "pid": pid,
            "log_path": str(log_path),
            "lines": kept,
            "line_count_total": total,
            "truncated": total > len(kept),
        }

    @mcp.tool()
    def qml_explorer_measure_frame_time(
        duration_seconds: float = 5.0,
    ) -> dict[str, Any]:
        """Measure per-frame scene-graph timing over a sampling window.

        Parses ``QSG_RENDER_TIMING`` output from the explorer's captured
        stderr log over the next ``duration_seconds`` seconds and returns
        aggregate statistics. Reports two distinct metrics — render cost
        (CPU/GPU time Qt spent producing a frame) and frame interval
        (wall-clock gap between consecutive frames, which determines
        actual FPS):

        Prerequisites:
          * Explorer must be running and managed by this session
            (``qml_explorer_launch`` captures the log; a foreign instance
            doesn't have one).
          * Explorer must have been launched with ``render_timing=True``;
            otherwise no scene-graph timing lines are emitted and the
            tool returns an error pointing to the right relaunch.
          * Something on the current page should be animating during the
            window — Qt's threaded renderloop parks when the scene is
            idle, so a fully static gauge will report ``sample_count: 0``.

        Resolution note: Qt reports frame times in integer milliseconds.
        That's enough to compare a 2 ms render against an 8 ms render but
        won't resolve sub-millisecond differences.

        Args:
            duration_seconds: Sampling window length in seconds. Clamped
                to ``[0.5, 60.0]``.

        Returns:
            On success:
                {
                  "success": True,
                  "duration_seconds": float,
                  "sample_count": int,
                  "render_cost_ms": {
                    "mean": float, "max": float, "p50": float,
                    "p95": float, "std_dev": float,
                  },
                  "frame_interval_ms": {
                    "mean": float, "max": float, "p50": float,
                    "p95": float, "std_dev": float,
                  },
                  "average_frame_time_ms": float,    # render_cost mean,
                                                     # kept for symmetry
                                                     # with the request
                  "max_frame_time_ms": float,        # render_cost max
                  "std_dev_ms": float,               # render_cost stddev
                  "target_fps_60_met_fraction": float,
                      # fraction of frames whose interval <= 16.6 ms;
                      # ``None`` if no interval samples were captured
                  "pid": int,
                  "log_path": str,
                }
            On failure:
                {"success": False, "error": <reason>}
        """
        duration = max(0.5, min(60.0, float(duration_seconds)))

        if not _LAUNCHED_LOG_PATHS:
            return {
                "success": False,
                "error": (
                    "No explorer launched by this MCP session — frame-time "
                    "measurement reads from the captured stderr log, which "
                    "doesn't exist for foreign instances. Run "
                    "qml_explorer_kill then qml_explorer_launch("
                    "render_timing=True)."
                ),
            }

        pid, log_path = max(_LAUNCHED_LOG_PATHS.items(), key=lambda kv: kv[0])

        if not log_path.exists():
            return {
                "success": False,
                "error": f"Log file vanished at {log_path}",
            }

        # Confirm QSG_RENDER_TIMING is actually emitting. The category prefix
        # is stable across Qt 6.x. If absent, the explorer was launched
        # without render_timing.
        try:
            existing = log_path.read_bytes()
        except OSError as e:
            return {"success": False, "error": f"Failed to read log: {e}"}

        if b"qt.scenegraph.time.renderloop" not in existing:
            return {
                "success": False,
                "error": (
                    "Explorer was launched without render_timing. Relaunch "
                    "with qml_explorer_kill then qml_explorer_launch("
                    "render_timing=True), then retry."
                ),
                "pid": pid,
                "log_path": str(log_path),
            }

        # Mark the end of the existing log, sleep, then parse only what
        # was appended during the window. This keeps the measurement
        # bounded to the requested window even if the log already has
        # minutes of prior timing data.
        start_offset = len(existing)
        time.sleep(duration)

        try:
            with open(log_path, "rb") as f:
                f.seek(start_offset)
                window_bytes = f.read()
        except OSError as e:
            return {"success": False, "error": f"Failed to read log: {e}"}

        window_text = window_bytes.decode("utf-8", errors="replace")
        render_times, intervals = _parse_qsg_render_timing(window_text)

        def _summarise(samples: list[float]) -> dict[str, float] | None:
            if not samples:
                return None
            return {
                "mean": round(statistics.fmean(samples), 3),
                "max": round(max(samples), 3),
                "p50": round(statistics.median(samples), 3),
                "p95": round(_percentile(samples, 95), 3),
                "std_dev": round(
                    statistics.pstdev(samples) if len(samples) > 1 else 0.0, 3
                ),
            }

        render_summary = _summarise(render_times)
        interval_summary = _summarise(intervals)

        if intervals:
            under_16_6 = sum(1 for dt in intervals if dt <= 16.6)
            target_fraction = round(under_16_6 / len(intervals), 4)
        else:
            target_fraction = None

        return {
            "success": True,
            "duration_seconds": duration,
            "sample_count": len(render_times),
            "render_cost_ms": render_summary,
            "frame_interval_ms": interval_summary,
            "average_frame_time_ms": render_summary["mean"] if render_summary else None,
            "max_frame_time_ms": render_summary["max"] if render_summary else None,
            "std_dev_ms": render_summary["std_dev"] if render_summary else None,
            "target_fps_60_met_fraction": target_fraction,
            "pid": pid,
            "log_path": str(log_path),
        }

    @mcp.tool()
    def qml_explorer_kill() -> dict[str, Any]:
        """Kill the running QML Gauges Explorer (qml-gauges repo).

        Terminates any running explorer processes.

        Returns:
            Result with number of processes killed
        """
        try:
            # Find explorer processes
            result = subprocess.run(
                ["pgrep", "-f", "qml-gauges-explorer"],
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                return {
                    "success": True,
                    "message": "No explorer processes running",
                    "killed": 0,
                }

            pids = result.stdout.strip().split("\n")
            killed = 0

            for pid in pids:
                try:
                    pid_int = int(pid)
                except ValueError:
                    continue
                try:
                    os.kill(pid_int, signal.SIGTERM)
                    killed += 1
                except ProcessLookupError:
                    pass
                _LAUNCHED_LOG_PATHS.pop(pid_int, None)

            return {
                "success": True,
                "message": f"Killed {killed} explorer process(es)",
                "killed": killed,
            }

        except Exception as e:
            return {"success": False, "error": str(e)}
