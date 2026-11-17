"""Workspace layout constants."""

from pathlib import Path

WORKSPACE_DIRS: tuple[Path, ...] = (
    Path("inbox"),
    Path("src/cf"),
    Path("src/cfe"),
    Path("src/epf"),
    Path("data"),
    Path("build/ib"),
    Path("build/reports"),
    Path(".agents/skills"),
    Path(".agents/rules"),
)

DEFAULT_ATLAS_IMAGE = "armankudaibergenov/bsl-atlas:latest"
DEFAULT_IBCONNECTION = "/F./build/ib"
PLATFORM_DIR_PLACEHOLDER = "<PLATFORM_DIR>"
MCP_PYTHON_PLACEHOLDER = "<MCP_PYTHON>"
