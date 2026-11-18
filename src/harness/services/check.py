"""Implementation of `harness check`: BSL Language Server analysis."""

import json
import re
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from pydantic import BaseModel, Field

from harness.errors import ConfigError
from harness.services.diff import diff_trees
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
        # bsl-ls relativizes paths against cwd and crashes when it is on another drive.
        cwd=source,
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


class Diagnostic(BaseModel):
    """One BSL LS diagnostic with a path relative to the analyzed root."""

    path: str
    line: int
    code: str
    severity: str
    message: str


class ChangedCheckReport(BaseModel):
    """Outcome of `harness check --changed-from`."""

    files: list[str] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    seconds: float = 0.0
    new_only: bool = True

    def counts(self) -> dict[str, int]:
        """Diagnostics per severity."""
        return dict(Counter(d.severity for d in self.diagnostics))


def _uri_to_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return Path(uri)
    return Path(url2pathname(unquote(parsed.path)))


def parse_report(report: Path, root: Path) -> list[Diagnostic]:
    """Parse a BSL LS `json` reporter file into diagnostics relative to `root`.

    Args:
        report: Report written by `-r json`.
        root: Analyzed source root.

    Returns:
        Diagnostics sorted by path and line.
    """
    data = json.loads(report.read_text(encoding="utf-8"))
    base = root.resolve()
    found: list[Diagnostic] = []
    for info in data.get("fileinfos", []):
        path = _uri_to_path(str(info.get("path", "")))
        try:
            rel = path.resolve().relative_to(base).as_posix()
        except ValueError:
            rel = path.as_posix()
        for diag in info.get("diagnostics", []):
            found.append(
                Diagnostic(
                    path=rel,
                    line=int(diag.get("range", {}).get("start", {}).get("line", 0)) + 1,
                    code=str(diag.get("code", "")),
                    severity=str(diag.get("severity", "")),
                    message=str(diag.get("message", "")),
                )
            )
    return sorted(found, key=lambda d: (d.path, d.line, d.code))


def _analyze(
    executable: Path, root: Path, config: Path | None, runner: CommandRunner
) -> list[Diagnostic]:
    out = root.parent / f"{root.name}-report"
    out.mkdir(parents=True, exist_ok=True)
    argv = [str(executable), "analyze", "-s", str(root), "-o", str(out), "-r", "json", "--silent"]
    if config is not None:
        argv[4:4] = ["-c", str(config)]
    runner(argv, timeout_s=_ANALYZE_TIMEOUT_S, cwd=root)
    reports = sorted(out.glob("*.json"))
    return parse_report(reports[-1], root) if reports else []


def run_check_changed(
    src: Path,
    changed_from: Path,
    *,
    config: Path | None = None,
    bsl: Path | None = None,
    new_only: bool = True,
    runner: CommandRunner = run,
) -> ChangedCheckReport:
    """Analyze only `.bsl` files that differ from a pristine dump.

    With `new_only`, diagnostics already present in the pristine version of the same file
    (same code and message) are dropped, so a vendor configuration's backlog does not hide
    what the change introduced.

    Args:
        src: Modified dump.
        changed_from: Pristine dump.
        config: BSL LS configuration file.
        bsl: Explicit executable path.
        new_only: Report only diagnostics introduced by the change.
        runner: Command runner.

    Returns:
        The report.
    """
    executable = find_bsl(bsl)
    delta = diff_trees(changed_from, src)
    files = [rel for rel in delta.changed if rel.lower().endswith(".bsl")]
    report = ChangedCheckReport(files=files, new_only=new_only)
    if not files:
        return report
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="harness-check-") as tmp:
        new_root = Path(tmp) / "new"
        old_root = Path(tmp) / "old"
        old_present = False
        for rel in files:
            target = new_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, target)
            previous = changed_from / rel
            if new_only and previous.is_file():
                old_target = old_root / rel
                old_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(previous, old_target)
                old_present = True
        current = _analyze(executable, new_root, config, runner)
        if new_only and old_present:
            baseline = Counter(
                (d.path, d.code, d.message) for d in _analyze(executable, old_root, config, runner)
            )
            fresh: list[Diagnostic] = []
            for diag in current:
                key = (diag.path, diag.code, diag.message)
                if baseline[key] > 0:
                    baseline[key] -= 1
                else:
                    fresh.append(diag)
            current = fresh
    report.diagnostics = current
    report.seconds = round(time.monotonic() - started, 1)
    return report
