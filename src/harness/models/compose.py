"""Schema for `docker-compose.yml`."""

from typing import Literal

import yaml
from pydantic import Field

from harness.models.base import StrictModel


class ComposeService(StrictModel):
    """A single docker compose service."""

    image: str
    ports: list[str] = Field(default_factory=list)
    volumes: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)
    restart: Literal["no", "unless-stopped"] = "unless-stopped"


class ComposeFile(StrictModel):
    """Top-level compose document."""

    services: dict[str, ComposeService]

    def to_yaml(self) -> str:
        """Render as YAML preserving key order.

        Returns:
            YAML text.
        """
        data = self.model_dump(mode="json", exclude_defaults=False)
        for service in data["services"].values():
            for key in ("ports", "volumes", "environment"):
                if not service[key]:
                    del service[key]
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
