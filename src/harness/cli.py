"""Typer CLI entry point."""

import json
import sys
from pathlib import Path
from typing import Annotated, Literal

import typer
from rich.console import Console

from harness.errors import HarnessError
from harness.report import render_actions, render_check, render_checks, render_stages
from harness.services.artifacts import ArtifactReport, Tools, create_infobase, pack, unpack
from harness.services.build import BuildStage, build_workspace
from harness.services.check import run_check, run_check_changed
from harness.services.compile import ALL_MODES, DEFAULT_MODES, compile_check
from harness.services.decompile import decompile_cf
from harness.services.diff import diff_trees, unified_patch
from harness.services.doctor import has_failures, run_doctor
from harness.services.extension import MODES, adopt, intercept, lint
from harness.services.fix_permissions import fix_permissions
from harness.services.init import InitOptions, init_workspace
from harness.services.link_ascii import link_ascii
from harness.services.meta import find_objects, render_summary, resolve_ref, show_object
from harness.services.onec import Credentials, Ibcmd
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
    """Print the error unwrapped (paths stay copyable) and build the exit exception."""
    typer.echo(f"error: {exc}", err=True)
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
    target_dir: Annotated[Path, typer.Argument(help="Workspace root or a dump directory.")],
    src: Annotated[Path | None, typer.Option(help="Source directory.")] = None,
    config: Annotated[Path | None, typer.Option(help="BSL LS configuration file.")] = None,
    report_dir: Annotated[Path | None, typer.Option(help="Report directory.")] = None,
    bsl: Annotated[Path | None, typer.Option(help="Path to bsl-language-server.")] = None,
    changed_from: Annotated[
        Path | None,
        typer.Option(help="Pristine dump: analyze only changed .bsl, report only new issues."),
    ] = None,
    all_issues: Annotated[
        bool, typer.Option("--all-issues", help="With --changed-from: keep pre-existing issues.")
    ] = False,
    fail_on_error: Annotated[
        bool, typer.Option(help="Exit 1 when at least one Error diagnostic is found.")
    ] = False,
) -> None:
    """Analyze sources with BSL Language Server (linter; `compile` is the real compiler)."""
    if src is None and (target_dir / "Configuration.xml").is_file():
        src = target_dir
    if changed_from is not None:
        try:
            changed = run_check_changed(
                src or target_dir,
                changed_from,
                config=config,
                bsl=bsl,
                new_only=not all_issues,
            )
        except HarnessError as exc:
            raise _fail(exc) from exc
        label = "new" if changed.new_only else "all"
        typer.echo(
            f"changed .bsl: {len(changed.files)}; {label} diagnostics: "
            f"{len(changed.diagnostics)} {changed.counts()} ({changed.seconds}s)"
        )
        for d in changed.diagnostics:
            typer.echo(f"{d.path}:{d.line}: {d.severity} {d.code}: {d.message}")
        if fail_on_error and any(d.severity == "Error" for d in changed.diagnostics):
            raise typer.Exit(code=1)
        return
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


# --------------------------------------------------------------------------------------
# Binaries <-> XML, infobases, compilation, metadata, extensions.
# Output is plain text on purpose: cheap to read for an agent, no Rich markup surprises.
# --------------------------------------------------------------------------------------

IbOpt = Annotated[
    Path | None, typer.Option("--ib", help="Persistent file infobase (temp one if omitted).")
]
IbUserOpt = Annotated[str | None, typer.Option("--ib-user", help="Infobase user (or ONEC_IB_USR).")]
IbPwdOpt = Annotated[
    str | None, typer.Option("--ib-pwd", help="Infobase password (or ONEC_IB_PWD).")
]
IbcmdOpt = Annotated[Path | None, typer.Option("--ibcmd", help="Path to ibcmd.")]
DesignerOpt = Annotated[Path | None, typer.Option("--designer", help="Path to 1cv8.")]


def _tools(
    ibcmd: Path | None, designer: Path | None, user: str | None, password: str | None
) -> Tools:
    return Tools(ibcmd=ibcmd, designer=designer, credentials=Credentials.from_env(user, password))


