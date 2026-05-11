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
| `qml_explorer_get_state` | Get current page and property values |
| `qml_explorer_navigate` | Navigate to a component page |
| `qml_explorer_get_property` | Get a single property value |
| `qml_explorer_set_property` | Set a property value |
| `qml_explorer_list_properties` | List available properties with metadata |

### devdash repo (DevDash runtime via HTTP API)

| Tool | Description |
|------|-------------|
| `devdash_telemetry_get_state` | Get current vehicle telemetry (RPM, temps, etc.) |
| `devdash_telemetry_get_warnings` | Get active warnings and alerts |
| `devdash_telemetry_list_windows` | List DevDash windows via DevTools |
| `devdash_telemetry_screenshot` | Capture screenshot via DevTools API |
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
├── tests/                  # stdlib unittest suite
├── pyproject.toml
└── README.md
```

### Tests

A small `unittest` suite (stdlib, no extra dependencies) covers the
property-value coercion rules and the screenshot focus/restore sequencing:

```bash
python -m unittest discover -s tests
```

(The bulk of the screenshot and WebSocket paths still need a running explorer
and a live compositor, so they're exercised manually rather than in the suite.)

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
