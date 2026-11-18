"""Thin, tested wrappers around the 1C platform CLIs: `ibcmd` and `1cv8 DESIGNER`.

Everything that touches an infobase goes through `Ibcmd` or `Designer` so the flags that were
verified against a real platform (8.3.27) live in one place.
"""

import os
import re
import shutil
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel

from harness.errors import CommandFailedError, ConfigError
from harness.services.build import BuildStage, find_ibcmd
from harness.system.platform import find_platform_dir
from harness.system.runner import CommandResult, CommandRunner, run

_DESIGNER_NAME = "1cv8.exe" if sys.platform == "win32" else "1cv8"
_LONG_TIMEOUT_S = 3600.0
_SHORT_TIMEOUT_S = 600.0
# `ibcmd ... import files` takes the list on the command line; above this the import is full.
MAX_PARTIAL_ARGV_CHARS = 24000


class Credentials(BaseModel):
    """Infobase user (file infobases restored from a client `.dt` usually have users)."""

    user: str | None = None
    password: str | None = None

    @classmethod
    def from_env(cls, user: str | None = None, password: str | None = None) -> "Credentials":
        """Fill missing values from `ONEC_IB_USR` / `ONEC_IB_PWD`.

        Args:
            user: Explicit user name.
            password: Explicit password.

        Returns:
            Resolved credentials (possibly empty).
        """
        return cls(
            user=user if user is not None else os.environ.get("ONEC_IB_USR") or None,
            password=password if password is not None else os.environ.get("ONEC_IB_PWD") or None,
        )


