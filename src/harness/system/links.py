"""Cross-platform symlink and directory junction management."""

import os
import shutil
import sys
from enum import StrEnum
from pathlib import Path

from harness.errors import CommandFailedError, LinkError
from harness.system.runner import CommandRunner, run

_MKLINK_TIMEOUT_S = 15.0


class LinkResult(StrEnum):
    """Outcome of a link operation."""

    CREATED = "created"
    EXISTS = "exists"
    COPIED = "copied"


def create_dir_link(link: Path, target: Path, *, runner: CommandRunner = run) -> LinkResult:
    """Create a directory link pointing at `target`, never overwriting anything.

    On Windows a junction (`mklink /J`, no admin rights needed) is tried first, then
    `mklink /D`. Elsewhere a symlink is used.

    Args:
        link: Link location.
        target: Existing directory to point at.
        runner: Command runner, injectable for tests.

    Returns:
        CREATED, or EXISTS if the link already points at `target`.

    Raises:
        LinkError: If the target is missing, the link path is occupied by something else,
            or link creation fails.
    """
    if not target.is_dir():
        msg = f"link target is not a directory: {target}"
        raise LinkError(msg)
    if link.exists() or link.is_symlink():
        if link.resolve() == target.resolve():
            return LinkResult.EXISTS
        msg = f"{link} already exists and does not point to {target}"
        raise LinkError(msg)
    link.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        errors: list[str] = []
        for flag in ("/J", "/D"):
            try:
                runner(
                    ["cmd", "/c", "mklink", flag, str(link), str(target)],
                    timeout_s=_MKLINK_TIMEOUT_S,
                )
            except CommandFailedError as exc:
                errors.append(str(exc))
            else:
                return LinkResult.CREATED
        msg = f"cannot create link {link}: {'; '.join(errors)}"
        raise LinkError(msg)
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as exc:
        msg = f"cannot create symlink {link}: {exc}"
        raise LinkError(msg) from exc
    return LinkResult.CREATED


def create_file_link(link: Path, target: Path) -> LinkResult:
    """Link `link` to the sibling file `target`, falling back to a copy.

    Args:
        link: New link path.
        target: Existing file in the same directory.

    Returns:
        CREATED for a symlink, COPIED when symlinks are not permitted, EXISTS if `link`
        is already a symlink to `target`.

    Raises:
        LinkError: If `target` is missing or `link` is occupied by something else.
    """
    if not target.is_file():
        msg = f"link target is not a file: {target}"
        raise LinkError(msg)
    if link.is_symlink():
        if link.resolve() == target.resolve():
            return LinkResult.EXISTS
        msg = f"{link} is a symlink to something else"
        raise LinkError(msg)
    if link.exists():
        msg = f"{link} already exists"
        raise LinkError(msg)
    try:
        os.symlink(target.name, link)
    except OSError:
        shutil.copyfile(target, link)
        return LinkResult.COPIED
    return LinkResult.CREATED
