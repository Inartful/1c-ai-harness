"""Tests for the `fix-permissions` service."""

import sys
from pathlib import Path

import pytest

from fakes import FakeRunner
from harness.errors import ConfigError
from harness.services.fix_permissions import fix_permissions


def test_fix_permissions_runs_three_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERNAME", "user")
    monkeypatch.setenv("USERDOMAIN", "DOM")
    runner = FakeRunner()
    report = fix_permissions(tmp_path, runner=runner)
    assert [step.name for step in report.steps] == [
        "grant owner full control",
        "re-enable inheritance",
        "apply low integrity label",
    ]
    assert all(step.ok for step in report.steps)
    assert len(runner.calls) == 3
    assert runner.calls[0][1:3] == (str(tmp_path.resolve()), "/grant:r")
    assert runner.calls[0][3] == "DOM\\user:(OI)(CI)F"
    assert runner.calls[1][2] == "/inheritance:e"
    assert runner.calls[2][2] == "/setintegritylevel"
    assert runner.calls[2][3] == "(OI)(CI)L"


def test_fix_permissions_dry_run_does_not_touch_acl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERNAME", "user")
    runner = FakeRunner()
    report = fix_permissions(tmp_path, dry_run=True, runner=runner)
    assert runner.calls == []
    assert len(report.steps) == 3
    assert report.dry_run


def test_fix_permissions_is_windows_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(ConfigError, match="Windows"):
        fix_permissions(tmp_path, runner=FakeRunner())


def test_fix_permissions_requires_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(ConfigError, match="not a directory"):
        fix_permissions(tmp_path / "missing", runner=FakeRunner())


def test_fix_permissions_stops_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERNAME", "user")
    runner = FakeRunner(fail_when=lambda argv: "/inheritance:e" in argv)
    report = fix_permissions(tmp_path, runner=runner)
    assert [step.ok for step in report.steps] == [True, False]
    assert report.warnings