class Ibcmd:
    """`ibcmd infobase ...` bound to one file infobase."""

    def __init__(
        self,
        db_path: Path,
        *,
        executable: Path | None = None,
        credentials: Credentials | None = None,
        runner: CommandRunner = run,
    ) -> None:
        """Bind to an infobase directory.

        Args:
            db_path: File infobase directory (may not exist yet).
            executable: Explicit `ibcmd` path; discovered when omitted.
            credentials: Infobase user for operations on a protected base.
            runner: Command runner, injectable for tests.
        """
        self.db_path = db_path.resolve()
        self.executable = find_ibcmd(executable)
        self.credentials = credentials or Credentials()
        self.runner = runner
        self.stages: list[BuildStage] = []

    @property
    def exists(self) -> bool:
        """Whether the file infobase has been created."""
        return (self.db_path / "1Cv8.1CD").is_file()

    def _auth(self) -> list[str]:
        args: list[str] = []
        if self.credentials.user:
            args.append(f"--user={self.credentials.user}")
        if self.credentials.password:
            args.append(f"--password={self.credentials.password}")
        return args

    def _run(
        self, stage: str, args: Sequence[str], *, timeout_s: float = _LONG_TIMEOUT_S
    ) -> CommandResult:
        argv = [str(self.executable), "infobase", *args]
        try:
            result = self.runner(argv, timeout_s=timeout_s)
        except CommandFailedError as exc:
            msg = f"ibcmd {stage} failed: {_ibcmd_errors(str(exc))}"
            raise CommandFailedError(msg) from exc
        self.stages.append(BuildStage(name=stage, seconds=result.duration_s, ok=True))
        return result

    def _db(self) -> list[str]:
        return [f"--db-path={self.db_path}", *self._auth()]

    def create(self, *, restore: Path | None = None) -> None:
        """Create an empty infobase, optionally restoring a `.dt` into it."""
        self.db_path.mkdir(parents=True, exist_ok=True)
        extra = [f"--restore={restore.resolve()}"] if restore is not None else []
        self._run("create" if restore is None else "restore-dt", ["create", *self._db(), *extra])

    def ensure(self) -> None:
        """Create the infobase when it does not exist yet."""
        if not self.exists:
            self.create()

    def dump(self, target: Path) -> None:
        """Write the whole infobase into a `.dt` file."""
        target.parent.mkdir(parents=True, exist_ok=True)
        self._run("dump-dt", ["dump", *self._db(), str(target.resolve())])

    def load(self, binary: Path, *, extension: str | None = None) -> None:
        """Load a `.cf` (or `.cfe` with `extension`) into the infobase."""
        ext = [f"--extension={extension}"] if extension else []
        name = "load-cfe" if extension else "load-cf"
        self._run(name, ["config", "load", *self._db(), *ext, "--force", str(binary.resolve())])

    def export(self, out: Path, *, extension: str | None = None) -> None:
        """Export the configuration (or one extension) into an XML tree."""
        out.mkdir(parents=True, exist_ok=True)
        ext = [f"--extension={extension}"] if extension else []
        self._run("export-xml", ["config", "export", *self._db(), *ext, str(out.resolve())])

    def export_all_extensions(self, out: Path) -> None:
        """Export every extension into `<out>/<extension name>/`."""
        out.mkdir(parents=True, exist_ok=True)
        self._run(
            "export-extensions",
            ["config", "export", "all-extensions", *self._db(), str(out.resolve())],
        )

    def import_full(self, src: Path, *, extension: str | None = None) -> None:
        """Import an XML tree, replacing the configuration (or one extension)."""
        ext = [f"--extension={extension}"] if extension else []
        self._run("import-xml", ["config", "import", *self._db(), *ext, str(src.resolve())])

    def import_files(
        self, src: Path, files: Sequence[str], *, extension: str | None = None
    ) -> None:
        """Import only the listed files (relative to `src`) with `--partial`."""
        ext = [f"--extension={extension}"] if extension else []
        self._run(
            f"import-files({len(files)})",
            [
                "config",
                "import",
                "files",
                *self._db(),
                *ext,
                f"--base-dir={src.resolve()}",
                "--partial",
                *files,
            ],
        )

    def save(self, target: Path, *, extension: str | None = None) -> None:
        """Save the configuration (or one extension) into a `.cf` / `.cfe` file."""
        target.parent.mkdir(parents=True, exist_ok=True)
        ext = [f"--extension={extension}"] if extension else []
        self._run(
            "save-cfe" if extension else "save-cf",
            ["config", "save", *self._db(), *ext, str(target.resolve())],
        )

    def apply(self, *, extension: str | None = None) -> None:
        """Update the database configuration; for an extension this proves it fits the base."""
        ext = [f"--extension={extension}"] if extension else []
        self._run("apply", ["config", "apply", *self._db(), *ext, "--force"])

    def extensions(self) -> list[str]:
        """List extension names registered in the infobase."""
        result = self._run(
            "list-extensions",
            ["config", "extension", "list", *self._db()],
            timeout_s=_SHORT_TIMEOUT_S,
        )
        return re.findall(r'^name\s*:\s*"([^"]*)"', result.stdout, flags=re.MULTILINE)

    def delete_extension(self, name: str) -> None:
        """Remove an extension from the infobase."""
        self._run(
            "delete-extension",
            ["config", "extension", "delete", *self._db(), f"--name={name}"],
            timeout_s=_SHORT_TIMEOUT_S,
        )


def _ibcmd_errors(text: str) -> str:
    """Keep only `[ERROR]` lines from ibcmd output when there are any."""
    errors = [line.strip() for line in text.splitlines() if "[ERROR]" in line]
    return "\n".join(errors) if errors else text


def find_designer(explicit: Path | None = None) -> Path:
    """Locate `1cv8` (designer).

    Args:
        explicit: Path passed by the user.

    Returns:
        Path to the executable.

    Raises:
        ConfigError: If it cannot be found.
    """
    if explicit is not None:
        if not explicit.is_file():
            msg = f"1cv8 not found at {explicit}"
            raise ConfigError(msg)
        return explicit
    platform = find_platform_dir()
    if platform is not None:
        candidate = platform / "bin" / _DESIGNER_NAME
        if candidate.is_file():
            return candidate
    found = shutil.which("1cv8")
    if found is not None:
        return Path(found)
    msg = "1cv8 (designer) not found; install the 1C:Enterprise platform or pass --designer"
    raise ConfigError(msg)


