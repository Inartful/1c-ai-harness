"""Shared test doubles."""

from collections.abc import Callable, Sequence
from pathlib import Path

from harness.errors import CommandFailedError
from harness.system.runner import CommandResult


class FakeRunner:
    """Records calls and returns canned results, optionally with side effects."""

    def __init__(
        self,
        stdout: str = "",
        *,
        fail_when: Callable[[Sequence[str]], bool] | None = None,
        on_call: Callable[[Sequence[str]], None] | None = None,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._stdout = stdout
        self._fail_when = fail_when
        self._on_call = on_call

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout_s: float,
        cwd: Path | None = None,
        check: bool = True,
    ) -> CommandResult:
        assert timeout_s > 0
        self.calls.append(tuple(argv))
        if self._fail_when is not None and self._fail_when(argv):
            msg = f"fake failure: {' '.join(argv)}"
            raise CommandFailedError(msg)
        if self._on_call is not None:
            self._on_call(argv)
        return CommandResult(
            argv=tuple(argv), returncode=0, stdout=self._stdout, stderr="", duration_s=0.0
        )
