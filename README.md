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

For screenshot capture via X11:
```bash
# Ubuntu/Debian
sudo apt install wmctrl imagemagick scrot
```

## Configuration

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

| Variable | Default | Description |
|----------|---------|-------------|
| `DEVDASH_EXPLORER_WS_PORT` | 9876 | Explorer WebSocket port |
| `DEVDASH_EXPLORER_WS_HOST` | localhost | Explorer WebSocket host |
| `DEVDASH_DEVTOOLS_PORT` | 18080 | DevTools HTTP API port |
| `DEVDASH_DEVTOOLS_HOST` | 127.0.0.1 | DevTools HTTP API host |

## Available Tools

### qml-gauges repo (QML Gauges Explorer via WebSocket)

| Tool | Description |
|------|-------------|
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

### System (X11 window capture - works with any window)

| Tool | Description |
|------|-------------|
| `screenshot_list_windows` | List available windows |
| `screenshot_capture` | Capture window as PNG |

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
│       ├── screenshot.py   # System: X11 window capture
│       ├── telemetry.py    # devdash: Runtime telemetry (HTTP)
│       └── logs.py         # devdash: Application logs (HTTP)
├── pyproject.toml
└── README.md
```

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
- Install `imagemagick` or `scrot`
- Ensure window is not minimized