class DesignerResult(BaseModel):
    """Exit code and the `/Out` log of one designer batch run."""

    returncode: int
    log: str
    seconds: float


class Designer:
    """`1cv8 DESIGNER` batch mode bound to one file infobase."""

    def __init__(
        self,
        db_path: Path,
        *,
        executable: Path | None = None,
        credentials: Credentials | None = None,
        runner: CommandRunner = run,
    ) -> None:
        """Bind to an infobase directory.

        Args:
            db_path: Existing file infobase directory.
            executable: Explicit `1cv8` path; discovered when omitted.
            credentials: Infobase user (`/N`, `/P`).
            runner: Command runner, injectable for tests.
        """
        self.db_path = db_path.resolve()
        self.executable = find_designer(executable)
        self.credentials = credentials or Credentials()
        self.runner = runner

    def run(self, args: Sequence[str], *, timeout_s: float = _LONG_TIMEOUT_S) -> DesignerResult:
        """Run designer in batch mode and return its exit code and log.

        Args:
            args: Designer command-line keys after the connection options.
            timeout_s: Timeout in seconds.

        Returns:
            The exit code and decoded `/Out` log.
        """
        with tempfile.TemporaryDirectory(prefix="harness-designer-") as tmp:
            log = Path(tmp) / "out.log"
            argv = [
                str(self.executable),
                "DESIGNER",
                "/F",
                str(self.db_path),
                *(["/N", self.credentials.user] if self.credentials.user else []),
                *(["/P", self.credentials.password] if self.credentials.password else []),
                "/DisableStartupDialogs",
                "/DisableStartupMessages",
                "/Out",
                str(log),
                *args,
            ]
            result = self.runner(argv, timeout_s=timeout_s, check=False)
            text = read_designer_log(log)
        return DesignerResult(returncode=result.returncode, log=text, seconds=result.duration_s)

    def run_checked(self, stage: str, args: Sequence[str]) -> BuildStage:
        """Run designer and raise with the log when it exits non-zero."""
        res = self.run(args)
        if res.returncode != 0:
            msg = f"designer {stage} failed (code {res.returncode}):\n{res.log.strip()}"
            raise CommandFailedError(msg)
        return BuildStage(name=stage, seconds=res.seconds, ok=True)


def read_designer_log(path: Path) -> str:
    """Decode a designer `/Out` log (UTF-8 with BOM on 8.3.2x, cp1251 on old platforms)."""
    if not path.is_file():
        return ""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1251", errors="replace")


def scratch_ib_dir() -> Path:
    """Directory of the cached empty infobase used for designer-only operations (`.epf`)."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(base) / "1c-harness" / "scratch-ib"


@contextmanager
def temp_infobase(
    keep: Path | None = None,
    *,
    ibcmd: Path | None = None,
    credentials: Credentials | None = None,
    runner: CommandRunner = run,
) -> Iterator[Ibcmd]:
    """Yield an `Ibcmd` for `keep` (created on demand) or for a throwaway infobase.

    Args:
        keep: Persistent infobase directory; a temp dir is used when None.
        ibcmd: Explicit `ibcmd` path.
        credentials: Infobase user.
        runner: Command runner.

    Yields:
        A bound `Ibcmd`; the infobase exists when the block starts.
    """
    if keep is not None:
        tool = Ibcmd(keep, executable=ibcmd, credentials=credentials, runner=runner)
        tool.ensure()
        yield tool
        return
    with tempfile.TemporaryDirectory(prefix="harness-ib-") as tmp:
        tool = Ibcmd(Path(tmp), executable=ibcmd, credentials=credentials, runner=runner)
        tool.ensure()
        yield tool
