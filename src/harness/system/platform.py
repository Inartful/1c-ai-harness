"""Discovery of the installed 1C platform and its `.hbk` help files."""

import os
import re
import sys
from pathlib import Path

_VERSION_RE = re.compile(r"^\d+(\.\d+){2,3}$")


def default_platform_roots() -> list[Path]:
    """List the standard directories that contain versioned 1C installs.

    Returns:
        Candidate root directories for the current OS.
    """
    if sys.platform == "win32":
        roots: list[Path] = []
        for var in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(var)
            if base:
                roots.append(Path(base) / "1cv8")
        return roots
    return [Path("/opt/1cv8/x86_64"), Path("/opt/1cv8/i386")]


def _version_key(name: str) -> tuple[int, ...]:
    """Convert a dotted version string to a sortable tuple."""
    return tuple(int(part) for part in name.split("."))


def find_platform_dir(roots: list[Path] | None = None) -> Path | None:
    """Find the newest installed platform version directory.

    Args:
        roots: Directories to scan; defaults to the standard locations.

    Returns:
        Path such as `C:/Program Files/1cv8/8.3.27.1606`, or None if nothing is found.
    """
    best: tuple[tuple[int, ...], Path] | None = None
    for root in roots if roots is not None else default_platform_roots():
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_dir() and _VERSION_RE.match(child.name):
                key = _version_key(child.name)
                if best is None or key > best[0]:
                    best = (key, child)
    return best[1] if best else None


def find_hbk_files(platform_dir: Path, limit: int = 50) -> list[Path]:
    """Collect `.hbk` help files under the platform `bin` directory.

    Args:
        platform_dir: Platform version directory.
        limit: Maximum number of files to return.

    Returns:
        Found help files, possibly empty.
    """
    bin_dir = platform_dir / "bin"
    if not bin_dir.is_dir():
        return []
    found: list[Path] = []
    for path in bin_dir.rglob("*.hbk"):
        found.append(path)
        if len(found) >= limit:
            break
    return found
