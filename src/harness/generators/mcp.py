"""Generator for `.mcp.json` / `.cursor/mcp.json`."""

import secrets
from pathlib import Path

from harness.models import McpConfig, McpServer
from harness.models.workspace import PLATFORM_DIR_PLACEHOLDER

TOOLS_JS_PLACEHOLDER = "<PLATFORM_TOOLS_JS>"


def build_mcp(
    *,
    ipc_port: int,
    platform_dir: Path | None = None,
    include_platform_tools: bool = False,
    platform_tools_js: Path | None = None,
    ipc_token: str | None = None,
    python_command: str = "python",
) -> McpConfig:
    """Build the MCP registry for a workspace.

    Args:
        ipc_port: Unique IPC port, also synced into `.vscode/settings.json`.
        platform_dir: 1C platform install dir; a placeholder is used when None.
        include_platform_tools: Add the optional IDE server `mcp-1c-platform-tools`.
        platform_tools_js: Path to the extension's `out/src/index.js`; a placeholder is
            written when None.
        ipc_token: IPC token; generated randomly when None.
        python_command: Interpreter that has `mcp_bsl_context` installed; `python`
            by default, a placeholder when no usable interpreter was found.

    Returns:
        Validated MCP config.
    """
    platform = platform_dir.as_posix() if platform_dir else PLATFORM_DIR_PLACEHOLDER
    servers: dict[str, McpServer] = {
        "vrunner-devops": McpServer(
            command="vrunner-mcp", args=["--settings", "./autumn-properties.json"]
        ),
        "bsl-platform-help": McpServer(
            command=python_command,
            args=["-m", "mcp_bsl_context", "-p", platform],
            env={"MCP_BSL_SEARCH_DEFAULT_MODE": "keyword"},
        ),
        "bsl-atlas-rag": McpServer(url="http://localhost:8000/mcp"),
        "bsl-lsp": McpServer(
            command="bsl-language-server",
            args=["mcp", "-c", "./.bsl-language-server.json"],
            env={"JAVA_TOOL_OPTIONS": "-Xmx4g"},
        ),
    }
    if include_platform_tools:
        script = platform_tools_js.as_posix() if platform_tools_js else TOOLS_JS_PLACEHOLDER
        servers["mcp-1c-platform-tools"] = McpServer(
            command="node",
            args=[script],
            env={
                "ONEC_IPC_HOST": "127.0.0.1",
                "ONEC_IPC_PORT": str(ipc_port),
                "ONEC_IPC_TOKEN": ipc_token or secrets.token_hex(16),
            },
        )
    return McpConfig(mcp_servers=servers)
