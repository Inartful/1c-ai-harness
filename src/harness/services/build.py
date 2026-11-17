"""Implementation of `harness build`: the fast ibcmd compile pipeline."""

import shutil
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from harness.errors import ConfigError
from harness.system.platform import find_platform_dir
from harness.system.runner import CommandRunner, run

_IBMCD_NAME = "ibcmd.exe" if sys.platform == "win32" else "ibcmd"
_IMPORT_TIMEOUT_S = 3600.0
_SAVE_TIMEOUT_S = 1800.0


class BuildStage(BaseModel):
    """One external step of the build."""

    name: str
    seconds: float
    ok: bool


class BuildReport(BaseModel):
    """Outcome of `harness build`."""

    src: Path
    ib: Path
    out: Path | None = None
    stages: list[BuildStage] = Field(default_factory=list)
    artifact_mb: float | None = None


def find_ibcmd(explicit: Path | None = None) -> Path:
    """Locate the `ibcmd` executable.

    Args:
        explicit: Path passed by the user.

    Returns:
        Path to `ibcmd`.

    Raises:
        ConfigError: If `ibcmd` is missing from PATH and from the platform `bin`.
    """
    if explicit is not None:
        if not explicit.is_file():
            msg = f"ibcmd not found at {explicit}"
            raise ConfigError(msg)
        return explicit
    found = shutil.which("ibcmd")
    if found is not None:
        return Path(found)
    platform = find_platform_dir()
    if platform is not None:
        candidate = platform / "bin" / _IBMCD_NAME
        if candidate.is_file():
            return candidate
    msg = "ibcmd not found; install 1C:Enterprise 8.3.20+ or pass --ibcmd PATH"
    raise ConfigError(msg)


def build_workspace(
    root: Path,
    *,
    src: Path | None = None,
    ib: Path | None = None,
    out: Path | None = None,
    skip_import: bool = False,
    skip_save: bool = False,
    no_check: bool = False,
    ibcmd: Path | None = None,
    runner: CommandRunner = run,
) -> BuildReport:
    """Load the dumped configuration into a file infobase and save a `.cf`.

    Uses `ibcmd` directly: the designer pipeline behind `vrunner cf compile` is
    single-threaded and roughly five times slower on a 26k-file configuration.

    Args:
        root: Workspace root.
        src: Configuration dump directory; defaults to `<root>/src/cf`.
        ib: File infobase directory; defaults to `<root>/build/ib`.
        out: Target `.cf` path; defaults to `<root>/build/reports/<root>.cf`.
        skip_import: Do not load the sources (save the current base as is).
        skip_save: Do not write the `.cf` (import only).
        no_check: Pass `--no-check` to the import step.
        ibcmd: Explicit `ibcmd` path.
        runner: Command runner, injectable for tests.

    Returns:
        A report with one stage per external command.

    Raises:
        ConfigError: If the layout is wrong or `ibcmd` is missing.
    """
    workspace = root.resolve()
    source = (src or workspace / "src" / "cf").resolve()
    infobase = (ib or workspace / "build" / "ib").resolve()
    target = (out or workspace / "build" / "reports" / f"{workspace.name}.cf").resolve()
    if not source.is_dir():
        msg = f"configuration dump not found: {source}"
        raise ConfigError(msg)
    executable = find_ibcmd(ibcmd)
    report = BuildReport(src=source, ib=infobase, out=None if skip_save else target)
    if not skip_import:
        argv = [
            str(executable),
            "infobase",
            "config",
            "import",
            f"--db-path={infobase}",
            *(["--no-check"] if no_check else []),
            str(source),
        ]
        result = runner(argv, timeout_s=_IMPORT_TIMEOUT_S)
        report.stages.append(BuildStage(name="import", seconds=result.duration_s, ok=True))
    if not skip_save:
        target.parent.mkdir(parents=True, exist_ok=True)
        result = runner(
            [
                str(executable),
                "infobase",
                "config",
                "save",
                f"--db-path={infobase}",
                "--db",
                str(target),
            ],
            timeout_s=_SAVE_TIMEOUT_S,
        )
        report.stages.append(BuildStage(name="save", seconds=result.duration_s, ok=True))
        if target.is_file():
            report.artifact_mb = round(target.stat().st_size / (1024 * 1024), 1)
    return report
