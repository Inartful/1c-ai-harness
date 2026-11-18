"""Safe execution layer for every external CLI invocation."""

import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from harness.errors import CommandFailedError, CommandTimeoutError, ToolNotFoundError


class CommandResult(BaseModel):
    """Structured result of a finished command."""

    model_config = ConfigDict(frozen=True)

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float


class CommandRunner(Protocol):
    """Callable signature shared by `run` and test doubles."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout_s: float,
        cwd: Path | None = None,
        check: bool = True,
        input_text: str | None = None,
    ) -> CommandResult:
        """Run a command and return its result."""
        ...


def run(
    argv: Sequence[str],
    *,
    timeout_s: float,
    cwd: Path | None = None,
    check: bool = True,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
) -> CommandResult:
    """Run an external command without a shell.

    Args:
        argv: Program and arguments. The program is resolved via PATH.
        timeout_s: Mandatory timeout in seconds; the process is killed on expiry.
        cwd: Working directory.
        check: Raise on a non-zero return code.
        env: Full replacement environment, or None to inherit.
        input_text: Text fed to stdin (keeps secrets out of argv); stdin is closed when None.

    Returns:
        The captured result.

    Raises:
        ValueError: If `argv` is empty or `timeout_s` is not positive.
        ToolNotFoundError: If the program is not found.
        CommandTimeoutError: If the timeout expires.
        CommandFailedError: If `check` is set and the return code is non-zero.
    """
    if not argv:
        msg = "argv must not be empty"
        raise ValueError(msg)
    if timeout_s <= 0:
        msg = "timeout_s must be positive"
        raise ValueError(msg)
    program = shutil.which(argv[0])
    if program is None:
        msg = f"executable not found on PATH: {argv[0]}"
        raise ToolNotFoundError(msg)
    full = (program, *argv[1:])
    start = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603 - shell=False, argv list, resolved executable
            full,
            capture_output=True,
            input=input_text,
            stdin=None if input_text is not None else subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"command timed out after {timeout_s}s: {' '.join(argv)}"
        raise CommandTimeoutError(msg) from exc
    except OSError as exc:
        msg = f"cannot execute {argv[0]}: {exc}"
        raise CommandFailedError(msg) from exc
    result = CommandResult(
        argv=full,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        duration_s=time.monotonic() - start,
    )
    if check and result.returncode != 0:
        tail = result.stderr.strip()[-500:]
        msg = f"command failed with code {result.returncode}: {' '.join(argv)}\n{tail}"
        raise CommandFailedError(msg)
    return result
