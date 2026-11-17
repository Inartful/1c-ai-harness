"""Tests for the safe subprocess wrapper and link manager."""

import sys
from pathlib import Path

import pytest

from fakes import FakeRunner
from harness.errors import (
    CommandFailedError,
    CommandTimeoutError,
    LinkError,
    ToolNotFoundError,
)
from harness.system.links import LinkResult, create_dir_link, create_file_link
from harness.system.runner import run


def test_run_captures_output() -> None:
    result = run([sys.executable, "-c", "print('hi')"], timeout_s=30)
    assert result.returncode == 0
    assert result.stdout.strip() == "hi"


def test_run_raises_on_nonzero() -> None:
    with pytest.raises(CommandFailedError):
        run([sys.executable, "-c", "import sys; sys.exit(3)"], timeout_s=30)


def test_run_check_false_returns_code() -> None:
    result = run([sys.executable, "-c", "import sys; sys.exit(3)"], timeout_s=30, check=False)
    assert result.returncode == 3


def test_run_timeout() -> None:
    with pytest.raises(CommandTimeoutError):
        run([sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=0.5)


def test_run_missing_tool() -> None:
    with pytest.raises(ToolNotFoundError):
        run(["definitely-not-a-real-tool-xyz"], timeout_s=5)


def test_run_validates_arguments() -> None:
    with pytest.raises(ValueError, match="argv"):
        run([], timeout_s=5)
    with pytest.raises(ValueError, match="timeout"):
        run([sys.executable], timeout_s=0)


def test_dir_link_exists_when_same_target(tmp_path: Path) -> None:
    assert create_dir_link(tmp_path, tmp_path) is LinkResult.EXISTS


def test_dir_link_refuses_conflict(tmp_path: Path) -> None:
    target = tmp_path / "t"
    other = tmp_path / "o"
    target.mkdir()
    other.mkdir()
    with pytest.raises(LinkError, match="already exists"):
        create_dir_link(other, target)


def test_dir_link_requires_target(tmp_path: Path) -> None:
    with pytest.raises(LinkError, match="not a directory"):
        create_dir_link(tmp_path / "l", tmp_path / "missing")


def test_dir_link_windows_prefers_junction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    target = tmp_path / "t"
    target.mkdir()
    runner = FakeRunner()
    assert create_dir_link(tmp_path / "l", target, runner=runner) is LinkResult.CREATED
    assert runner.calls[0][:4] == ("cmd", "/c", "mklink", "/J")
    assert len(runner.calls) == 1


def test_dir_link_windows_falls_back_to_symlink_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    target = tmp_path / "t"
    target.mkdir()
    runner = FakeRunner(fail_when=lambda argv: "/J" in argv)
    assert create_dir_link(tmp_path / "l", target, runner=runner) is LinkResult.CREATED
    assert [c[3] for c in runner.calls] == ["/J", "/D"]


def test_dir_link_windows_all_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    target = tmp_path / "t"
    target.mkdir()
    with pytest.raises(LinkError):
        create_dir_link(tmp_path / "l", target, runner=FakeRunner(fail_when=lambda _: True))


def test_file_link_created_or_copied(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("x", encoding="utf-8")
    result = create_file_link(tmp_path / "CLAUDE.md", tmp_path / "AGENTS.md")
    assert result in {LinkResult.CREATED, LinkResult.COPIED}
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "x"
    if result is LinkResult.CREATED:
        assert create_file_link(tmp_path / "CLAUDE.md", tmp_path / "AGENTS.md") is LinkResult.EXISTS
    else:
        with pytest.raises(LinkError):
            create_file_link(tmp_path / "CLAUDE.md", tmp_path / "AGENTS.md")
