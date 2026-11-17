"""Implementation of `harness init`."""

import json
import shutil
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from harness.errors import ConfigError, LinkError
from harness.generators.agents_md import render_agents_md
from harness.generators.basic import (
    build_autumn,
    build_bsl_ls,
    build_compose,
    render_packagedef,
)
from harness.generators.mcp import TOOLS_JS_PLACEHOLDER, build_mcp
from harness.models import EnvFile, VSCodeSettings
from harness.models.workspace import (
    DEFAULT_ATLAS_IMAGE,
    MCP_PYTHON_PLACEHOLDER,
    WORKSPACE_DIRS,
)
from harness.paths import needs_ascii_link
from harness.ports import allocate_ipc_port
from harness.services.writer import FileAction, FileWriter
from harness.system.links import LinkResult, create_file_link
from harness.system.platform import find_platform_dir

GITIGNORE_LINES = ("build/", "data/*.db", ".env")
_NO_GITKEEP = Path(".agents/skills")
_IBMCD_NAMES = ("ibcmd", "ibcmd.exe")


class InitOptions(BaseModel):
    """Inputs for workspace initialization."""

    target_dir: Path
    force: bool = False
    dry_run: bool = False
    ide: bool = False
    platform_tools_js: Path | None = None
    platform_dir: Path | None = None
    ipc_port: int | None = None
    atlas_image: str = DEFAULT_ATLAS_IMAGE
    mcp_python: Path | None = None
    indexing_mode: Literal["fast", "full"] = "fast"


