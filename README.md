# QML Gauges MCP Server

Model Context Protocol (MCP) server providing Claude Code with window inspection and screenshot capture for QML Gauges applications.

## Architecture

```
Claude Code CLI
      |
      | MCP Protocol (stdio)
      v
+---------------------------+
| qmlgauges-mcp (Python)    |
| - qmlgauges_list_windows  |
| - qmlgauges_screenshot    |
+------------+--------------+
             | X11 Commands (xwininfo, wmctrl, import/scrot)
             v
+---------------------------+
| X11 Window System         |
| - QML Gauges Explorer     |
| - QML Gauges Examples     |
+---------------------------+
```

## Installation

From the `tools/mcp` directory:

```bash
# Install in development mode
pip install -e .

# Or install normally
pip install .
```

## System Requirements

The MCP server uses X11 tools for window inspection and screenshot capture. Install these dependencies:

```bash
# Ubuntu/Debian
sudo apt install wmctrl imagemagick scrot

# The server will work with either:
# - imagemagick (preferred, uses 'import' command)
# - scrot (fallback)
```

## Configuration

Add to `.claude/mcp.json` in the qml-gauges project root:

```json
{
  "mcpServers": {
    "qmlgauges": {
      "command": "python",
      "args": ["-m", "qmlgauges_mcp"],
      "cwd": "/home/dev/Projects/personal/devdash/qml-gauges/tools/mcp"
    }
  }
}
```

## MCP Tools

### `qmlgauges_list_windows`

List available QML Gauges windows for screenshot capture.

**Returns**: JSON object with window information

**Example**:
```json
{
  "windows": [
    {
      "id": "0x3c0000c",
      "title": "DevDash Gauges Explorer",
      "width": 1280,
      "height": 800,
      "x": 379,
      "y": 29
    }
  ],
  "total_count": 1
}
```

### `qmlgauges_screenshot`

Capture PNG screenshot of a QML Gauges window.

**Parameters**:
- `window` (required): Window name to capture (e.g., "explorer", "DevDash Gauges Explorer")

**Returns**: PNG image

**Example usage in Claude Code**:
```
Can you show me a screenshot of the explorer window?
```

The tool performs case-insensitive substring matching on window titles:
- "explorer" matches "DevDash Gauges Explorer"
- "gauge" matches any window with "gauge" in the title
- "qml" matches any QML-related window

## Usage Example

Once configured, Claude Code can directly inspect and screenshot QML Gauges windows:

```
User: What QML Gauges windows are open?
Claude: [uses qmlgauges_list_windows tool] The DevDash Gauges Explorer is running at 1280x800.

User: Show me the explorer window
Claude: [uses qmlgauges_screenshot tool with window="explorer"] Here's the current explorer view...
```

## Troubleshooting

**"Window not found"**:
- Use `qmlgauges_list_windows` to see available windows
- Window matching is case-insensitive and uses substring search
- Ensure the application window is visible (not minimized)

**"Failed to capture screenshot"**:
- Ensure either `imagemagick` or `scrot` is installed
- Check that the window is not minimized or hidden
- Try running manually: `import -window <id> screenshot.png`

**No windows listed**:
- Ensure QML Gauges application is running
- Check that windows are visible on the current X display
- Verify `wmctrl -l` shows the windows

## Development

The MCP server uses X11 tools to inspect and capture windows. It doesn't require any modifications to QML applications - it works with any Qt/QML window via the X11 protocol.

To modify or extend:
1. Edit `qmlgauges_mcp/server.py`
2. Add new tools in `list_tools()` and `call_tool()`
3. Use standard X11 tools (xwininfo, wmctrl, xdotool, etc.)

## Comparison with DevDash MCP Server

| Feature | qmlgauges-mcp | devdash-mcp |
|---------|---------------|-------------|
| **Architecture** | Direct X11 tools | HTTP bridge to C++ server |
| **Dependencies** | wmctrl, imagemagick/scrot | Running DevDash application |
| **Window Detection** | Any X11 window | Only DevDash windows |
| **Performance** | Fast (direct) | Fast (HTTP localhost) |
| **State Access** | Screenshot only | Full telemetry access |
| **Use Case** | Development/debugging | Runtime monitoring |

The qmlgauges-mcp server is simpler because it doesn't need access to application state - it only needs to see and capture windows, which X11 provides for free.