def _echo_stages(stages: list[BuildStage]) -> None:
    for stage in stages:
        typer.echo(f"  {stage.name:<22} {stage.seconds:7.1f}s")


def _echo_artifact(title: str, report: ArtifactReport) -> None:
    typer.echo(f"{title}: {report.source} -> {report.target}")
    _echo_stages(report.stages)
    for name in report.extensions:
        typer.echo(f"  extension: {name}")
    for note in report.notes:
        typer.echo(f"  note: {note}")


@app.command("unpack")
def unpack_cmd(
    source: Annotated[Path, typer.Argument(help=".cf, .cfe, .epf, .erf or .dt")],
    out: Annotated[Path, typer.Argument(help="Target directory for XML sources.")],
    ib: IbOpt = None,
    base_cf: Annotated[
        Path | None, typer.Option(help=".cfe only: base .cf to load first (enables apply).")
    ] = None,
    ib_user: IbUserOpt = None,
    ib_pwd: IbPwdOpt = None,
    ibcmd: IbcmdOpt = None,
    designer: DesignerOpt = None,
) -> None:
    """Decompile any 1C binary into XML/BSL sources (type chosen by suffix)."""
    try:
        report = unpack(
            source,
            out,
            ib=ib,
            base_cf=base_cf,
            tools=_tools(ibcmd, designer, ib_user, ib_pwd),
        )
    except HarnessError as exc:
        raise _fail(exc) from exc
    _echo_artifact("unpack", report)


@app.command("pack")
def pack_cmd(
    src: Annotated[Path, typer.Argument(help="XML dump (or infobase dir when OUT is .dt).")],
    out: Annotated[Path, typer.Argument(help="Target .cf, .cfe, .epf, .erf or .dt")],
    ib: IbOpt = None,
    extension: Annotated[
        str | None, typer.Option(help=".cfe: extension name (default: from Configuration.xml).")
    ] = None,
    base_cf: Annotated[
        Path | None, typer.Option(help=".cfe without --ib: base .cf to apply against.")
    ] = None,
    changed_from: Annotated[
        Path | None,
        typer.Option(help="Pristine dump that --ib holds: import only changed files."),
    ] = None,
    no_apply: Annotated[
        bool, typer.Option("--no-apply", help=".cfe: skip `config apply` against the base.")
    ] = False,
    ib_user: IbUserOpt = None,
    ib_pwd: IbPwdOpt = None,
    ibcmd: IbcmdOpt = None,
    designer: DesignerOpt = None,
) -> None:
    """Build a 1C binary from XML sources (type chosen by the output suffix)."""
    try:
        report = pack(
            src,
            out,
            ib=ib,
            extension=extension,
            base_cf=base_cf,
            changed_from=changed_from,
            apply=not no_apply,
            tools=_tools(ibcmd, designer, ib_user, ib_pwd),
        )
    except HarnessError as exc:
        raise _fail(exc) from exc
    _echo_artifact("pack", report)
    if report.target.is_file():
        typer.echo(f"  size: {report.target.stat().st_size / 1024:.1f} KB")


@app.command("compile")
def compile_cmd(
    ib: Annotated[Path, typer.Argument(help="File infobase with the configuration loaded.")],
    extension: Annotated[
        str | None, typer.Option(help="Check this extension instead of the configuration.")
    ] = None,
    modes: Annotated[
        str | None,
        typer.Option(help=f"Comma-separated contexts (default {','.join(DEFAULT_MODES)})."),
    ] = None,
    all_modes: Annotated[
        bool, typer.Option(help="Check every context incl. thick/mobile.")
    ] = False,
    ib_user: IbUserOpt = None,
    ib_pwd: IbPwdOpt = None,
    designer: DesignerOpt = None,
) -> None:
    """Compile modules with the real platform compiler (designer /CheckModules)."""
    chosen = (
        ALL_MODES
        if all_modes
        else tuple(m.strip() for m in (modes.split(",") if modes else DEFAULT_MODES) if m.strip())
    )
    try:
        report = compile_check(
            ib,
            extension=extension,
            modes=chosen,
            credentials=Credentials.from_env(ib_user, ib_pwd),
            designer=designer,
        )
    except HarnessError as exc:
        raise _fail(exc) from exc
    for err in report.errors:
        where = f"{err.module}({err.line},{err.column})"
        typer.echo(f"{where}: {err.message} [{', '.join(err.modes)}]")
        if err.context:
            typer.echo(f"    {err.context}")
    status = "OK" if report.ok else f"{len(report.errors)} error(s)"
    typer.echo(f"compile: {status} ({report.seconds}s)")
    if not report.ok:
        raise typer.Exit(code=1)


