"""Typer CLI entry point."""

import json
import sys
from pathlib import Path
from typing import Annotated, Literal

import typer
from rich.console import Console

from harness.errors import HarnessError
from harness.report import render_actions, render_check, render_checks, render_stages
from harness.services.build import build_workspace
from harness.services.check import run_check
from harness.services.decompile import decompile_cf
from harness.services.doctor import has_failures, run_doctor
from harness.services.fix_permissions import fix_permissions
from harness.services.init import InitOptions, init_workspace
from harness.services.link_ascii import link_ascii
from harness.services.query import run_query
from harness.services.rules import pack_rules, unpack_rules
from harness.services.setup_skills import SkillSource, setup_skills


def _configure_streams() -> None:
    """Force UTF-8 on Windows so Cyrillic paths survive pipes and redirects."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


_configure_streams()

app = typer.Typer(
    name="harness",
    help="Bootstrap, configure and validate 1C AI agent workspaces.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)

TargetDir = Annotated[Path, typer.Argument(help="Workspace root directory.")]


def _fail(exc: HarnessError) -> typer.Exit:
    """Print an error panel and build the exit exception."""
    err_console.print(f"[red]error:[/] {exc}")
    return typer.Exit(code=1)


@app.command()
def init(
    target_dir: TargetDir,
    force: Annotated[bool, typer.Option(help="Overwrite existing generated files.")] = False,
    dry_run: Annotated[bool, typer.Option(help="Show the plan without writing.")] = False,
    ide: Annotated[bool, typer.Option(help="Add the mcp-1c-platform-tools server.")] = False,
    platform_tools_js: Annotated[
        Path | None, typer.Option(help="Path to the extension's out/src/index.js.")
    ] = None,
    platform_dir: Annotated[Path | None, typer.Option(help="1C platform directory.")] = None,
    ipc_port: Annotated[int | None, typer.Option(help="Override the IPC port.")] = None,
    mcp_python: Annotated[
        Path | None,
        typer.Option(help="Python interpreter with mcp_bsl_context installed (MCP help server)."),
    ] = None,
    indexing_mode: Annotated[
        Literal["fast", "full"],
        typer.Option(help="Atlas indexing mode: fast (SQLite) or full (embeddings)."),
    ] = "fast",
) -> None:
    """Create the workspace tree and generate all configs."""
    try:
        report = init_workspace(
            InitOptions(
                target_dir=target_dir,
                force=force,
                dry_run=dry_run,
                ide=ide,
                platform_tools_js=platform_tools_js,
                platform_dir=platform_dir,
                ipc_port=ipc_port,
                mcp_python=mcp_python,
                indexing_mode=indexing_mode,
            )
        )
    except HarnessError as exc:
        raise _fail(exc) from exc
    render_actions(console, report.actions, str(target_dir))
    console.print(f"IPC port: [bold]{report.ipc_port}[/]")
    for warning in report.warnings:
        console.print(f"[yellow]warning:[/] {warning}")


@app.command("link-ascii")
def link_ascii_cmd(
    target_dir: TargetDir,
    link_root: Annotated[Path | None, typer.Option(help="Directory for ASCII links.")] = None,
    dry_run: Annotated[bool, typer.Option(help="Show the plan without changes.")] = False,
) -> None:
    """Create an ASCII-safe junction/symlink to src/cf and update .env."""
    try:
        report = link_ascii(target_dir, link_root, dry_run=dry_run)
    except HarnessError as exc:
        raise _fail(exc) from exc
    if not report.needed:
        console.print("path is already ASCII-safe; nothing to do")
    elif report.dry_run:
        console.print(f"would link {report.link}")
    else:
        console.print(f"{report.result.value if report.result else ''}: {report.link}")


@app.command("fix-permissions")
def fix_permissions_cmd(
    target_dir: TargetDir,
    dry_run: Annotated[bool, typer.Option(help="Show the plan without changing ACLs.")] = False,
) -> None:
    """Repair ACLs and low-integrity labels of a moved workspace (Windows sandbox workaround)."""
    try:
        report = fix_permissions(target_dir, dry_run=dry_run)
    except HarnessError as exc:
        raise _fail(exc) from exc
    for step in report.steps:
        state = "would run" if report.dry_run else ("ok" if step.ok else "failed")
        console.print(f"{state}: {step.name}")
    for warning in report.warnings:
        console.print(f"[yellow]warning:[/] {warning}")
    if report.warnings:
        raise typer.Exit(code=1)


@app.command("setup-skills")
def setup_skills_cmd(
    target_dir: TargetDir,
    source: Annotated[SkillSource, typer.Option(help="Skill library.")] = SkillSource.DESKO77,
    local_path: Annotated[Path | None, typer.Option(help="Existing local checkout.")] = None,
    ref: Annotated[str | None, typer.Option(help="Git branch or tag.")] = None,
    update: Annotated[bool, typer.Option(help="Pull if already cloned.")] = False,
) -> None:
    """Fetch skills into .agents/skills and link them for Claude and Cursor."""
    try:
        report = setup_skills(target_dir, source, local_path=local_path, ref=ref, update=update)
    except HarnessError as exc:
        raise _fail(exc) from exc
    for name, result in report.links.items():
        console.print(f"{result.value}: {name}")
    for note in report.notes:
        console.print(note)


@app.command()
def doctor(
    target_dir: TargetDir,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
    strict: Annotated[bool, typer.Option(help="Treat warnings as failures.")] = False,
) -> None:
    """Verify tools, platform help files, Docker and ports."""
    results = run_doctor(target_dir)
    if json_output:
        typer.echo(json.dumps([r.model_dump(mode="json") for r in results], indent=2))
    else:
        render_checks(console, results)
    if has_failures(results, strict=strict):
        raise typer.Exit(code=1)


@app.command()
def build(
    target_dir: TargetDir,
    src: Annotated[Path | None, typer.Option(help="Configuration dump directory.")] = None,
    ib: Annotated[Path | None, typer.Option(help="File infobase directory.")] = None,
    out: Annotated[Path | None, typer.Option(help="Target .cf file.")] = None,
    skip_import: Annotated[
        bool, typer.Option(help="Save the current infobase without loading sources.")
    ] = False,
    skip_save: Annotated[bool, typer.Option(help="Load sources without writing the .cf.")] = False,
    no_check: Annotated[
        bool, typer.Option(help="Pass --no-check to the import (skip metadata check).")
    ] = False,
    ibcmd: Annotated[Path | None, typer.Option(help="Path to ibcmd.")] = None,
) -> None:
    """Load the dump into the infobase and write a .cf via ibcmd (fast path)."""
    try:
        report = build_workspace(
            target_dir,
            src=src,
            ib=ib,
            out=out,
            skip_import=skip_import,
            skip_save=skip_save,
            no_check=no_check,
            ibcmd=ibcmd,
        )
    except HarnessError as exc:
        raise _fail(exc) from exc
    render_stages(console, f"build: {target_dir}", report.stages)
    if report.out is not None:
        size = f"{report.artifact_mb} MB" if report.artifact_mb is not None else "missing"
        console.print(f"artifact: [bold]{report.out}[/] ({size})")


@app.command()
def check(
    target_dir: TargetDir,
    src: Annotated[Path | None, typer.Option(help="Source directory.")] = None,
    config: Annotated[Path | None, typer.Option(help="BSL LS configuration file.")] = None,
    report_dir: Annotated[Path | None, typer.Option(help="Report directory.")] = None,
    bsl: Annotated[Path | None, typer.Option(help="Path to bsl-language-server.")] = None,
    fail_on_error: Annotated[
        bool, typer.Option(help="Exit 1 when at least one Error diagnostic is found.")
    ] = False,
) -> None:
    """Analyze sources with BSL Language Server (fast syntax gate)."""
    try:
        report = run_check(target_dir, src=src, config=config, report_dir=report_dir, bsl=bsl)
    except HarnessError as exc:
        raise _fail(exc) from exc
    render_check(console, report)
    if fail_on_error and report.counts.get("Error", 0):
        raise typer.Exit(code=1)


@app.command()
def decompile(
    cf: Annotated[Path, typer.Argument(help="Source .cf file.")],
    out: Annotated[Path, typer.Argument(help="Target directory for exported XML sources.")],
    ib: Annotated[
        Path | None, typer.Option(help="Optional file infobase dir (temp dir if omitted).")
    ] = None,
    ibcmd: Annotated[Path | None, typer.Option(help="Path to ibcmd.")] = None,
) -> None:
    """Decompile a .cf file into XML/BSL sources via ibcmd."""
    try:
        report = decompile_cf(cf, out, ib=ib, ibcmd=ibcmd)
    except HarnessError as exc:
        raise _fail(exc) from exc
    render_stages(console, f"decompile: {report.cf.name}", report.stages)
    console.print(f"output: [bold]{report.out}[/]")


rules_app = typer.Typer(
    name="rules",
    help="Unpack and pack 1C Data Conversion 2.x rules via gitrules.",
    no_args_is_help=True,
)
app.add_typer(rules_app, name="rules")


@rules_app.command("unpack")
def rules_unpack(
    source: Annotated[Path, typer.Argument(help="Rules .zip archive or .xml file.")],
    out: Annotated[Path, typer.Argument(help="Target directory for unpacked rule trees.")],
    gitrules: Annotated[Path | None, typer.Option(help="Path to gitrules.")] = None,
) -> None:
    """Unpack exchange rules into a gitrules directory tree."""
    try:
        report = unpack_rules(source, out, gitrules=gitrules)
    except HarnessError as exc:
        raise _fail(exc) from exc
    render_stages(console, f"rules unpack: {report.source.name}", report.stages)
    console.print(f"output: [bold]{report.target}[/]")


@rules_app.command("pack")
def rules_pack(
    src_dir: Annotated[Path, typer.Argument(help="Directory with unpacked gitrules trees.")],
    out: Annotated[Path, typer.Argument(help="Target .zip archive, .xml file, or directory.")],
    gitrules: Annotated[Path | None, typer.Option(help="Path to gitrules.")] = None,
) -> None:
    """Assemble unpacked exchange rules back into .xml or .zip."""
    try:
        report = pack_rules(src_dir, out, gitrules=gitrules)
    except HarnessError as exc:
        raise _fail(exc) from exc
    render_stages(console, f"rules pack: {report.source.name}", report.stages)
    console.print(f"output: [bold]{report.target}[/]")


@app.command()
def query(
    query_text: Annotated[str | None, typer.Option("--query", "-q", help="1C query text.")] = None,
    query_file: Annotated[
        Path | None, typer.Option("--file", help="Path to a .bsl/.txt file with 1C query.")
    ] = None,
    server: Annotated[str | None, typer.Option(help="1C cluster server (Srvr).")] = None,
    ref: Annotated[str | None, typer.Option(help="Infobase name on cluster (Ref).")] = None,
    file_path: Annotated[Path | None, typer.Option("--ib", help="File infobase path.")] = None,
    usr: Annotated[str | None, typer.Option(help="1C user (or ONEC_USR env).")] = None,
    pwd: Annotated[str | None, typer.Option(help="1C password (or ONEC_PWD env).")] = None,
    ssh: Annotated[str | None, typer.Option(help="SSH target user@host (or ONEC_SSH env).")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = True,
) -> None:
    """Run a 1C query against a local or remote infobase via V83.COMConnector (Windows)."""
    text = query_text or ""
    if query_file is not None:
        if not query_file.is_file():
            raise _fail(HarnessError(f"query file not found: {query_file}"))
        text = query_file.read_text(encoding="utf-8")
    try:
        res = run_query(
            text,
            server=server,
            ref=ref,
            file_path=file_path,
            usr=usr,
            pwd=pwd,
            ssh=ssh,
        )
    except HarnessError as exc:
        raise _fail(exc) from exc
    if json_output:
        typer.echo(res.model_dump_json(indent=2))
    else:
        console.print(f"rows: {len(res.rows)} ({res.seconds}s)")


if __name__ == "__main__":
    app()
