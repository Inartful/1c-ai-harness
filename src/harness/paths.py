"""Path helpers: ASCII detection and stable project hashes."""

import hashlib
from pathlib import Path


def needs_ascii_link(path: Path) -> bool:
    """Tell whether a path contains non-ASCII characters or spaces.

    Args:
        path: Path to inspect.

    Returns:
        True if Docker volume mounts on Windows may break for this path.
    """
    text = str(path)
    return not text.isascii() or " " in text


def project_hash(path: Path) -> str:
    """Compute a short stable hash of a resolved path.

    Args:
        path: Project path.

    Returns:
        First 8 hex characters of the SHA-256 of the POSIX path.
    """
    return hashlib.sha256(path.resolve().as_posix().encode("utf-8")).hexdigest()[:8]
