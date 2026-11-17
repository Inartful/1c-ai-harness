"""Rich rendering of command results."""

from rich.console import Console
from rich.table import Table

from harness.services.build import BuildStage
from harness.services.check import CheckReport
from harness.services.doctor import CheckResult, Status
from harness.services.writer import FileAction

_STYLE = {Status.OK: "green", Status.WARN: "yellow", Status.FAIL: "red"}


def render_checks(console: Console, results: list[CheckResult]) -> None:
    """Print doctor results as a table.

    Args:
        console: Target console.
        results: Check results.
    """
    table = Table(title="harness doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")
    table.add_column("Hint", overflow="fold")
    for r in results:
        table.add_row(r.name, f"[{_STYLE[r.status]}]{r.status.value}[/]", r.detail, r.hint)
    console.print(table)


def render_actions(console: Console, actions: list[FileAction], root_label: str) -> None:
    """Print file actions as a table.

    Args:
        console: Target console.
        actions: Recorded file actions.
        root_label: Title suffix, usually the workspace path.
    """
    table = Table(title=f"init: {root_label}")
    table.add_column("Action")
    table.add_column("Path", overflow="fold")
    for a in actions:
        table.add_row(a.action.value, str(a.path))
    console.print(table)


def render_stages(console: Console, title: str, stages: list[BuildStage]) -> None:
    """Print build stages with their durations.

    Args:
        console: Target console.
        title: Table title.
        stages: Finished stages.
    """
    table = Table(title=title)
    table.add_column("Stage")
    table.add_column("Time", justify="right")
    for stage in stages:
        table.add_row(stage.name, f"{stage.seconds:.1f} s")
    console.print(table)


def render_check(console: Console, report: CheckReport) -> None:
    """Print BSL LS diagnostics grouped by severity.

    Args:
        console: Target console.
        report: Analysis outcome.
    """
    table = Table(title="harness check")
    table.add_column("Severity")
    table.add_column("Count", justify="right")
    for name, count in sorted(report.counts.items()):
        table.add_row(name, str(count))
    table.add_row("[bold]total[/]", f"[bold]{report.total}[/]")
    console.print(table)
    console.print(f"{report.seconds:.1f} s; report: {report.report or 'none'}")
