"""Tests for platform discovery and the doctor command."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fakes import FakeRunner
from harness.cli import app
from harness.services.doctor import Status, check_workspace, has_failures, run_doctor
from harness.services.init import InitOptions, init_workspace
from harness.system.platform import find_hbk_files, find_platform_dir


def _make_platform(root: Path, version: str, *, hbk: bool = True) -> Path:
    bin_dir = root / version / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "1cv8.exe").write_text("", encoding="utf-8")
    if hbk:
        (bin_dir / "shcntx_ru.hbk").write_text("", encoding="utf-8")
    return root / version


def test_find_platform_dir_picks_newest(tmp_path: Path) -> None:
    _make_platform(tmp_path, "8.3.9.1")
    newest = _make_platform(tmp_path, "8.3.27.1606")
    (tmp_path / "common").mkdir()
    assert find_platform_dir([tmp_path, tmp_path / "missing"]) == newest
    assert find_platform_dir([tmp_path / "missing"]) is None


def test_find_hbk(tmp_path: Path) -> None:
    with_help = _make_platform(tmp_path, "8.3.27.1606")
    without = _make_platform(tmp_path, "8.3.1.1", hbk=False)
    assert find_hbk_files(with_help)
    assert not find_hbk_files(without)


def _doctor(
    root: Path, platform: Path | None, *, tools: set[str], version: str = "3.0.2"
) -> dict[str, Status]:
    results = run_doctor(
        root,
        runner=FakeRunner(stdout=version),
        which=lambda exe: exe if exe in tools else None,
        is_free=lambda _: True,
        platform_dir=platform,
    )
    return {r.name: r.status for r in results}


def test_doctor_all_green(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    init_workspace(InitOptions(target_dir=root))
    platform = _make_platform(tmp_path / "1cv8", "8.3.27.1606")
    tools = {"vrunner", "oscript", "ibcmd", "git", "java", "bsl-language-server", "docker"}
    status = _doctor(root, platform, tools=tools, version="3.0.2")
    assert status["vrunner"] is Status.OK
    assert status["1cv8 platform"] is Status.OK
    assert status[".hbk help files"] is Status.OK
    assert status["docker daemon"] is Status.OK
    assert status["workspace layout"] is Status.OK
    assert status["IPC port consistency"] is Status.OK


def test_doctor_reports_missing_tools(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    results = run_doctor(
        root,
        runner=FakeRunner(fail_when=lambda _: True),
        which=lambda _: None,
        is_free=lambda _: True,
        platform_dir=tmp_path / "none",
    )
    by_name = {r.name: r for r in results}
    assert by_name["vrunner"].status is Status.FAIL
    assert by_name["oscript"].status is Status.FAIL
    assert by_name["ibcmd"].status is Status.WARN
    assert by_name["docker daemon"].status is Status.WARN
    assert by_name["workspace layout"].status is Status.FAIL
    assert has_failures(results)


def test_doctor_old_version_fails(tmp_path: Path) -> None:
    status = _doctor(tmp_path, None, tools={"vrunner", "oscript"}, version="1.9.0")
    assert status["vrunner"] is Status.FAIL


def test_doctor_strict_counts_warnings(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    results = run_doctor(
        root,
        runner=FakeRunner(),
        which=lambda _: None,
        is_free=lambda _: True,
        platform_dir=tmp_path,
    )
    only_warn = [r for r in results if r.status is Status.WARN]
    assert only_warn
    assert not has_failures(only_warn)
    assert has_failures(only_warn, strict=True)


def test_workspace_check_detects_port_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    init_workspace(InitOptions(target_dir=root, ide=True))
    mcp = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
    mcp["mcpServers"]["mcp-1c-platform-tools"]["env"]["ONEC_IPC_PORT"] = "40999"
    (root / ".mcp.json").write_text(json.dumps(mcp), encoding="utf-8")
    results = {r.name: r for r in check_workspace(root, lambda _: True)}
    assert results["IPC port consistency"].status is Status.FAIL


def test_workspace_check_busy_port_warns(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    init_workspace(InitOptions(target_dir=root))
    results = {r.name: r for r in check_workspace(root, lambda _: False)}
    assert results["IPC port availability"].status is Status.WARN


def test_cli_init_reports_os_error_without_traceback(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "inbox").write_text("blocker", encoding="utf-8")
    result = CliRunner().invoke(app, ["init", str(root)])
    output = result.output + getattr(result, "stderr", "")
    assert result.exit_code == 1
    assert "cannot create workspace at" in output
    assert "Traceback" not in output


def test_doctor_hints_ibcmd_absent_from_platform(tmp_path: Path) -> None:
    platform = tmp_path / "1cv8" / "8.3.27"
    (platform / "bin").mkdir(parents=True)
    results = run_doctor(
        tmp_path,
        runner=FakeRunner(),
        which=lambda _: None,
        is_free=lambda _: True,
        platform_dir=platform,
    )
    ibcmd = next(r for r in results if r.name == "ibcmd")
    assert ibcmd.status is Status.WARN
    assert "ibcmd" in ibcmd.hint
    assert "false" in ibcmd.hint


def test_doctor_accepts_ibcmd_disabled_in_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("harness.services.init.shutil.which", lambda _: None)
    platform = tmp_path / "1cv8" / "8.3.27"
    (platform / "bin").mkdir(parents=True)
    root = tmp_path / "proj"
    init_workspace(InitOptions(target_dir=root, platform_dir=platform))
    results = run_doctor(
        root,
        runner=FakeRunner(),
        which=lambda _: None,
        is_free=lambda _: True,
        platform_dir=platform,
    )
    ibcmd = next(r for r in results if r.name == "ibcmd")
    assert ibcmd.status is Status.OK
    assert "disabled" in ibcmd.detail


def test_workspace_check_flags_flat_vrunner_settings(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    init_workspace(InitOptions(target_dir=root))
    (root / "autumn-properties.json").write_text(
        '{"ibconnection": "/F./build/ib"}', encoding="utf-8"
    )
    results = {r.name: r for r in check_workspace(root, lambda _: True)}
    assert results["vrunner settings"].status is Status.FAIL


def test_workspace_check_accepts_v3_settings(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    init_workspace(InitOptions(target_dir=root))
    results = {r.name: r for r in check_workspace(root, lambda _: True)}
    assert results["vrunner settings"].status is Status.OK


def test_cli_init_and_help(tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["--help"]).exit_code == 0
    result = runner.invoke(app, ["init", str(tmp_path / "w"), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "w").exists()
    missing = runner.invoke(app, ["link-ascii", str(tmp_path / "nothing")])
    assert missing.exit_code == 1
