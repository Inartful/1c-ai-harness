"""Real platform compilation check: `1cv8 DESIGNER /CheckModules`.

BSL Language Server is a linter; this is the compiler. It catches what `ibcmd config check`
does not (that one validates metadata only). Note: neither catches an extension annotation
pointing at a non-existent base method; `harness ext lint` covers that.
"""

import re
from pathlib import Path

from pydantic import BaseModel, Field

from harness.errors import CommandFailedError
from harness.services.onec import Credentials, Designer
from harness.system.runner import CommandRunner, run

DEFAULT_MODES = ("ThinClient", "WebClient", "Server", "ExternalConnection")
ALL_MODES = (
    *DEFAULT_MODES,
    "ThickClientManagedApplication",
    "ThickClientOrdinaryApplication",
    "ThickClientServerOrdinaryApplication",
    "MobileAppClient",
    "MobileAppServer",
)
_LOCATION_RE = re.compile(r"^\{(?P<module>[^}(]+)\((?P<line>\d+),(?P<col>\d+)\)\}:\s*(?P<msg>.*)$")
_MODE_RE = re.compile(r"\((?:Проверка|Check):\s*([^)]+)\)\s*$")
_CLEAN_MARKERS = ("Синтаксических ошибок не обнаружено", "No syntax errors detected")


class CompileError(BaseModel):
    """One compiler diagnostic, merged across client/server modes."""

    module: str
    line: int
    column: int
    message: str
    context: str = ""
    modes: list[str] = Field(default_factory=list)


class CompileReport(BaseModel):
    """Outcome of `harness compile`."""

    ok: bool
    errors: list[CompileError] = Field(default_factory=list)
    seconds: float = 0.0
    raw: str = ""


def parse_check_modules_log(log: str) -> list[CompileError]:
    """Parse a `/CheckModules` log into deduplicated errors.

    Args:
        log: Designer `/Out` log text.

    Returns:
        Errors in log order; the same location reported for several modes is merged.
    """
    merged: dict[tuple[str, int, int, str], CompileError] = {}
    current: CompileError | None = None
    for raw_line in log.splitlines():
        match = _LOCATION_RE.match(raw_line.strip())
        if match is not None:
            msg = match.group("msg").strip()
            mode_match = _MODE_RE.search(msg)
            modes = [mode_match.group(1).strip()] if mode_match else []
            if mode_match:
                msg = msg[: mode_match.start()].strip()
            key = (match.group("module"), int(match.group("line")), int(match.group("col")), msg)
            current = merged.get(key)
            if current is None:
                current = CompileError(
                    module=key[0], line=key[1], column=key[2], message=msg, modes=modes
                )
                merged[key] = current
            else:
                current.modes.extend(m for m in modes if m not in current.modes)
            continue
        if current is not None and raw_line.startswith(("\t", " ")) and raw_line.strip():
            text = raw_line.strip()
            mode_match = _MODE_RE.search(text)
            if mode_match:
                mode = mode_match.group(1).strip()
                if mode not in current.modes:
                    current.modes.append(mode)
                text = text[: mode_match.start()].strip()
            if not current.context:
                current.context = text
    return list(merged.values())


def compile_check(
    ib: Path,
    *,
    extension: str | None = None,
    modes: tuple[str, ...] = DEFAULT_MODES,
    credentials: Credentials | None = None,
    designer: Path | None = None,
    runner: CommandRunner = run,
) -> CompileReport:
    """Compile all modules of the infobase configuration (or one extension).

    Args:
        ib: File infobase that holds the configuration to check.
        extension: Extension name to check instead of the main configuration.
        modes: Execution contexts (`ThinClient`, `Server`, ...).
        credentials: Infobase user.
        designer: Explicit `1cv8` path.
        runner: Command runner.

    Returns:
        Parsed errors; `ok` is False when any were found.

    Raises:
        CommandFailedError: If designer failed for a reason other than compile errors.
    """
    tool = Designer(ib, executable=designer, credentials=credentials, runner=runner)
    args = ["/CheckModules", *(f"-{m}" for m in modes)]
    if extension:
        args += ["-Extension", extension]
    res = tool.run(args)
    errors = parse_check_modules_log(res.log)
    clean = any(marker in res.log for marker in _CLEAN_MARKERS)
    if res.returncode != 0 and not errors:
        msg = f"designer /CheckModules failed (code {res.returncode}):\n{res.log.strip()}"
        raise CommandFailedError(msg)
    return CompileReport(
        ok=not errors and (clean or res.returncode == 0),
        errors=errors,
        seconds=round(res.seconds, 1),
        raw=res.log,
    )
