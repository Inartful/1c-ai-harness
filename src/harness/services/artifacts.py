"""Universal unpack/pack of 1C binaries chosen by file suffix.

- `.cf`: unpack = ibcmd load + export; pack = ibcmd import (full or partial) + save.
- `.cfe`: unpack = ibcmd load --extension + export; pack = import --extension, apply, save.
- `.epf`/`.erf`: designer /DumpExternalDataProcessorOrReportToFiles and
  /LoadExternalDataProcessorOrReportFromFiles on a cached empty infobase.
- `.dt`: unpack = ibcmd create --restore + export cf + all extensions; pack = ibcmd dump.
"""

import shutil
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, Field

from harness.errors import ConfigError
from harness.services.build import BuildStage
from harness.services.diff import diff_trees
from harness.services.meta import read_config_name
from harness.services.onec import (
    MAX_PARTIAL_ARGV_CHARS,
    Credentials,
    Designer,
    Ibcmd,
    scratch_ib_dir,
    temp_infobase,
)
from harness.system.runner import CommandRunner, run

_TMP_EXTENSION = "harness_tmp_ext"
EXTERNAL = (".epf", ".erf")


class ArtifactReport(BaseModel):
    """Outcome of `unpack`, `pack` or `ib create`."""

    kind: str
    source: Path
    target: Path
    stages: list[BuildStage] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    extensions: list[str] = Field(default_factory=list)


class Tools(BaseModel):
    """Explicit tool paths and credentials shared by all operations."""

    ibcmd: Path | None = None
    designer: Path | None = None
    credentials: Credentials = Field(default_factory=Credentials)


def _scratch_designer(tools: Tools, runner: CommandRunner) -> Designer:
    """Designer bound to a cached empty infobase (enough for `.epf`/`.erf`)."""
    ib = Ibcmd(scratch_ib_dir(), executable=tools.ibcmd, runner=runner)
    ib.ensure()
    return Designer(ib.db_path, executable=tools.designer, runner=runner)


def load_extension(tool: Ibcmd, cfe: Path, *, name: str | None = None) -> str:
    """Load a `.cfe` registering it under its own internal name; return that name.

    The internal name is unknown until the extension is exported, so the file is loaded under
    a temporary name first, exported, and re-registered when the names differ.
    """
    if name:
        tool.load(cfe, extension=name)
        return name
    tool.load(cfe, extension=_TMP_EXTENSION)
    probe = tool.db_path.parent / f".{tool.db_path.name}-probe"
    try:
        tool.export(probe, extension=_TMP_EXTENSION)
        real = read_config_name(probe)
    finally:
        shutil.rmtree(probe, ignore_errors=True)
    if real != _TMP_EXTENSION:
        tool.delete_extension(_TMP_EXTENSION)
        tool.load(cfe, extension=real)
    return real


