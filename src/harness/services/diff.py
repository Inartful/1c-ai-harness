"""Compare two XML/BSL dumps (pristine vs. modified) file by file."""

import difflib
import hashlib
from pathlib import Path

from pydantic import BaseModel, Field

from harness.errors import ConfigError

# Written by the platform on every export; never meaningful for a change set.
IGNORED = frozenset({"ConfigDumpInfo.xml"})


class TreeDiff(BaseModel):
    """Relative POSIX paths that were added, modified or deleted."""

    added: list[str] = Field(default_factory=list)
    modified: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)

    @property
    def changed(self) -> list[str]:
        """Added plus modified paths (what a partial import has to load)."""
        return sorted([*self.added, *self.modified])

    @property
    def empty(self) -> bool:
        """Whether the trees are identical."""
        return not (self.added or self.modified or self.deleted)


def _files(root: Path) -> dict[str, Path]:
    return {
        p.relative_to(root).as_posix(): p
        for p in root.rglob("*")
        if p.is_file() and p.name not in IGNORED
    }


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def diff_trees(base: Path, current: Path) -> TreeDiff:
    """Compare two dump directories.

    Args:
        base: Pristine dump.
        current: Modified dump.

    Returns:
        Added, modified and deleted relative paths.

    Raises:
        ConfigError: If either directory is missing.
    """
    for root in (base, current):
        if not root.is_dir():
            msg = f"directory not found: {root}"
            raise ConfigError(msg)
    old = _files(base.resolve())
    new = _files(current.resolve())
    result = TreeDiff()
    for rel in sorted(new):
        if rel not in old:
            result.added.append(rel)
        elif old[rel].stat().st_size != new[rel].stat().st_size or _digest(old[rel]) != _digest(
            new[rel]
        ):
            result.modified.append(rel)
    result.deleted = sorted(rel for rel in old if rel not in new)
    return result


def unified_patch(base: Path, current: Path, rel: str, *, context: int = 3) -> str:
    """Return a unified diff of one text file (missing side treated as empty)."""

    def read(path: Path) -> list[str]:
        if not path.is_file():
            return []
        return path.read_text(encoding="utf-8-sig", errors="replace").splitlines(keepends=True)

    return "".join(
        difflib.unified_diff(
            read(base / rel), read(current / rel), f"a/{rel}", f"b/{rel}", n=context
        )
    )
