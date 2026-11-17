"""Implementation of `harness check`: BSL Language Server analysis."""

import re
import shutil
import time
from pathlib import Path

from pydantic import BaseModel, Field

from harness.errors import ConfigError
from harness.system.runner import CommandRunner, run

_ANALYZE_TIMEOUT_S = 3600.0
_CHUNK_BYTES = 4 * 1024 * 1024
_TAIL_CHARS = 256
_SEVERITY_RE = re.compile(r'"severity"\s*:\s*"([A-Za-z]+)"')


class CheckReport(BaseModel):
    """Outcome of `harness check`."""

    src: Path
    report_dir: Path
    seconds: float
    counts: dict[str, int] = Field(default_factory=dict)
    total: int = 0
    report: Path | None = None


def find_bsl(explicit: Path | None = None) -> Path:
    """Locate the `bsl-language-server` executable.

    Args:
        explicit: Path passed by the user.

    Returns:
        Path to the language server.

    Raises:
        ConfigError: If it is not on PATH.
    """
    if explicit is not None:
        if not explicit.is_file():
            msg = f"bsl-language-server not found at {explicit}"
            raise ConfigError(msg)
        return explicit
    found = shutil.which("bsl-language-server")
    if found is None:
        msg = "bsl-language-server not found on PATH"
        raise ConfigError(msg)
    return Path(found)


def count_severities(report: Path) -> dict[str, int]:
    """Count `"severity"` values in a BSL LS JSON report without loading it whole.

    Args:
        report: JSON report written by the `json` reporter.

    Returns:
        Severity name to number of diagnostics.
    """
    counts: dict[str, int] = {}
    tail = ""
    with report.open("r", encoding="utf-8", errors="replace") as handle:
        while True:
            chunk = handle.read(_CHUNK_BYTES)
            if not chunk:
                break
            data = tail + chunk
            limit = len(data) - _TAIL_CHARS
            for match in _SEVERITY_RE.finditer(data):
                if match.end() > limit:
                    break
                name = match.group(1)
                counts[name] = counts.get(name, 0) + 1
            tail = data[max(limit, 0) :]
    for match in _SEVERITY_RE.finditer(tail):
        name = match.group(1)
        counts[name] = counts.get(name, 0) + 1
    return counts


def run_check(
    root: Path,
    *,
    src: Path | None = None,
    config: Path | None = None,
    report_dir: Path | None = None,
    bsl: Path | None = None,
    runner: CommandRunner = run,
) -> CheckReport:
    """Analyze the configuration dump with BSL Language Server.

    Args:
        root: Workspace root.
        src: Source directory; defaults to `<root>/src/cf`.
        config: Language server config; defaults to `<root>/.bsl-language-server.json`.
        report_dir: Report directory; defaults to `<root>/build/reports/bsl`.
        bsl: Explicit executable path.
        runner: Command runner, injectable for tests.

    Returns:
        A report with the elapsed time, severity counts and the report path.

    Raises:
        ConfigError: If the sources or the executable are missing.
    """
    workspace = root.resolve()
    source = (src or workspace / "src" / "cf").resolve()
    settings = (config or workspace / ".bsl-language-server.json").resolve()
    output = (report_dir or workspace / "build" / "reports" / "bsl").resolve()
    if not source.is_dir():
        msg = f"configuration dump not found: {source}"
        raise ConfigError(msg)
    executable = find_bsl(bsl)
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    runner(
        [
            str(executable),
            "analyze",
            "-s",
            str(source),
            "-c",
            str(settings),
            "-o",
            str(output),
            "-r",
            "json",
            "--silent",
        ],
        timeout_s=_ANALYZE_TIMEOUT_S,
    )
    elapsed = time.monotonic() - started
    reports = sorted(output.glob("*.json"))
    report = reports[-1] if reports else None
    counts = count_severities(report) if report is not None else {}
    return CheckReport(
        src=source,
        report_dir=output,
        seconds=round(elapsed, 1),
        counts=counts,
        total=sum(counts.values()),
        report=report,
    )
