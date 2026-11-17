"""Shared base model with strict validation and JSON rendering."""

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Immutable model that rejects unknown fields and accepts field names or aliases."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    def to_json(self) -> str:
        """Serialize to indented JSON using aliases and omitting unset optionals.

        Returns:
            JSON text terminated by a newline.
        """
        return self.model_dump_json(by_alias=True, indent=2, exclude_none=True) + "\n"
