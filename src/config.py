"""Configuration for DevDash MCP Server.

Configuration is loaded from environment variables, which can be set in a .env file.
Copy .env.example to .env and customize for your setup.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from the package directory
_package_dir = Path(__file__).parent.parent
_env_file = _package_dir / ".env"
if _env_file.exists():
    load_dotenv(_env_file)


@dataclass
class Config:
    """MCP server configuration."""

    # QML Gauges Explorer WebSocket settings
    explorer_ws_port: int = 9876
    explorer_ws_host: str = "localhost"

    # DevDash DevTools HTTP API settings
    devtools_port: int = 18080
    devtools_host: str = "127.0.0.1"

    # Project paths (must be configured in .env or environment)
    qml_gauges_path: str = ""

    @property
    def explorer_ws_url(self) -> str:
        """WebSocket URL for QML Gauges Explorer."""
        return f"ws://{self.explorer_ws_host}:{self.explorer_ws_port}"

    @property
    def devtools_base_url(self) -> str:
        """Base URL for DevTools HTTP API."""
        return f"http://{self.devtools_host}:{self.devtools_port}"

    @property
    def explorer_executable(self) -> str:
        """Path to the QML Gauges Explorer executable."""
        return os.path.join(self.qml_gauges_path, "build/explorer/qml-gauges-explorer")

    @property
    def explorer_lib_path(self) -> str:
        """LD_LIBRARY_PATH for running the explorer."""
        build_path = os.path.join(self.qml_gauges_path, "build")
        return ":".join([
            os.path.join(build_path, "src/compounds"),
            os.path.join(build_path, "src/primitives"),
        ])


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
            qml_gauges_path=os.environ.get("DEVDASH_QML_GAUGES_PATH", ""),
        )
    return _config
