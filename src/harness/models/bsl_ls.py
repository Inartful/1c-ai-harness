"""Schema for `.bsl-language-server.json`."""

from typing import Literal

from pydantic import Field

from harness.models.base import StrictModel

SCHEMA_URL = (
    "https://1c-syntax.github.io/bsl-language-server/configuration/schema.json"  # PROVISIONAL
)


class DiagnosticsConfig(StrictModel):
    """Diagnostics section of the BSL Language Server config."""

    parameters: dict[str, bool | dict[str, str | int | bool]] = Field(default_factory=dict)


class BslLanguageServerConfig(StrictModel):
    """BSL Language Server configuration with Russian diagnostics by default."""

    schema_url: str = Field(default=SCHEMA_URL, alias="$schema")
    language: Literal["ru", "en"] = "ru"
    diagnostics: DiagnosticsConfig = Field(default_factory=DiagnosticsConfig)
