"""Schema for `.mcp.json` and `.cursor/mcp.json`."""

from typing import Self

from pydantic import Field, model_validator

from harness.models.base import StrictModel


class McpServer(StrictModel):
    """One MCP server entry: either a stdio command or an HTTP URL."""

    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None

    @model_validator(mode="after")
    def _exactly_one_transport(self) -> Self:
        """Ensure exactly one of `command` and `url` is set."""
        if (self.command is None) == (self.url is None):
            msg = "exactly one of 'command' or 'url' must be set"
            raise ValueError(msg)
        return self


class McpConfig(StrictModel):
    """Project-level MCP registry."""

    mcp_servers: dict[str, McpServer] = Field(alias="mcpServers")
