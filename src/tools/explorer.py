"""QML Gauges Explorer tools - WebSocket communication with the explorer app.

These tools interact with the QML Gauges Explorer from the qml-gauges repository.
Includes tools for building, launching, and managing the explorer process,
as well as property inspection and modification via WebSocket.
"""

import json
import os
import signal
import subprocess
import tempfile
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


# Valid component pages in the explorer
EXPLORER_PAGES = [
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


def register_explorer_tools(mcp: FastMCP) -> None:
    """Register QML Gauges Explorer tools with the MCP server."""

    @mcp.tool()
    def qml_explorer_status() -> dict[str, Any]:
        """Check if the QML Gauges Explorer is running (qml-gauges repo).

        Returns the running status, process IDs if running, and whether
        the WebSocket server is responding.

        Returns:
            Status dict with 'running', 'pids', and 'websocket_connected' fields
        """
        result: dict[str, Any] = {
            "running": False,
            "pids": [],
            "websocket_connected": False,
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
        """Navigate the QML Gauges Explorer to a specific component page (qml-gauges repo).

        Args:
            page: Component page name. Valid pages: Welcome, BezelScrews, GaugeArc,
                  GaugeBezel, GaugeCenterCap, GaugeFace, GaugeTick, GaugeTickLabel,
                  DigitalReadout, GaugeNeedle, GaugeTickRing, GaugeValueArc,
                  GaugeZoneArc, RollingDigitReadout, RadialGauge, RadialGauge3D,
                  IndustrialGauge, Bezel3D, CenterCap3D

        Returns:
            Navigation result with success status
        """
        if page not in EXPLORER_PAGES:
            return {
                "success": False,
                "error": f"Invalid page '{page}'. Valid pages: {', '.join(EXPLORER_PAGES)}",
            }
        return _send_request({"action": "navigate", "page": page})

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
    def qml_explorer_set_property(name: str, value: Any) -> dict[str, Any]:
        """Set a property value on the current component in the QML Gauges Explorer (qml-gauges repo).

        Args:
            name: Property name (e.g., 'tickShape', 'color', 'hasGlow')
            value: Value to set (type depends on property: string, number, boolean, color hex)

        Returns:
            Result with success status
        """
        return _send_request({"action": "setProperty", "name": name, "value": value})

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
    def qml_explorer_launch() -> dict[str, Any]:
        """Launch the QML Gauges Explorer (qml-gauges repo).

        Starts the explorer with the correct library paths. The explorer
        provides a WebSocket server on port 9876 for property inspection
        and modification.

        Requires DEVDASH_QML_GAUGES_PATH to be set in .env or environment.

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
