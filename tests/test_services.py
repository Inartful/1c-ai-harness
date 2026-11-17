"""Tests for the init, link-ascii and setup-skills services."""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from fakes import FakeRunner
from harness.errors import ConfigError
from harness.models import VSCodeSettings
from harness.services.init import InitOptions, init_workspace
from harness.services.link_ascii import link_ascii
from harness.services.setup_skills import SkillSource, setup_skills
from harness.services.writer import WriteAction
from harness.system.links import LinkResult


def _platform_dir(tmp_path: Path, *, ibcmd: bool) -> Path:
    platform = tmp_path / "1cv8" / "8.3.27"
    (platform / "bin").mkdir(parents=True)
    if ibcmd:
        (platform / "bin" / "ibcmd.exe").write_text("", encoding="utf-8")
    return platform


def test_init_creates_tree_and_configs(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    report = init_workspace(
        InitOptions(target_dir=root, platform_dir=_platform_dir(tmp_path, ibcmd=True))
    )
    dirs = ("inbox", "src/cf", "src/cfe", "src/epf", "data", "build/ib", "build/reports")
    for rel in (*dirs, ".agents/skills", ".agents/rules"):
        assert (root / rel).is_dir()
    files = (
        "AGENTS.md",
        "CLAUDE.md",
        "packagedef",
        "autumn-properties.json",
        ".bsl-language-server.json",
        ".mcp.json",
        ".cursor/mcp.json",
        "docker-compose.yml",
        ".env",
        ".vscode/settings.json",
    )
    for rel in files:
        assert (root / rel).is_file(), rel
    autumn = json.loads((root / "autumn-properties.json").read_text(encoding="utf-8"))
    assert autumn["vrunner"]["ibcmd"] is True
    assert autumn["vrunner"]["ibconnection"] == "/F./build/ib"
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == agents
    assert 40241 <= report.ipc_port < 41001
    assert not list((root / ".agents" / "skills").iterdir())


def test_init_writes_full_indexing_mode(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    init_workspace(InitOptions(target_dir=root, indexing_mode="full"))
    content = (root / ".env").read_text(encoding="utf-8")
    assert "INDEXING_MODE=full" in content


def test_init_refreshes_claude_copy_of_generated_agents(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    init_workspace(InitOptions(target_dir=root))
    (root / "AGENTS.md").write_text("old protocol", encoding="utf-8")
    (root / "CLAUDE.md").write_text("old protocol", encoding="utf-8")
    init_workspace(InitOptions(target_dir=root, force=True))
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == agents


def test_init_keeps_hand_edited_claude_md(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    init_workspace(InitOptions(target_dir=root))
    (root / "CLAUDE.md").write_text("my own notes", encoding="utf-8")
    init_workspace(InitOptions(target_dir=root, force=True))
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == "my own notes"


def test_init_syncs_port_everywhere(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    report = init_workspace(InitOptions(target_dir=root, ide=True))
    port = report.ipc_port
    vs = VSCodeSettings.from_text((root / ".vscode/settings.json").read_text(encoding="utf-8"))
    assert vs.ipc_port() == port
    assert f"ONEC_IPC_PORT={port}" in (root / ".env").read_text(encoding="utf-8")
    for rel in (".mcp.json", ".cursor/mcp.json"):
        data = json.loads((root / rel).read_text(encoding="utf-8"))
        assert data["mcpServers"]["mcp-1c-platform-tools"]["env"]["ONEC_IPC_PORT"] == str(port)
    assert any("PLATFORM_TOOLS_JS" in w for w in report.warnings)


def test_init_is_idempotent_and_stable_port(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    first = init_workspace(InitOptions(target_dir=root))
    second = init_workspace(InitOptions(target_dir=root))
    assert second.ipc_port == first.ipc_port
    generated = {a.path.name: a.action for a in second.actions}
    assert generated["AGENTS.md"] is WriteAction.UNCHANGED


def test_init_does_not_overwrite_without_force(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    init_workspace(InitOptions(target_dir=root))
    (root / "AGENTS.md").write_text("custom", encoding="utf-8")
    init_workspace(InitOptions(target_dir=root))
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == "custom"
    init_workspace(InitOptions(target_dir=root, force=True))
    assert (root / "AGENTS.md").read_text(encoding="utf-8") != "custom"


def test_init_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    report = init_workspace(InitOptions(target_dir=root, dry_run=True))
    assert not root.exists()
    assert all(a.action is WriteAction.PLANNED for a in report.actions)


def test_init_preserves_user_vscode_settings(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / ".vscode").mkdir(parents=True)
    (root / ".vscode/settings.json").write_text('{"editor.tabSize": 2}', encoding="utf-8")
    init_workspace(InitOptions(target_dir=root))
    data = json.loads((root / ".vscode/settings.json").read_text(encoding="utf-8"))
    assert data["editor.tabSize"] == 2
    assert "1c-platform-tools.ipc.port" in data


def test_init_warns_on_jsonc_settings(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / ".vscode").mkdir(parents=True)
    (root / ".vscode/settings.json").write_text("// comment\n{}", encoding="utf-8")
    report = init_workspace(InitOptions(target_dir=root))
    assert any("not plain JSON" in w for w in report.warnings)
    assert (root / ".vscode/settings.json").read_text(encoding="utf-8").startswith("//")


def test_init_reports_blocked_path_as_config_error(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "inbox").write_text("blocker", encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        init_workspace(InitOptions(target_dir=root))
    message = str(info.value)
    assert "cannot create workspace at" in message
    assert "inbox" in message


def test_init_disables_ibcmd_when_platform_lacks_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("harness.services.init.shutil.which", lambda _: None)
    root = tmp_path / "proj"
    report = init_workspace(
        InitOptions(target_dir=root, platform_dir=_platform_dir(tmp_path, ibcmd=False))
    )
    data = json.loads((root / "autumn-properties.json").read_text(encoding="utf-8"))
    assert data["vrunner"]["ibcmd"] is False
    assert any("ibcmd" in warning for warning in report.warnings)


def test_init_keeps_ibcmd_when_platform_ships_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("harness.services.init.shutil.which", lambda _: None)
    root = tmp_path / "proj"
    report = init_workspace(
        InitOptions(target_dir=root, platform_dir=_platform_dir(tmp_path, ibcmd=True))
    )
    data = json.loads((root / "autumn-properties.json").read_text(encoding="utf-8"))
    assert data["vrunner"]["ibcmd"] is True
    assert not any("ibcmd" in warning for warning in report.warnings)


def test_init_writes_explicit_mcp_python(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    init_workspace(InitOptions(target_dir=root, mcp_python=Path("C:/py/python.exe")))
    data = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["bsl-platform-help"]["command"] == "C:/py/python.exe"


def test_init_replaces_store_python_alias_with_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alias = tmp_path / "WindowsApps" / "python.exe"
    alias.parent.mkdir(parents=True)
    alias.write_bytes(b"")
    monkeypatch.setattr("harness.services.init.shutil.which", lambda _: str(alias))
    monkeypatch.setattr("harness.services.init.sys.platform", "win32")
    root = tmp_path / "proj"
    report = init_workspace(InitOptions(target_dir=root))
    data = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["bsl-platform-help"]["command"] == "<MCP_PYTHON>"
    assert any("Microsoft Store" in warning for warning in report.warnings)


def test_init_uses_placeholder_when_python_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("harness.services.init.shutil.which", lambda _: None)
    root = tmp_path / "proj"
    report = init_workspace(InitOptions(target_dir=root))
    data = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["bsl-platform-help"]["command"] == "<MCP_PYTHON>"
    assert any("python not found" in warning for warning in report.warnings)


def test_init_gitignore_append_is_deduplicated(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".gitignore").write_text("node_modules/", encoding="utf-8")
    init_workspace(InitOptions(target_dir=root))
    init_workspace(InitOptions(target_dir=root))
    lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines.count("build/") == 1
    assert "node_modules/" in lines


def test_link_ascii_updates_env(tmp_path: Path) -> None:
    root = tmp_path / "проект x"
    init_workspace(InitOptions(target_dir=root))
    runner = FakeRunner()
    report = link_ascii(root, tmp_path / "links", runner=runner)
    assert report.needed
    assert report.link is not None
    assert f"SOURCE_PATH={report.link.as_posix()}" in (root / ".env").read_text(encoding="utf-8")
    dry = link_ascii(root, tmp_path / "other", dry_run=True, runner=runner)
    assert dry.dry_run


def test_link_ascii_requires_init(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        link_ascii(tmp_path)


def _fake_clone(argv: Sequence[str]) -> None:
    if "clone" in argv:
        (Path(argv[-1]) / ".git").mkdir(parents=True, exist_ok=True)


def test_setup_skills_clones_and_links(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    init_workspace(InitOptions(target_dir=root))
    runner = FakeRunner(on_call=_fake_clone)
    report = setup_skills(root, SkillSource.DESKO77, ref="main", runner=runner)
    clone = next(c for c in runner.calls if "clone" in c)
    assert clone[:5] == ("git", "clone", "--depth", "1", "--branch")
    assert "Desko77/claude-code-skills-1c" in clone[-2]
    assert set(report.links) == {".claude/skills", ".cursor/skills"}
    assert all(v is LinkResult.CREATED for v in report.links.values())


def test_setup_skills_update_pulls(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    init_workspace(InitOptions(target_dir=root))
    (root / ".agents/skills/.git").mkdir()
    runner = FakeRunner()
    setup_skills(root, update=True, runner=runner)
    assert any("pull" in c for c in runner.calls)


def test_setup_skills_refuses_foreign_dir(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    init_workspace(InitOptions(target_dir=root))
    (root / ".agents/skills/mine.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ConfigError, match="not empty"):
        setup_skills(root, runner=FakeRunner())


def test_setup_skills_local_requires_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="local-path"):
        setup_skills(tmp_path, SkillSource.LOCAL, runner=FakeRunner())
