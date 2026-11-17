"""Tests for the `build` and `check` services."""

from pathlib import Path

import pytest

from fakes import FakeRunner
from harness.errors import ConfigError
from harness.services.build import build_workspace
from harness.services.check import count_severities, run_check


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "src" / "cf").mkdir(parents=True)
    (tmp_path / "build" / "ib").mkdir(parents=True)
    return tmp_path


def _ibcmd(tmp_path: Path) -> Path:
    exe = tmp_path / "ibcmd.exe"
    exe.write_text("", encoding="utf-8")
    return exe


def test_build_imports_and_saves(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    runner = FakeRunner()
    report = build_workspace(root, ibcmd=_ibcmd(tmp_path), runner=runner)
    assert [stage.name for stage in report.stages] == ["import", "save"]
    assert runner.calls[0][1:4] == ("infobase", "config", "import")
    assert f"--db-path={root.resolve() / 'build' / 'ib'}" in runner.calls[0]
    assert str(root.resolve() / "src" / "cf") in runner.calls[0]
    assert runner.calls[1][3] == "save"
    assert runner.calls[1][4] == f"--db-path={root.resolve() / 'build' / 'ib'}"
    assert runner.calls[1][5] == "--db"
    assert runner.calls[1][6].endswith(".cf")


def test_build_skip_import_and_skip_save(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    runner = FakeRunner()
    build_workspace(root, skip_import=True, ibcmd=_ibcmd(tmp_path), runner=runner)
    assert [call[3] for call in runner.calls] == ["save"]
    runner = FakeRunner()
    build_workspace(root, skip_save=True, ibcmd=_ibcmd(tmp_path), runner=runner)
    assert [call[3] for call in runner.calls] == ["import"]


def test_build_no_check_flag(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    runner = FakeRunner()
    build_workspace(root, no_check=True, ibcmd=_ibcmd(tmp_path), runner=runner)
    assert "--no-check" in runner.calls[0]


def test_build_requires_sources(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="dump not found"):
        build_workspace(tmp_path, ibcmd=_ibcmd(tmp_path), runner=FakeRunner())


def test_build_requires_ibcmd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _workspace(tmp_path)
    monkeypatch.setattr("harness.services.build.shutil.which", lambda _: None)
    monkeypatch.setattr("harness.services.build.find_platform_dir", lambda: None)
    with pytest.raises(ConfigError, match="ibcmd not found"):
        build_workspace(tmp_path, runner=FakeRunner())


def test_count_severities_small_report(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text(
        '{"fileinfos":[{"diagnostics":[{"severity":"Error"},{"severity":"Warning"}]},'
        '{"diagnostics":[{"severity":"Error"}]}]}',
        encoding="utf-8",
    )
    assert count_severities(report) == {"Error": 2, "Warning": 1}


def test_count_severities_streams_large_report(tmp_path: Path) -> None:
    report = tmp_path / "big.json"
    with report.open("w", encoding="utf-8") as handle:
        handle.write('{"fileinfos":[')
        for _ in range(200_000):
            handle.write('{"diagnostics":[{"severity":"Error"}]},')
        handle.write('{"diagnostics":[]}]}')
    assert count_severities(report) == {"Error": 200_000}


def test_run_check_reads_report(tmp_path: Path) -> None:
    root = tmp_path
    (root / "src" / "cf").mkdir(parents=True)
    (root / ".bsl-language-server.json").write_text("{}", encoding="utf-8")
    report_dir = root / "build" / "reports" / "bsl"
    report_dir.mkdir(parents=True)
    (report_dir / "bsl-json.json").write_text(
        '{"fileinfos":[{"diagnostics":[{"severity":"Error"}]}]}', encoding="utf-8"
    )
    bsl = tmp_path / "bsl-language-server.exe"
    bsl.write_text("", encoding="utf-8")
    runner = FakeRunner()
    report = run_check(root, bsl=bsl, runner=runner)
    assert report.counts == {"Error": 1}
    assert report.total == 1
    assert runner.calls[0][1] == "analyze"
    assert report.report is not None


def test_run_check_requires_sources(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="dump not found"):
        run_check(tmp_path, bsl=tmp_path / "bsl.exe", runner=FakeRunner())


def test_check_requires_bsl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "src" / "cf").mkdir(parents=True)
    monkeypatch.setattr("harness.services.check.shutil.which", lambda _: None)
    with pytest.raises(ConfigError, match="not found"):
        run_check(tmp_path, runner=FakeRunner())