@app.command("diff")
def diff_cmd(
    base: Annotated[Path, typer.Argument(help="Pristine dump.")],
    current: Annotated[Path, typer.Argument(help="Modified dump.")],
    patch: Annotated[bool, typer.Option(help="Print unified diffs of changed .bsl files.")] = False,
) -> None:
    """List added (A), modified (M) and deleted (D) files between two dumps."""
    try:
        delta = diff_trees(base, current)
    except HarnessError as exc:
        raise _fail(exc) from exc
    for status, items in (("A", delta.added), ("M", delta.modified), ("D", delta.deleted)):
        for rel in items:
            typer.echo(f"{status} {rel}")
    typer.echo(
        f"added {len(delta.added)}, modified {len(delta.modified)}, deleted {len(delta.deleted)}"
    )
    if patch:
        for rel in [*delta.changed, *delta.deleted]:
            if rel.lower().endswith(".bsl"):
                typer.echo(unified_patch(base, current, rel), nl=False)


meta_app = typer.Typer(
    name="meta", help="Inspect configuration dumps without reading raw XML.", no_args_is_help=True
)
app.add_typer(meta_app, name="meta")


@meta_app.command("find")
def meta_find(
    src: Annotated[Path, typer.Argument(help="Dump root (with Configuration.xml).")],
    pattern: Annotated[str, typer.Argument(help="Regex/substring for name or synonym.")],
    kind: Annotated[
        str | None, typer.Option("--type", help="Class: Catalog, Документ, ...")
    ] = None,
    limit: Annotated[int, typer.Option(help="Maximum hits.")] = 200,
) -> None:
    """Find objects by name or synonym."""
    try:
        hits = find_objects(src, pattern, kind=kind, limit=limit)
    except HarnessError as exc:
        raise _fail(exc) from exc
    for hit in hits:
        typer.echo(f"{hit.ref}" + (f" — {hit.synonym}" if hit.synonym else ""))
    typer.echo(f"{len(hits)} object(s)")


