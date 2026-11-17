"""Schema for the `.env` file consumed by docker compose."""

from pathlib import Path
from typing import Literal

from pydantic import Field

from harness.models.base import StrictModel


class EnvFile(StrictModel):
    """Environment variables for the compose stack."""

    source_path: Path
    platform_dir: Path | None = None
    onec_ipc_port: int = Field(ge=1024, le=65535)
    indexing_mode: Literal["fast", "full"] = "fast"

    def render(self) -> str:
        """Render as sorted `KEY=value` lines. Paths use forward slashes.

        Returns:
            File text terminated by a newline.
        """
        values: dict[str, str] = {
            "SOURCE_PATH": self.source_path.as_posix(),
            "ONEC_IPC_PORT": str(self.onec_ipc_port),
            "INDEXING_MODE": self.indexing_mode,
        }
        if self.platform_dir is not None:
            values["PLATFORM_DIR"] = self.platform_dir.as_posix()
        return "".join(f"{k}={v}\n" for k, v in sorted(values.items()))


def replace_env_value(text: str, key: str, value: str) -> str:
    """Replace or append a single key in `.env` text, preserving other lines.

    Args:
        text: Existing file content.
        key: Variable name.
        value: New value.

    Returns:
        Updated file content.
    """
    lines = text.splitlines()
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    return "\n".join(out) + "\n"
