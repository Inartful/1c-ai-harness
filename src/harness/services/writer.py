"""Idempotent file writer with dry-run and force semantics."""

import os
import tempfile
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class WriteAction(StrEnum):
    """What happened to a file."""

    WRITTEN = "written"
    UPDATED = "updated"
    SKIPPED = "skipped"
    UNCHANGED = "unchanged"
    PLANNED = "planned"


class FileAction(BaseModel):
    """A recorded file operation."""

    model_config = ConfigDict(frozen=True)

    path: Path
    action: WriteAction


class FileWriter:
    """Writes text files atomically and records every action."""

    def __init__(self, *, force: bool = False, dry_run: bool = False) -> None:
        """Create a writer.

        Args:
            force: Overwrite existing files whose content differs.
            dry_run: Record actions without touching the disk.
        """
        self._force = force
        self._dry_run = dry_run
        self.actions: list[FileAction] = []

    def write(self, path: Path, content: str) -> WriteAction:
        """Write `content` to `path` according to the writer policy.

        Args:
            path: Destination file.
            content: UTF-8 text.

        Returns:
            The action taken.
        """
        exists = path.exists()
        if exists and path.read_text(encoding="utf-8") == content:
            return self._record(path, WriteAction.UNCHANGED)
        if exists and not self._force:
            return self._record(path, WriteAction.SKIPPED)
        if self._dry_run:
            return self._record(path, WriteAction.PLANNED)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".harness-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            Path(tmp_name).replace(path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return self._record(path, WriteAction.UPDATED if exists else WriteAction.WRITTEN)

    def merge(self, path: Path, content: str) -> WriteAction:
        """Write a file that is merged by the caller, so it is always updatable.

        Args:
            path: Destination file.
            content: Final merged content.

        Returns:
            The action taken.
        """
        saved = self._force
        self._force = True
        try:
            return self.write(path, content)
        finally:
            self._force = saved

    @property
    def dry_run(self) -> bool:
        """Whether the writer avoids touching the disk."""
        return self._dry_run

    def _record(self, path: Path, action: WriteAction) -> WriteAction:
        """Append an action to the log."""
        self.actions.append(FileAction(path=path, action=action))
        return action