@meta_app.command("show")
def meta_show(
    src: Annotated[Path, typer.Argument(help="Dump root (with Configuration.xml).")],
    ref: Annotated[str, typer.Argument(help="Catalog.X, Справочник.X, CommonModule.X ...")],
    methods: Annotated[bool, typer.Option(help="List module methods with line numbers.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Show attributes, tabular sections, forms, modules (and methods) of one object."""
    try:
        summary = show_object(src, resolve_ref(ref), with_methods=methods)
    except HarnessError as exc:
        raise _fail(exc) from exc
    if json_output:
        typer.echo(summary.model_dump_json(indent=2))
    else:
        typer.echo(render_summary(summary, src.resolve()))


ext_app = typer.Typer(
    name="ext",
    help="Configuration extension (.cfe) sources: adopt, intercept, lint.",
    no_args_is_help=True,
)
app.add_typer(ext_app, name="ext")
ExtSrc = Annotated[Path, typer.Argument(help="Extension XML dump.")]
BaseSrc = Annotated[Path, typer.Argument(help="Base configuration XML dump.")]


@ext_app.command("adopt")
def ext_adopt(
    ext_src: ExtSrc,
    base_src: BaseSrc,
    refs: Annotated[list[str], typer.Argument(help="Objects: Catalog.X CommonModule.Y ...")],
) -> None:
    """Adopt base objects into the extension (like "Добавить в расширение")."""
    try:
        for ref in refs:
            result = adopt(ext_src, base_src, resolve_ref(ref))
            state = "adopted" if result.created else "already adopted"
            typer.echo(f"{state}: {result.ref} -> {result.path}")
    except HarnessError as exc:
        raise _fail(exc) from exc


@ext_app.command("intercept")
def ext_intercept(
    ext_src: ExtSrc,
    base_src: BaseSrc,
    ref: Annotated[str, typer.Argument(help="Object owning the module: CommonModule.X ...")],
    method: Annotated[str, typer.Argument(help="Base method name.")],
    mode: Annotated[str, typer.Option(help=f"Interception: {', '.join(MODES)}.")] = "instead",
    module: Annotated[
        str | None,
        typer.Option(help="Module: ObjectModule, ManagerModule, RecordSetModule, ..."),
    ] = None,
) -> None:
    """Generate an interception (&Перед/&После/&Вместо/&ИзменениеИКонтроль) for a method."""
    try:
        result = intercept(ext_src, base_src, resolve_ref(ref), method, mode, module=module)
    except HarnessError as exc:
        raise _fail(exc) from exc
    if result.adopted:
        typer.echo(f"adopted {ref}")
    typer.echo(f"added {result.method} to {result.module}:")
    typer.echo(result.code)


@ext_app.command("lint")
def ext_lint(ext_src: ExtSrc, base_src: BaseSrc) -> None:
    """Check interceptions and adopted objects against the base (what the platform misses)."""
    try:
        report = lint(ext_src, base_src)
    except HarnessError as exc:
        raise _fail(exc) from exc
    for issue in report.issues:
        where = f"{issue.path}:{issue.line}" if issue.line else issue.path
        typer.echo(f"{issue.level}: {where}: {issue.message}")
    typer.echo(
        f"ext lint {report.extension}: {report.objects} object(s), "
        f"{report.interceptions} interception(s), {report.errors} error(s), "
        f"{len(report.issues) - report.errors} warning(s)"
    )
    if report.errors:
        raise typer.Exit(code=1)


ib_app = typer.Typer(name="ib", help="File infobases: create, dump, list extensions.")
app.add_typer(ib_app, name="ib")


@ib_app.command("create")
def ib_create(
    ib: Annotated[Path, typer.Argument(help="New infobase directory.")],
    cf: Annotated[Path | None, typer.Option(help="Configuration to load.")] = None,
    dt: Annotated[Path | None, typer.Option(help="Infobase dump to restore.")] = None,
    cfe: Annotated[list[Path] | None, typer.Option(help="Extension(s) to load.")] = None,
    ib_user: IbUserOpt = None,
    ib_pwd: IbPwdOpt = None,
    ibcmd: IbcmdOpt = None,
) -> None:
    """Create a file infobase from a .cf or .dt, plus extensions."""
    try:
        report = create_infobase(
            ib, cf=cf, dt=dt, cfe=cfe or [], tools=_tools(ibcmd, None, ib_user, ib_pwd)
        )
    except HarnessError as exc:
        raise _fail(exc) from exc
    _echo_artifact("ib create", report)


@ib_app.command("dump")
def ib_dump(
    ib: Annotated[Path, typer.Argument(help="File infobase directory.")],
    dt: Annotated[Path, typer.Argument(help="Target .dt file.")],
    ib_user: IbUserOpt = None,
    ib_pwd: IbPwdOpt = None,
    ibcmd: IbcmdOpt = None,
) -> None:
    """Dump a file infobase into a .dt."""
    try:
        report = pack(ib, dt, tools=_tools(ibcmd, None, ib_user, ib_pwd))
    except HarnessError as exc:
        raise _fail(exc) from exc
    _echo_artifact("ib dump", report)


@ib_app.command("extensions")
def ib_extensions(
    ib: Annotated[Path, typer.Argument(help="File infobase directory.")],
    ib_user: IbUserOpt = None,
    ib_pwd: IbPwdOpt = None,
    ibcmd: IbcmdOpt = None,
) -> None:
    """List extensions registered in an infobase."""
    try:
        names = Ibcmd(
            ib, executable=ibcmd, credentials=Credentials.from_env(ib_user, ib_pwd)
        ).extensions()
    except HarnessError as exc:
        raise _fail(exc) from exc
    for name in names:
        typer.echo(name)


if __name__ == "__main__":
    app()
