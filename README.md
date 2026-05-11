# DevDash MCP Server

Model Context Protocol (MCP) server providing Claude Code with tools for DevDash development and debugging.

## Architecture

```
Claude Code CLI
      │
      │ MCP Protocol (stdio)
      ▼
┌─────────────────────────────────────────┐
│         devdash-mcp (Python)            │
│                                         │
│  ┌─────────────┐  ┌─────────────────┐   │
│  │ qml-gauges  │  │   Screenshot    │   │
│  │ (WebSocket) │  │     (X11)       │   │
│  └──────┬──────┘  └────────┬────────┘   │
│         │                  │            │
│  ┌──────┴──────┐  ┌────────┴────────┐   │
│  │   devdash   │  │    devdash      │   │
│  │ (Telemetry) │  │    (Logs)       │   │
│  └─────────────┘  └─────────────────┘   │
└─────────────────────────────────────────┘
         │                    │
         ▼                    ▼
┌─────────────────┐  ┌─────────────────┐
│  QML Gauges     │  │    DevDash      │
│   Explorer      │  │  (DevTools)     │
│  (port 9876)    │  │  (port 18080)   │
└─────────────────┘  └─────────────────┘
```

## Installation

```bash
# Install in development mode
pip install -e .

# Or install normally
pip install .
```

## System Requirements

Screenshot capture auto-detects the session type from `XDG_SESSION_TYPE`
at server startup. Install the binaries for whichever you use.

For X11 sessions:
```bash
# Ubuntu/Debian
sudo apt install wmctrl imagemagick scrot
# Arch / EndeavourOS
sudo pacman -S wmctrl imagemagick scrot
```

For Wayland sessions (Hyprland):
```bash
# Arch / EndeavourOS
sudo pacman -S grim hyprland   # hyprctl ships with hyprland
```

`Pillow` is installed automatically as a Python dependency and handles
crop/scale post-processing on both paths.

## Configuration

### 1. Create local .env file

Copy the example configuration and customize for your setup:

```bash
cd devdash-mcp
cp .env.example .env
# Edit .env with your paths
```

### 2. Add MCP server to Claude Code

Add to `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "devdash": {
      "command": "python",
      "args": ["-m", "src.server"],
      "cwd": "/path/to/devdash-mcp"
    }
  }
}
```

### Environment Variables