def unpack(
    source: Path,
    out: Path,
    *,
    ib: Path | None = None,
    base_cf: Path | None = None,
    tools: Tools | None = None,
    runner: CommandRunner = run,
) -> ArtifactReport:
    """Decompile a `.cf`, `.cfe`, `.epf`, `.erf` or `.dt` into XML sources.

    Args:
        source: Binary to decompile.
        out: Target directory.
        ib: Persistent infobase to keep (recommended for `.cfe`/`.dt`: later `pack`, `compile`
            and `ext` checks reuse it). A temp infobase is used when omitted.
        base_cf: For `.cfe`: base configuration to load first, so the extension can be applied.
        tools: Tool paths and credentials.
        runner: Command runner.

    Returns:
        The report.

    Raises:
        ConfigError: On an unsupported suffix or missing input.
    """
    tools = tools or Tools()
    src = source.resolve()
    if not src.is_file():
        msg = f"input not found: {src}"
        raise ConfigError(msg)
    target = out.resolve()
    suffix = src.suffix.lower()
    report = ArtifactReport(kind=suffix.lstrip("."), source=src, target=target)

    if suffix in EXTERNAL:
        designer = _scratch_designer(tools, runner)
        target.mkdir(parents=True, exist_ok=True)
        root_xml = target / f"{src.stem}.xml"
        report.stages.append(
            designer.run_checked(
                "dump-external",
                [
                    "/DumpExternalDataProcessorOrReportToFiles",
                    str(root_xml),
                    str(src),
                    "-Format",
                    "Hierarchical",
                ],
            )
        )
        report.notes.append(f"root file: {root_xml}")
        return report

    if suffix == ".dt":
        if ib is not None and (ib / "1Cv8.1CD").exists():
            msg = f"infobase already exists: {ib}; remove it or pass another --ib"
            raise ConfigError(msg)
        with _restored(src, ib, tools, runner) as tool:
            tool.export(target / "cf")
            names = tool.extensions()
            if names:
                tool.export_all_extensions(target / "cfe")
            report.extensions = names
            report.stages = tool.stages
        report.notes.append(f"configuration: {target / 'cf'}")
        report.notes.extend(f"extension: {target / 'cfe' / n}" for n in report.extensions)
        return report

    if suffix not in (".cf", ".cfe"):
        msg = f"unsupported input {src.suffix!r}; expected .cf, .cfe, .epf, .erf or .dt"
        raise ConfigError(msg)

    with temp_infobase(ib, ibcmd=tools.ibcmd, credentials=tools.credentials, runner=runner) as tool:
        if suffix == ".cf":
            tool.load(src)
            tool.export(target)
        else:
            if base_cf is not None:
                tool.load(base_cf)
                tool.apply()
            # The internal name is only known after export: export straight into the target,
            # then re-register under the real name only when the infobase is kept.
            registered = _TMP_EXTENSION
            tool.load(src, extension=registered)
            tool.export(target, extension=registered)
            name = read_config_name(target)
            if ib is not None and name != registered:
                tool.delete_extension(registered)
                tool.load(src, extension=name)
                registered = name
            report.extensions = [name]
            if base_cf is not None or ib is not None:
                tool.apply(extension=registered)
        report.stages = tool.stages
    if ib is not None:
        report.notes.append(f"infobase kept: {ib.resolve()}")
    return report


@contextmanager
def _restored(dt: Path, ib: Path | None, tools: Tools, runner: CommandRunner) -> Iterator[Ibcmd]:
    """Yield an `Ibcmd` over `dt` restored into `ib` or into a temp directory."""
    tmp = None if ib is not None else tempfile.mkdtemp(prefix="harness-dt-")
    path = ib if ib is not None else Path(str(tmp))
    try:
        tool = Ibcmd(path, executable=tools.ibcmd, credentials=tools.credentials, runner=runner)
        tool.create(restore=dt)
        yield tool
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _changed_files(src: Path, changed_from: Path) -> list[str] | None:
    """Files to import partially, or None when a full import is required."""
    delta = diff_trees(changed_from, src)
    if delta.deleted:
        return None
    files = delta.changed
    if sum(len(f) + 3 for f in files) > MAX_PARTIAL_ARGV_CHARS:
        return None
    return files


def _import(
    tool: Ibcmd,
    src: Path,
    *,
    extension: str | None,
    changed_from: Path | None,
    notes: list[str],
) -> None:
    if changed_from is not None:
        files = _changed_files(src, changed_from)
        if files is None:
            notes.append("partial import impossible (deletions or too many files): full import")
        elif not files:
            notes.append("no changes against --changed-from: import skipped")
            return
        else:
            tool.import_files(src, files, extension=extension)
            return
    tool.import_full(src, extension=extension)


def external_root(src: Path) -> Path:
    """Find the root XML of an external data processor/report dump."""
    if src.is_file():
        return src
    candidates = [p for p in sorted(src.glob("*.xml")) if p.name != "ConfigDumpInfo.xml"]
    if len(candidates) != 1:
        msg = f"expected exactly one root .xml in {src}, found {len(candidates)}"
        raise ConfigError(msg)
    return candidates[0]