class InitReport(BaseModel):
    """Outcome of `init`."""

    ipc_port: int
    actions: list[FileAction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    claude_link: LinkResult | None = None


def _ibcmd_available(platform_dir: Path | None) -> bool:
    """Tell whether the fast `ibcmd` loader can be used on this machine.

    Args:
        platform_dir: Installed platform directory, or None when none was found.

    Returns:
        True when `ibcmd` is on PATH or ships inside the platform `bin`
        directory. The blueprint default (True) is kept while the platform is
        unknown, so installing it later needs no config change.
    """
    if shutil.which("ibcmd") is not None:
        return True
    if platform_dir is None:
        return True
    return any((platform_dir / "bin" / name).is_file() for name in _IBMCD_NAMES)


def _is_app_execution_alias(path: Path) -> bool:
    """Tell whether `path` is a Windows App Execution Alias stub.

    Args:
        path: Resolved executable path.

    Returns:
        True for empty placeholder files such as the Store `python.exe`.
    """
    try:
        return path.stat().st_size == 0
    except OSError:
        return False


def _resolve_mcp_python(explicit: Path | None, warnings: list[str]) -> str:
    """Choose the interpreter command for the MCP platform help server.

    Args:
        explicit: `--mcp-python` value; used verbatim when provided.
        warnings: Accumulator for user-facing warnings.

    Returns:
        The command for `.mcp.json`: the explicit path, `python`, or the
        placeholder when no usable interpreter was found.
    """
    if explicit is not None:
        return explicit.as_posix()
    found = shutil.which("python")
    if found is None:
        warnings.append(
            "python not found; pass --mcp-python with the interpreter that has "
            f"mcp_bsl_context installed ({MCP_PYTHON_PLACEHOLDER} is in the MCP configs)"
        )
        return MCP_PYTHON_PLACEHOLDER
    if sys.platform == "win32" and _is_app_execution_alias(Path(found)):
        warnings.append(
            "python on PATH is a Microsoft Store alias; pass --mcp-python with the "
            f"interpreter that has mcp_bsl_context ({MCP_PYTHON_PLACEHOLDER} used instead)"
        )
        return MCP_PYTHON_PLACEHOLDER
    return "python"


def _drop_stale_claude_copy(claude: Path, previous_agents: str | None) -> None:
    """Remove a `CLAUDE.md` that is an untouched copy of the previous `AGENTS.md`.

    The copy fallback keeps the file readable for Claude Code, but a regenerated
    protocol must not leave the old text behind. A hand-edited `CLAUDE.md` is kept.

    Args:
        claude: `CLAUDE.md` path.
        previous_agents: `AGENTS.md` text before regeneration, or None.
    """
    if previous_agents is None or not claude.is_file() or claude.is_symlink():
        return
    try:
        if claude.read_text(encoding="utf-8") != previous_agents:
            return
        claude.unlink()
    except OSError:
        return


def _read_vscode(path: Path, warnings: list[str]) -> VSCodeSettings | None:
    """Parse existing VS Code settings, or return None (with a warning) if unusable."""
    if not path.exists():
        return VSCodeSettings()
    try:
        return VSCodeSettings.from_text(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        warnings.append(f"{path.name}: not plain JSON (comments?); IPC port not synced")
        return None


def _gitignore_content(existing: str) -> str:
    """Append missing harness entries to `.gitignore` text."""
    present = set(existing.splitlines())
    missing = [line for line in GITIGNORE_LINES if line not in present]
    if not missing:
        return existing
    base = existing if not existing or existing.endswith("\n") else existing + "\n"
    return base + "\n".join(missing) + "\n"


def init_workspace(opts: InitOptions) -> InitReport:
    """Scaffold a workspace: directories, configs, AGENTS.md and the CLAUDE.md link.

    Args:
        opts: Initialization options.

    Returns:
        A report with file actions, warnings and the allocated IPC port.

    Raises:
        ConfigError: If a generated config fails validation or the workspace tree
            cannot be created (permissions, path occupied by a file, missing drive).
    """
    try:
        return _init_workspace(opts)
    except OSError as exc:
        failed = exc.filename or str(opts.target_dir)
        msg = f"cannot create workspace at {failed}: {exc.strerror or exc}"
        raise ConfigError(msg) from exc


def _init_workspace(opts: InitOptions) -> InitReport:
    """Build the directory tree and configs; filesystem errors propagate upward."""
    root = opts.target_dir.resolve()
    writer = FileWriter(force=opts.force, dry_run=opts.dry_run)
    warnings: list[str] = []

    vscode_path = root / ".vscode" / "settings.json"
    vscode = _read_vscode(vscode_path, warnings)
    reuse = vscode.ipc_port() if vscode else None
    port = opts.ipc_port or allocate_ipc_port(root, reuse=reuse)

    platform_dir = opts.platform_dir or find_platform_dir()
    if platform_dir is None:
        warnings.append("1C platform not found; set PLATFORM_DIR in .env and .mcp.json")
    use_ibcmd = _ibcmd_available(platform_dir)
    if platform_dir is not None and not use_ibcmd:
        warnings.append(
            "platform ships no ibcmd; autumn-properties.json uses ibcmd: false "
            "(install the full platform distribution for faster loads)"
        )
    if opts.ide and opts.platform_tools_js is None:
        warnings.append(f"replace {TOOLS_JS_PLACEHOLDER} in MCP configs with the extension path")
    python_command = _resolve_mcp_python(opts.mcp_python, warnings)
    autumn = build_autumn(use_ibcmd=use_ibcmd)

    try:
        env = EnvFile(
            source_path=root / "src" / "cf",
            platform_dir=platform_dir,
            onec_ipc_port=port,
            indexing_mode=opts.indexing_mode,
        )
        mcp_json = build_mcp(
            ipc_port=port,
            platform_dir=platform_dir,
            include_platform_tools=opts.ide,
            platform_tools_js=opts.platform_tools_js,
            python_command=python_command,
        ).to_json()
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    if not opts.dry_run:
        for rel in WORKSPACE_DIRS:
            (root / rel).mkdir(parents=True, exist_ok=True)
    for rel in WORKSPACE_DIRS:
        if rel != _NO_GITKEEP:
            writer.write(root / rel / ".gitkeep", "")

    writer.write(root / "packagedef", render_packagedef(root.name))
    writer.write(root / "autumn-properties.json", autumn.to_json())
    writer.write(root / ".bsl-language-server.json", build_bsl_ls().to_json())
    writer.write(root / ".mcp.json", mcp_json)
    writer.write(root / ".cursor" / "mcp.json", mcp_json)
    writer.write(
        root / "docker-compose.yml",
        build_compose(opts.atlas_image).to_yaml(),
    )
    writer.write(root / ".env", env.render())
    agents_path = root / "AGENTS.md"
    previous_agents = agents_path.read_text(encoding="utf-8") if agents_path.is_file() else None
    writer.write(agents_path, render_agents_md())
    if vscode is not None:
        writer.merge(vscode_path, vscode.with_ipc_port(port).to_json())
    gitignore = root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    writer.merge(gitignore, _gitignore_content(existing))

    claude_link: LinkResult | None = None
    if not opts.dry_run:
        _drop_stale_claude_copy(root / "CLAUDE.md", previous_agents)
        try:
            claude_link = create_file_link(root / "CLAUDE.md", agents_path)
        except LinkError as exc:
            warnings.append(f"CLAUDE.md: {exc}")
        if claude_link is LinkResult.COPIED:
            warnings.append("CLAUDE.md copied (symlinks not permitted); keep it in sync")
    if needs_ascii_link(root):
        warnings.append("path has non-ASCII chars or spaces; run `harness link-ascii` for Docker")

    return InitReport(
        ipc_port=port, actions=writer.actions, warnings=warnings, claude_link=claude_link
    )
