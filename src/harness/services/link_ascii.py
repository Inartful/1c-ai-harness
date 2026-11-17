"""Implementation of `harness link-ascii`."""

import sys
from pathlib import Path

from pydantic import BaseModel

from harness.errors import ConfigError
from harness.models.env import replace_env_value
from harness.paths import needs_ascii_link, project_hash
from harness.system.links import LinkResult, create_dir_link
from harness.system.runner import CommandRunner, run


class LinkAsciiReport(BaseModel):
    """Outcome of `link-ascii`."""

    needed: bool
    link: Path | None = None
    result: LinkResult | None = None
    dry_run: bool = False


def default_link_root() -> Path:
    """Return the default directory that holds ASCII links.

    Returns:
        `C:/bsl-src` on Windows, `~/.bsl-src` elsewhere.
    """
    return Path("C:/bsl-src") if sys.platform == "win32" else Path.home() / ".bsl-src"


def link_ascii(
    target_dir: Path,
    link_root: Path | None = None,
    *,
    dry_run: bool = False,
    runner: CommandRunner = run,
) -> LinkAsciiReport:
    """Create an ASCII-only link to `src/cf` and point `.env` SOURCE_PATH at it.

    Args:
        target_dir: Workspace root.
        link_root: Directory in which `<project_hash>` links are created.
        dry_run: Report the plan without changing anything.
        runner: Command runner, injectable for tests.

    Returns:
        A report; `needed` is False when the path is already Docker-safe.

    Raises:
        ConfigError: If `src/cf` or `.env` is missing (run `init` first).
        LinkError: If the link cannot be created safely.
    """
    root = target_dir.resolve()
    source = root / "src" / "cf"
    env_path = root / ".env"
    if not source.is_dir() or not env_path.is_file():
        msg = "src/cf or .env missing; run `harness init` first"
        raise ConfigError(msg)
    if not needs_ascii_link(source):
        return LinkAsciiReport(needed=False)
    link = (link_root or default_link_root()) / project_hash(root)
    if dry_run:
        return LinkAsciiReport(needed=True, link=link, dry_run=True)
    result = create_dir_link(link, source, runner=runner)
    updated = replace_env_value(
        env_path.read_text(encoding="utf-8"), "SOURCE_PATH", link.as_posix()
    )
    env_path.write_text(updated, encoding="utf-8", newline="\n")
    return LinkAsciiReport(needed=True, link=link, result=result)
