"""Implementation of `harness decompile`: export a `.cf` into XML sources via `ibcmd`."""

import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from harness.errors import ConfigError
from harness.services.build import BuildStage, find_ibcmd
from harness.system.runner import CommandRunner, run

_LOAD_TIMEOUT_S = 3600.0
_EXPORT_TIMEOUT_S = 3600.0
_CREATE_TIMEOUT_S = 300.0


class DecompileReport(BaseModel):
    """Outcome of `harness decompile`."""

    cf: Path
    out: Path
    ib: Path | None = None
    stages: list[BuildStage] = Field(default_factory=list)


def decompile_cf(
    cf: Path,
    out: Path,
    *,
    ib: Path | None = None,
    ibcmd: Path | None = None,
    runner: CommandRunner = run,
) -> DecompileReport:
    """Decompile a `.cf` file into XML/BSL sources using `ibcmd`.

    Args:
        cf: Source `.cf` file.
        out: Target directory for the exported configuration XML tree.
        ib: Optional persistent file infobase directory; uses a temp dir when omitted.
        ibcmd: Explicit `ibcmd` executable path.
        runner: Command runner, injectable for tests.

    Returns:
        A report with one stage per `ibcmd` invocation.

    Raises:
        ConfigError: If `cf` or `ibcmd` is missing.
    """
    source_cf = cf.resolve()
    if not source_cf.is_file():
        msg = f"configuration file not found: {source_cf}"
        raise ConfigError(msg)
    target_out = out.resolve()
    executable = find_ibcmd(ibcmd)
    stages: list[BuildStage] = []

    def _run_pipeline(infobase: Path) -> None:
        if not (infobase / "1Cv8.1CD").is_file():
            res = runner(
                [str(executable), "infobase", "create", f"--db-path={infobase}"],
                timeout_s=_CREATE_TIMEOUT_S,
            )
            stages.append(BuildStage(name="create-ib", seconds=res.duration_s, ok=True))
        res = runner(
            [
                str(executable),
                "infobase",
                "config",
                "load",
                f"--db-path={infobase}",
                "--force",
                str(source_cf),
            ],
            timeout_s=_LOAD_TIMEOUT_S,
        )
        stages.append(BuildStage(name="load-cf", seconds=res.duration_s, ok=True))
        target_out.mkdir(parents=True, exist_ok=True)
        res = runner(
            [
                str(executable),
                "infobase",
                "config",
                "export",
                f"--db-path={infobase}",
                str(target_out),
            ],
            timeout_s=_EXPORT_TIMEOUT_S,
        )
        stages.append(BuildStage(name="export-xml", seconds=res.duration_s, ok=True))

    if ib is not None:
        infobase = ib.resolve()
        infobase.mkdir(parents=True, exist_ok=True)
        _run_pipeline(infobase)
        return DecompileReport(cf=source_cf, out=target_out, ib=infobase, stages=stages)

    with tempfile.TemporaryDirectory(prefix="harness-ib-") as tmp_dir:
        _run_pipeline(Path(tmp_dir))
    return DecompileReport(cf=source_cf, out=target_out, ib=None, stages=stages)
