"""Implementation of `harness doctor`: read-only environment pre-flight checks."""

import json
import re
import shutil
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from harness.errors import HarnessError
from harness.models import VSCodeSettings
from harness.models.workspace import WORKSPACE_DIRS
from harness.paths import needs_ascii_link
from harness.ports import is_port_free
from harness.system.docker import docker_daemon_available
from harness.system.platform import find_hbk_files, find_platform_dir
from harness.system.runner import CommandRunner, run

_VERSION_TIMEOUT_S = 15.0
_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")
REQUIRED_FILES = (
    "AGENTS.md",
    "packagedef",
    "autumn-properties.json",
    ".bsl-language-server.json",
    ".mcp.json",
    "docker-compose.yml",
    ".env",
)


class Status(StrEnum):
    """Check severity."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


class CheckResult(BaseModel):
    """Result of one check."""

    name: str
    status: Status
    detail: str
    hint: str = ""


def _tool_version(
    exe: str, args: list[str], runner: CommandRunner
) -> tuple[tuple[int, ...] | None, str]:
    """Run `<exe> <args>` and parse the first version-like token from its output."""
    try:
        result = runner([exe, *args], timeout_s=_VERSION_TIMEOUT_S)
    except HarnessError as exc:
        return None, str(exc).splitlines()[0]
    text = (result.stdout + result.stderr).strip()
    match = _VERSION_RE.search(text)
    if match is None:
        return None, text.splitlines()[0] if text else "no output"
    return tuple(int(g) for g in match.groups() if g is not None), match.group(0)


def _check_versioned(
    name: str,
    exe: str,
    args: list[str],
    min_major: int,
    *,
    required: bool,
    hint: str,
    which: Callable[[str], str | None],
    runner: CommandRunner,
) -> CheckResult:
    """Check that a CLI exists and meets a minimum major version."""
    missing = Status.FAIL if required else Status.WARN
    if which(exe) is None:
        return CheckResult(name=name, status=missing, detail="not found on PATH", hint=hint)
    version, detail = _tool_version(exe, args, runner)
    if version is None:
        return CheckResult(name=name, status=Status.WARN, detail=f"version unknown: {detail}")
    if version[0] < min_major:
        return CheckResult(
            name=name, status=Status.FAIL, detail=f"{detail} < {min_major}.x", hint=hint
        )
    return CheckResult(name=name, status=Status.OK, detail=detail)


def _read_json(path: Path) -> object:
    """Load JSON, returning None if the file is missing or invalid."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _mcp_ports(path: Path) -> list[int]:
    """Extract every ONEC_IPC_PORT value from an MCP config file."""
    data = _read_json(path)
    ports: list[int] = []
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if isinstance(servers, dict):
        for server in servers.values():
            env = server.get("env") if isinstance(server, dict) else None
            raw = env.get("ONEC_IPC_PORT") if isinstance(env, dict) else None
            if isinstance(raw, str) and raw.isdigit():
                ports.append(int(raw))
    return ports


def _env_values(path: Path) -> dict[str, str]:
    """Parse simple `KEY=value` lines from `.env`."""
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep:
                values[key] = value
    return values


def _check_ibcmd(
    root: Path, platform: Path | None, which: Callable[[str], str | None]
) -> CheckResult:
    """Check `ibcmd`, tolerating its absence when the workspace disables it.

    Args:
        root: Workspace root, used to read the generated `autumn-properties.json`.
        platform: Installed platform directory, if one was found.
        which: Executable lookup, injectable for tests.

    Returns:
        The check result; 32-bit platform builds ship no `ibcmd` at all.
    """
    if which("ibcmd") is not None:
        return CheckResult(name="ibcmd", status=Status.OK, detail="found")
    data = _read_json(root / "autumn-properties.json")
    if isinstance(data, dict):
        nested = data.get("vrunner")
        settings = nested if isinstance(nested, dict) else data
        if settings.get("ibcmd") is False:
            return CheckResult(
                name="ibcmd", status=Status.OK, detail="disabled in autumn-properties.json"
            )
    if platform is not None and not any(
        (platform / "bin" / name).is_file() for name in ("ibcmd", "ibcmd.exe")
    ):
        return CheckResult(
            name="ibcmd",
            status=Status.WARN,
            detail="not shipped with this platform (32-bit build)",
            hint='set "ibcmd": false in autumn-properties.json or install a 64-bit platform',
        )
    return CheckResult(
        name="ibcmd",
        status=Status.WARN,
        detail="not found on PATH",
        hint='needed for "ibcmd": true; add platform bin to PATH',
    )


