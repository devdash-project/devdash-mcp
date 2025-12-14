"""Configuration for DevDash MCP Server."""

import os
from dataclasses import dataclass


@dataclass
class Config:
    """MCP server configuration."""

    # QML Gauges Explorer WebSocket settings
    explorer_ws_port: int = 9876
    explorer_ws_host: str = "localhost"

    # DevDash DevTools HTTP API settings
    devtools_port: int = 18080
    devtools_host: str = "127.0.0.1"

    @property
    def explorer_ws_url(self) -> str:
        """WebSocket URL for QML Gauges Explorer."""
        return f"ws://{self.explorer_ws_host}:{self.explorer_ws_port}"

    @property
    def devtools_base_url(self) -> str:
        """Base URL for DevTools HTTP API."""
        return f"http://{self.devtools_host}:{self.devtools_port}"


# Global configuration instance
_config: Config | None = None


def get_config() -> Config:
    """Get the configuration, loading from environment if needed."""
    global _config
    if _config is None:
        _config = Config(
            explorer_ws_port=int(os.environ.get("DEVDASH_EXPLORER_WS_PORT", 9876)),
            explorer_ws_host=os.environ.get("DEVDASH_EXPLORER_WS_HOST", "localhost"),
            devtools_port=int(os.environ.get("DEVDASH_DEVTOOLS_PORT", 18080)),
            devtools_host=os.environ.get("DEVDASH_DEVTOOLS_HOST", "127.0.0.1"),
        )
    return _config
