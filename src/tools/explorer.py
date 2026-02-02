"""QML Gauges Explorer tools - WebSocket communication with the explorer app.

These tools interact with the QML Gauges Explorer from the qml-gauges repository.
Includes tools for building, launching, and managing the explorer process,
as well as property inspection and modification via WebSocket.
"""

import json
import os
import signal
import subprocess
from typing import Any

from mcp.server.fastmcp import FastMCP

try:
    from websockets.sync.client import connect as ws_connect

    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

from ..config import get_config


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
            page: Component page name. Valid pages: Welcome, GaugeArc, GaugeBezel,
                  GaugeCenterCap, GaugeFace, GaugeTick, GaugeTickLabel, DigitalReadout,
                  GaugeNeedle, GaugeTickRing, GaugeValueArc, GaugeZoneArc,
                  RollingDigitReadout, RadialGauge, RadialGauge3D, Bezel3D, CenterCap3D

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
        """Get the current value of a specific property in the QML Gauges Explorer (qml-gauges repo).

        Args:
            name: Property name (e.g., 'tickShape', 'color', 'hasGlow')

        Returns:
            Property value and metadata
        """
        return _send_request({"action": "getProperty", "name": name})

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
            process = subprocess.Popen(
                [config.explorer_executable],
                cwd=config.qml_gauges_path,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            # Give it a moment to start
            import time
            time.sleep(1)

            # Check if it's still running
            if process.poll() is None:
                return {
                    "success": True,
                    "message": "Explorer launched successfully",
                    "pid": process.pid,
                }
            else:
                return {
                    "success": False,
                    "error": "Explorer exited immediately after launch. Check library dependencies.",
                }

        except Exception as e:
            return {"success": False, "error": str(e)}

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
                    os.kill(int(pid), signal.SIGTERM)
                    killed += 1
                except (ProcessLookupError, ValueError):
                    pass

            return {
                "success": True,
                "message": f"Killed {killed} explorer process(es)",
                "killed": killed,
            }

        except Exception as e:
            return {"success": False, "error": str(e)}