Configuration can be set in `.env` or as environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DEVDASH_EXPLORER_WS_PORT` | 9876 | Explorer WebSocket port |
| `DEVDASH_EXPLORER_WS_HOST` | localhost | Explorer WebSocket host |
| `DEVDASH_DEVTOOLS_PORT` | 18080 | DevTools HTTP API port |
| `DEVDASH_DEVTOOLS_HOST` | 127.0.0.1 | DevTools HTTP API host |
| `DEVDASH_QML_GAUGES_PATH` | (required) | Absolute path to qml-gauges repo |

## Available Tools

### qml-gauges repo (QML Gauges Explorer)

| Tool | Description |
|------|-------------|
| `qml_explorer_build` | Build the explorer (cmake configure + build) |
| `qml_explorer_launch` | Launch explorer with correct library paths |
| `qml_explorer_kill` | Kill running explorer processes |
| `qml_explorer_status` | Whether an explorer is running, and whether *this* session launched it (`managed_by_session`) |
| `qml_explorer_get_state` | Get current page and property values |
| `qml_explorer_navigate` | Navigate to a component page (valid pages discovered from the explorer source at call time; confirms the page actually switched) |
| `qml_explorer_get_property` | Get a single property value (resolves bound properties too) |
| `qml_explorer_set_property` | Set a property value (string args are coerced to the property's declared type) |
| `qml_explorer_reset_property` | Re-establish a property's binding after `set_property`/`freeze` pinned it (needs explorer support — see below) |
| `qml_explorer_freeze_property` / `qml_explorer_freeze_all_properties` | Pin a property (or all) to its current value, breaking animation/expression bindings for verification |
| `qml_explorer_list_properties` | List available properties with metadata |
| `qml_explorer_logs_get` | Tail the explorer's stdout/stderr (only when this session launched it) |

### devdash repo (DevDash runtime via HTTP API)

| Tool | Description |
|------|-------------|
| `devdash_telemetry_get_state` | Get current vehicle telemetry (RPM, temps, etc.) |
| `devdash_telemetry_get_warnings` | Get active warnings and alerts |
| `devdash_telemetry_list_windows` | List DevDash windows via DevTools |
| `devdash_telemetry_screenshot` | Capture screenshot via DevTools API (path-only by default, like `screenshot_capture`; `inline_thumbnail=True` for a base64 preview) |
| `devdash_logs_get` | Retrieve logs with filtering (level, category, count) |

### System (window capture — X11 or Wayland/Hyprland, auto-detected)

| Tool | Description |
|------|-------------|
| `screenshot_list_windows` | List available windows (filtered by DevDash keywords) |
| `screenshot_capture` | Capture window as PNG (focuses the target window first so the image matches the user's view, then restores focus) |
| `screenshot_gauge_preview` | Compact crop centered on the gauge preview pane (also focuses the target window first) |

The capture tools focus the target window before grabbing pixels because
compositors apply effects to *unfocused* windows — e.g. Hyprland's
`inactive_opacity` + blur `xray` makes a window translucent and bleeds the
wallpaper through, so a capture of an unfocused window looks different from
what the user sees in direct view. Focus is restored to the previously-focused
window afterwards (this can briefly flip the active window/workspace). The
result includes a `focused` boolean; when it's `false` the image may still be
distorted and a `focus_warning` explains why.

`qml_explorer_set_property` coerces string arguments to the target property's
declared type — `"true"` / `"false"` (case-insensitive) become real booleans
and numeric strings become numbers — so a bare `"false"` no longer reads as
truthy on the QML side and sets a bool property to *true*. A string that can't
represent the declared type (`"yes"` for a bool, `NaN` for a number, `"1.5"`
for an int) is rejected with an error instead of being silently miscoerced.
Prefer passing JSON values of the natural type (`true`/`false`, numbers); the
string forms are a convenience.

`qml_explorer_reset_property` undoes the binding break that
`qml_explorer_set_property` (and the `freeze_*` tools) cause when they assign a
property a literal — it asks the explorer to re-evaluate the original binding so
the property tracks it again. It needs the explorer to implement a
`resetProperty` WebSocket action; builds without it (all current ones) get back
`explorer_support: false` and a pointer to the qml-gauges-side change, and the
tool starts working the moment that change ships — no MCP update needed.

`qml_explorer_status` verifies liveness actively on every call (`pgrep` for the
explorer binary plus a real WebSocket round-trip), so an instance left running
by a *previous* MCP session still reports `running: true`. When it does, it also
reports `managed_by_session`: `false` means this server didn't launch it (so
`qml_explorer_logs_get` has no log for it, and `qml_explorer_launch` would just
attach to it) — `qml_explorer_kill` then `qml_explorer_launch` brings it under
this session's management. `session_pids` / `foreign_pids` break the running
PIDs down accordingly.

`qml_explorer_navigate` validates the page name against a list discovered at
call time, so a freshly added page works without restarting the MCP server.
Discovery order: the local checkout's `explorer/qml/pages/*Page.qml` files
(via `DEVDASH_QML_GAUGES_PATH`); then the running explorer's `getState`
`data.pages` array (a no-op until qml-gauges exposes that field, but the only
source that works against a remote explorer with no local checkout); then a
hardcoded fallback. It also confirms via `getState` that the explorer really
switched, and reports a failure (rather than a false success) if it didn't —
e.g. a page file that exists but isn't registered in `Main.qml`'s
`pageIndexMap`. The success response includes `pages` (the current valid set).

## Usage Examples

```
User: What QML Gauges windows are open?
Claude: [uses screenshot_list_windows] The DevDash Gauges Explorer is running.

User: Show me the explorer window
Claude: [uses screenshot_capture with window="explorer"] Here's the current view...

User: Navigate to the GaugeTick page
Claude: [uses qml_explorer_navigate with page="GaugeTick"] Navigated to GaugeTick.

User: What properties are available?
Claude: [uses qml_explorer_list_properties] Here are the available properties...

User: Set the tick color to red
Claude: [uses qml_explorer_set_property with name="color", value="#ff0000"] Done.

User: What's the current RPM?
Claude: [uses devdash_telemetry_get_state] Current RPM is 3500...
```

## Development

### Project Structure

```
devdash-mcp/
├── .github/workflows/      # CI: runs the unittest suite on push/PR
├── src/
│   ├── __init__.py
│   ├── server.py           # FastMCP entry point
│   ├── config.py           # Configuration management
│   └── tools/
│       ├── __init__.py
│       ├── explorer.py     # qml-gauges: QML Gauges Explorer (WebSocket)
│       ├── screenshot.py   # System: window capture (X11 + Wayland/Hyprland)
│       ├── image.py        # System: image diff/analysis helpers
│       ├── telemetry.py    # devdash: Runtime telemetry (HTTP)
│       └── logs.py         # devdash: Application logs (HTTP)
├── tests/                  # stdlib unittest suite (+ _fake_explorer.py fixture)
├── pyproject.toml
└── README.md
```

### Tests

A `unittest` suite (stdlib, no extra dependencies):

```bash
python -m unittest discover          # from the repo root
```

It covers the property-value coercion rules and the screenshot focus/restore
sequencing as unit tests, plus end-to-end coverage of the explorer tools
against an in-process fake WebSocket state server (`tests/_fake_explorer.py`)
and of the screenshot tools with `hyprctl`/`grim` faked. CI runs the same
suite on push/PR via `.github/workflows/tests.yml`.

(The image-analysis tools and the X11 capture path aren't covered by the
suite yet — they need real PNGs / a real X11 session — so those are still
exercised manually.)

### Adding New Tools

1. Create a new file in `src/tools/` or add to existing category
2. Create a `register_*_tools(mcp: FastMCP)` function
3. Add import and registration in `src/tools/__init__.py`
4. Register in `src/server.py`

Example:
```python
from mcp.server.fastmcp import FastMCP

def register_my_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    def my_tool(param: str) -> dict:
        """Tool description."""
        return {"result": param}
```

## Troubleshooting

**"Cannot connect to QML Gauges Explorer"**
- Ensure QML Gauges Explorer is running: `cd qml-gauges && ./build/explorer/qml-gauges-explorer`
- Check WebSocket port (default: 9876)

**"Cannot connect to DevDash"**
- Ensure DevDash is running: `cd devdash && ./build/dev/devdash --profile profiles/haltech-vcan.json`
- Check HTTP port (default: 18080)

**"Window not found"**
- Use `screenshot_list_windows` to see available windows
- Window matching is case-insensitive substring search

**"Failed to capture screenshot"**
- Wayland (Hyprland): install `grim` (and `hyprctl`, which ships with Hyprland)
- X11: install `imagemagick` or `scrot`
- Ensure the window is not minimized

**Screenshot looks washed-out / translucent / shows the wallpaper**
- This is a compositor effect on *unfocused* windows. The capture tools focus
  the target window first to avoid it; if `focused` is `false` in the result
  (with a `focus_warning`), install the focus helpers: `hyprctl` on Wayland,
  `wmctrl` (and optionally `xdotool` for focus restore) on X11.
