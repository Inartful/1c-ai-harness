"""Implementation of `harness setup-skills`."""

import shutil
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from harness.errors import ConfigError
from harness.system.links import LinkResult, create_dir_link
from harness.system.runner import CommandRunner, run

_GIT_TIMEOUT_S = 600.0
_AGENT_SKILL_DIRS = (Path(".claude/skills"), Path(".cursor/skills"))


class SkillSource(StrEnum):
    """Supported skill libraries."""

    DESKO77 = "desko77"
    SHIROKOV = "shirokov"
    LOCAL = "local"


SOURCE_URLS: dict[SkillSource, str] = {
    SkillSource.DESKO77: "https://github.com/Desko77/claude-code-skills-1c.git",
    SkillSource.SHIROKOV: "https://github.com/Nikolay-Shirokov/cc-1c-skills.git",
}


class SetupSkillsReport(BaseModel):
    """Outcome of `setup-skills`."""

    skills_dir: Path
    source: SkillSource
    links: dict[str, LinkResult] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


def _is_empty(path: Path) -> bool:
    """Tell whether a directory is missing or has no entries besides `.gitkeep`."""
    return not path.exists() or all(p.name == ".gitkeep" for p in path.iterdir())


def setup_skills(
    target_dir: Path,
    source: SkillSource = SkillSource.DESKO77,
    *,
    local_path: Path | None = None,
    ref: str | None = None,
    update: bool = False,
    runner: CommandRunner = run,
) -> SetupSkillsReport:
    """Fetch a skill library into `.agents/skills` and link it for Claude and Cursor.

    Args:
        target_dir: Workspace root.
        source: Which library to use; LOCAL links an existing checkout.
        local_path: Existing checkout, required for LOCAL.
        ref: Optional git branch or tag to clone.
        update: Run `git pull --ff-only` when the library is already cloned.
        runner: Command runner, injectable for tests.

    Returns:
        A report with link results.

    Raises:
        ConfigError: If options are inconsistent or the skills directory is occupied.
        HarnessError: Subclasses on git or link failures.
    """
    root = target_dir.resolve()
    skills = root / ".agents" / "skills"
    report = SetupSkillsReport(skills_dir=skills, source=source)
    if source is SkillSource.LOCAL:
        if local_path is None:
            msg = "--local-path is required with source 'local'"
            raise ConfigError(msg)
        if skills.is_dir() and not skills.is_symlink() and _is_empty(skills):
            shutil.rmtree(skills)
        report.links[str(skills.relative_to(root))] = create_dir_link(
            skills, local_path.resolve(), runner=runner
        )
    elif (skills / ".git").is_dir():
        if update:
            runner(["git", "-C", str(skills), "pull", "--ff-only"], timeout_s=_GIT_TIMEOUT_S)
            report.notes.append("updated existing clone")
        else:
            report.notes.append("already cloned; use --update to pull")
    elif _is_empty(skills):
        skills.parent.mkdir(parents=True, exist_ok=True)
        for keep in skills.glob(".gitkeep"):
            keep.unlink()
        cmd = ["git", "clone", "--depth", "1"]
        if ref:
            cmd += ["--branch", ref]
        runner([*cmd, SOURCE_URLS[source], str(skills)], timeout_s=_GIT_TIMEOUT_S)
    else:
        msg = f"{skills} is not empty and is not a git clone"
        raise ConfigError(msg)

    for rel in _AGENT_SKILL_DIRS:
        report.links[rel.as_posix()] = create_dir_link(root / rel, skills, runner=runner)
    report.notes.append("optional: run `python scripts/switch.py --runtime python` in the library")
    return report