def check_workspace(root: Path, is_free: Callable[[int], bool]) -> list[CheckResult]:
    """Validate workspace layout, port consistency and path safety.

    Args:
        root: Workspace root.
        is_free: Port probe.

    Returns:
        Check results.
    """
    results: list[CheckResult] = []
    missing = [str(p) for p in WORKSPACE_DIRS if not (root / p).is_dir()]
    missing += [f for f in REQUIRED_FILES if not (root / f).is_file()]
    if missing:
        results.append(
            CheckResult(
                name="workspace layout",
                status=Status.FAIL,
                detail="missing: " + ", ".join(missing),
                hint="run `harness init`",
            )
        )
    else:
        results.append(CheckResult(name="workspace layout", status=Status.OK, detail="complete"))

    settings = _read_json(root / "autumn-properties.json")
    if isinstance(settings, dict):
        if "vrunner" in settings:
            results.append(
                CheckResult(name="vrunner settings", status=Status.OK, detail="3.0 format")
            )
        else:
            results.append(
                CheckResult(
                    name="vrunner settings",
                    status=Status.FAIL,
                    detail="flat 2.6 format; vrunner 3.0 ignores it",
                    hint="run `harness init --force` to regenerate the file",
                )
            )

    env = _env_values(root / ".env")
    sources: dict[str, set[int]] = {}
    if env.get("ONEC_IPC_PORT", "").isdigit():
        sources[".env"] = {int(env["ONEC_IPC_PORT"])}
    for rel in (".mcp.json", ".cursor/mcp.json"):
        found = _mcp_ports(root / rel)
        if found:
            sources[rel] = set(found)
    vscode_file = root / ".vscode" / "settings.json"
    try:
        vscode_port = VSCodeSettings.from_text(vscode_file.read_text(encoding="utf-8")).ipc_port()
    except (OSError, ValueError):
        vscode_port = None
    if vscode_port is not None:
        sources[".vscode/settings.json"] = {vscode_port}
    distinct = set().union(*sources.values()) if sources else set()
    if len(distinct) > 1:
        detail = "; ".join(f"{k}={sorted(v)}" for k, v in sources.items())
        results.append(
            CheckResult(
                name="IPC port consistency",
                status=Status.FAIL,
                detail=detail,
                hint="make all values equal to avoid WORKSPACE_MISMATCH",
            )
        )
    elif distinct:
        port = next(iter(distinct))
        results.append(
            CheckResult(name="IPC port consistency", status=Status.OK, detail=f"port {port}")
        )
        busy = not is_free(port)
        results.append(
            CheckResult(
                name="IPC port availability",
                status=Status.WARN if busy else Status.OK,
                detail="in use (may be this workspace's IDE)" if busy else "free",
            )
        )
    else:
        results.append(
            CheckResult(name="IPC port consistency", status=Status.WARN, detail="no port recorded")
        )

    source_path = env.get("SOURCE_PATH")
    if source_path and needs_ascii_link(Path(source_path)):
        results.append(
            CheckResult(
                name="ASCII source path",
                status=Status.WARN,
                detail=f"SOURCE_PATH not Docker-safe: {source_path}",
                hint="run `harness link-ascii`",
            )
        )
    return results


def run_doctor(
    target_dir: Path,
    *,
    runner: CommandRunner = run,
    which: Callable[[str], str | None] = shutil.which,
    is_free: Callable[[int], bool] = is_port_free,
    platform_dir: Path | None = None,
) -> list[CheckResult]:
    """Run every pre-flight check without modifying anything.

    Args:
        target_dir: Workspace root.
        runner: Command runner, injectable for tests.
        which: Executable lookup, injectable for tests.
        is_free: Port probe, injectable for tests.
        platform_dir: Platform dir override; autodetected when None.

    Returns:
        Ordered check results.
    """
    platform = platform_dir or find_platform_dir()
    results = [
        _check_versioned(
            "vrunner",
            "vrunner",
            ["--version"],
            3,
            required=True,
            hint="install vanessa-runner 3.x (opm install vanessa-runner)",
            which=which,
            runner=runner,
        ),
        _check_versioned(
            "oscript",
            "oscript",
            ["--version"],
            2,
            required=True,
            hint="install OneScript 2.0+",
            which=which,
            runner=runner,
        ),
        _check_ibcmd(target_dir.resolve(), platform, which),
    ]
    if platform is None:
        results.append(
            CheckResult(
                name="1cv8 platform",
                status=Status.FAIL,
                detail="no installation found",
                hint="install 1C:Enterprise 8.3.20+ or pass --platform-dir",
            )
        )
    else:
        has_exe = which("1cv8") is not None or any((platform / "bin").glob("1cv8*"))
        results.append(
            CheckResult(
                name="1cv8 platform",
                status=Status.OK if has_exe else Status.FAIL,
                detail=str(platform),
                hint="" if has_exe else "1cv8 executable missing in bin/",
            )
        )
        hbk = find_hbk_files(platform, limit=1)
        results.append(
            CheckResult(
                name=".hbk help files",
                status=Status.OK if hbk else Status.FAIL,
                detail="found" if hbk else "none under bin/",
                hint="" if hbk else "install the platform help component",
            )
        )
    docker_ok, docker_detail = docker_daemon_available(runner)
    results.append(
        CheckResult(
            name="docker daemon",
            status=Status.OK if docker_ok else Status.WARN,
            detail=docker_detail,
            hint="" if docker_ok else "start Docker Desktop (needed for bsl-atlas)",
        )
    )
    for tool, hint in (
        ("git", "needed by setup-skills"),
        ("java", "needed by bsl-language-server"),
        ("bsl-language-server", "install from 1c-syntax/bsl-language-server"),
        ("gitrules", "install via opm install gitrules (needed for exchange rules)"),
    ):
        found = which(tool) is not None
        results.append(
            CheckResult(
                name=tool,
                status=Status.OK if found else Status.WARN,
                detail="found" if found else "not found on PATH",
                hint="" if found else hint,
            )
        )
    results.extend(check_workspace(target_dir.resolve(), is_free))
    return results


def has_failures(results: list[CheckResult], *, strict: bool = False) -> bool:
    """Decide whether results should produce a non-zero exit code.

    Args:
        results: Check results.
        strict: Treat warnings as failures.

    Returns:
        True if any check failed (or warned, when strict).
    """
    bad = {Status.FAIL, Status.WARN} if strict else {Status.FAIL}
    return any(r.status in bad for r in results)
