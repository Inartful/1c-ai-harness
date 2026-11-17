"""Implementation of `harness fix-permissions` (Windows only)."""

import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from harness.errors import ConfigError, HarnessError
from harness.system.runner import CommandRunner, run

_ICACLS_TIMEOUT_S = 900.0


class StepResult(BaseModel):
    """One `icacls` invocation."""

    name: str
    argv: tuple[str, ...]
    ok: bool


class FixReport(BaseModel):
    """Outcome of `fix-permissions`."""

    root: Path
    dry_run: bool = False
    steps: list[StepResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _current_user() -> str:
    r"""Return `DOMAIN\user` for the signed-in account.

    Returns:
        The account name used in the ACL grant.
    """
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    return f"{domain}\\{user}" if domain and user else user


def _steps(target: Path, user: str) -> list[tuple[str, list[str]]]:
    """Build the repair plan.

    Args:
        target: Workspace root.
        user: Account that receives full control.

    Returns:
        Named `icacls` argument lists, applied in order.
    """
    root = str(target)
    return [
        ("grant owner full control", ["icacls", root, "/grant:r", f"{user}:(OI)(CI)F"]),
        ("re-enable inheritance", ["icacls", root, "/inheritance:e", "/T"]),
        ("apply low integrity label", ["icacls", root, "/setintegritylevel", "(OI)(CI)L", "/T"]),
    ]


def fix_permissions(
    root: Path,
    *,
    dry_run: bool = False,
    runner: CommandRunner = run,
) -> FixReport:
    """Repair a moved workspace so the sandboxed agent can write to it.

    A tree copied from another location keeps stale inherited ACEs and often loses
    the low integrity label, so a low-integrity sandbox cannot write into it even
    when the root itself was repaired. This re-grants the owner, re-enables
    inheritance for the whole tree and re-applies the low label.

    Args:
        root: Workspace root.
        dry_run: Report the plan without changing any ACL.
        runner: Command runner, injectable for tests.

    Returns:
        A report with one step per `icacls` call.

    Raises:
        ConfigError: On non-Windows platforms or when `root` is not a directory.
        HarnessError: If a step fails; remaining steps are skipped.
    """
    if sys.platform != "win32":
        msg = "fix-permissions is only needed on Windows"
        raise ConfigError(msg)
    target = root.resolve()
    if not target.is_dir():
        msg = f"not a directory: {target}"
        raise ConfigError(msg)
    report = FixReport(root=target, dry_run=dry_run)
    user = _current_user()
    if not user:
        msg = "cannot determine the current user for the ACL grant"
        raise ConfigError(msg)
    for name, argv in _steps(target, user):
        if dry_run:
            report.steps.append(StepResult(name=name, argv=tuple(argv), ok=True))
            continue
        try:
            runner(argv, timeout_s=_ICACLS_TIMEOUT_S)
        except HarnessError as exc:
            report.steps.append(StepResult(name=name, argv=tuple(argv), ok=False))
            report.warnings.append(f"{name}: {exc}")
            break
        report.steps.append(StepResult(name=name, argv=tuple(argv), ok=True))
    return report
