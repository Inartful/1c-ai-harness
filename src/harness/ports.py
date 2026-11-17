"""Deterministic IPC port allocation derived from the workspace path."""

import hashlib
import socket
from collections.abc import Callable
from pathlib import Path

from harness.errors import PortAllocationError

BASE_PORT = 40241
PORT_SPAN = 760


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """Check whether a TCP port can be bound locally.

    Args:
        port: Port to probe.
        host: Interface to bind.

    Returns:
        True if the bind succeeded.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def hash_start_port(workspace: Path) -> int:
    """Map a workspace path to its preferred starting port.

    Args:
        workspace: Workspace root; resolved before hashing.

    Returns:
        A port in `[BASE_PORT, BASE_PORT + PORT_SPAN)`.
    """
    digest = hashlib.sha256(workspace.resolve().as_posix().encode("utf-8")).hexdigest()
    return BASE_PORT + int(digest, 16) % PORT_SPAN


def allocate_ipc_port(
    workspace: Path,
    *,
    is_free: Callable[[int], bool] = is_port_free,
    reuse: int | None = None,
) -> int:
    """Allocate a stable, free IPC port for a workspace.

    Args:
        workspace: Workspace root.
        is_free: Probe function, injectable for tests.
        reuse: Previously recorded port; kept if still valid and free.

    Returns:
        The allocated port.

    Raises:
        PortAllocationError: If every port in the range is busy.
    """
    if reuse is not None and BASE_PORT <= reuse < BASE_PORT + PORT_SPAN and is_free(reuse):
        return reuse
    start = hash_start_port(workspace)
    for offset in range(PORT_SPAN):
        port = BASE_PORT + (start - BASE_PORT + offset) % PORT_SPAN
        if is_free(port):
            return port
    msg = f"no free port in {BASE_PORT}..{BASE_PORT + PORT_SPAN - 1}"
    raise PortAllocationError(msg)
