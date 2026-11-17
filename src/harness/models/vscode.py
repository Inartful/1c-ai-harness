"""Schema and merge logic for `.vscode/settings.json`."""

import json

from pydantic import BaseModel, ConfigDict

IPC_PORT_KEY = "1c-platform-tools.ipc.port"  # PROVISIONAL: confirm against the extension


class VSCodeSettings(BaseModel):
    """VS Code settings that preserves unknown user keys."""

    model_config = ConfigDict(extra="allow")

    def with_ipc_port(self, port: int) -> "VSCodeSettings":
        """Return a copy with the IPC port set.

        Args:
            port: IPC port to record.

        Returns:
            Updated settings.
        """
        data = self.model_dump()
        data[IPC_PORT_KEY] = port
        return VSCodeSettings.model_validate(data)

    def ipc_port(self) -> int | None:
        """Read the configured IPC port, if any.

        Returns:
            The port, or None when unset or not an integer.
        """
        value = (self.model_extra or {}).get(IPC_PORT_KEY)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def to_json(self) -> str:
        """Serialize to indented JSON.

        Returns:
            JSON text terminated by a newline.
        """
        return json.dumps(self.model_dump(), indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_text(cls, text: str) -> "VSCodeSettings":
        """Parse settings JSON; empty text yields empty settings.

        Args:
            text: File content.

        Returns:
            Parsed settings.
        """
        return cls.model_validate(json.loads(text) if text.strip() else {})