def pack(
    src: Path,
    out: Path,
    *,
    ib: Path | None = None,
    extension: str | None = None,
    base_cf: Path | None = None,
    changed_from: Path | None = None,
    apply: bool = True,
    tools: Tools | None = None,
    runner: CommandRunner = run,
) -> ArtifactReport:
    """Build a `.cf`, `.cfe`, `.epf`, `.erf` from XML sources, or a `.dt` from an infobase.

    Args:
        src: XML dump directory (or root `.xml` for external objects, or infobase for `.dt`).
        out: Target binary; its suffix selects the operation.
        ib: Persistent infobase (for `.cfe` it should hold the base configuration).
        extension: Extension name for `.cfe`; read from `src/Configuration.xml` by default.
        base_cf: For `.cfe` without `ib`: base configuration to validate against.
        changed_from: Pristine dump; only files changed against it are imported into `ib`.
        apply: For `.cfe` with a base: run `config apply` to prove the extension fits.
        tools: Tool paths and credentials.
        runner: Command runner.

    Returns:
        The report.

    Raises:
        ConfigError: On an unsupported suffix or missing input.
    """
    tools = tools or Tools()
    source = src.resolve()
    target = out.resolve()
    suffix = target.suffix.lower()
    report = ArtifactReport(kind=suffix.lstrip("."), source=source, target=target)
    if not source.exists():
        msg = f"source not found: {source}"
        raise ConfigError(msg)
    target.parent.mkdir(parents=True, exist_ok=True)

    if suffix in EXTERNAL:
        designer = _scratch_designer(tools, runner)
        root_xml = external_root(source)
        report.stages.append(
            designer.run_checked(
                "load-external",
                ["/LoadExternalDataProcessorOrReportFromFiles", str(root_xml), str(target)],
            )
        )
        return report

    if suffix == ".dt":
        tool = Ibcmd(source, executable=tools.ibcmd, credentials=tools.credentials, runner=runner)
        if not tool.exists:
            msg = f"not a file infobase: {source}"
            raise ConfigError(msg)
        tool.dump(target)
        report.stages = tool.stages
        return report

    if suffix not in (".cf", ".cfe"):
        msg = f"unsupported output {target.suffix!r}; expected .cf, .cfe, .epf, .erf or .dt"
        raise ConfigError(msg)
    if not (source / "Configuration.xml").is_file():
        msg = f"no Configuration.xml in {source}"
        raise ConfigError(msg)
    if changed_from is not None and ib is None:
        report.notes.append("--changed-from needs --ib holding that baseline: full import")
        changed_from = None

    with temp_infobase(ib, ibcmd=tools.ibcmd, credentials=tools.credentials, runner=runner) as tool:
        if suffix == ".cf":
            _import(tool, source, extension=None, changed_from=changed_from, notes=report.notes)
            tool.save(target)
        else:
            name = extension or read_config_name(source)
            report.extensions = [name]
            if base_cf is not None:
                tool.load(base_cf)
                tool.apply()
            if name not in tool.extensions():
                changed_from = None
            _import(tool, source, extension=name, changed_from=changed_from, notes=report.notes)
            if apply and (ib is not None or base_cf is not None):
                tool.apply(extension=name)
            else:
                report.notes.append("not applied to a base configuration (pass --ib or --base-cf)")
            tool.save(target, extension=name)
        report.stages = tool.stages
    return report


def create_infobase(
    ib: Path,
    *,
    cf: Path | None = None,
    dt: Path | None = None,
    cfe: Sequence[Path] = (),
    tools: Tools | None = None,
    runner: CommandRunner = run,
) -> ArtifactReport:
    """Create a file infobase from a `.dt` or `.cf` and load extensions into it.

    Args:
        ib: Infobase directory (must not contain an infobase yet).
        cf: Configuration to load and apply.
        dt: Infobase dump to restore (data included).
        cfe: Extensions to load (registered under their internal names) and apply.
        tools: Tool paths and credentials.
        runner: Command runner.

    Returns:
        The report.

    Raises:
        ConfigError: When both `cf` and `dt` are passed or the infobase already exists.
    """
    tools = tools or Tools()
    if cf is not None and dt is not None:
        msg = "pass either --cf or --dt, not both"
        raise ConfigError(msg)
    tool = Ibcmd(ib, executable=tools.ibcmd, credentials=tools.credentials, runner=runner)
    if tool.exists:
        msg = f"infobase already exists: {tool.db_path}"
        raise ConfigError(msg)
    report = ArtifactReport(kind="ib", source=(dt or cf or ib).resolve(), target=tool.db_path)
    tool.create(restore=dt)
    if cf is not None:
        tool.load(cf)
        tool.apply()
    for item in cfe:
        name = load_extension(tool, item)
        tool.apply(extension=name)
    report.extensions = tool.extensions()
    report.stages = tool.stages
    return report
