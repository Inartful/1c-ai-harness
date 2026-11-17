"""Implementation of `harness rules`: unpack and pack 1C Data Conversion 2.x rules via `gitrules`."""

import shutil
import tempfile
import zipfile
from pathlib import Path

from pydantic import BaseModel, Field

from harness.errors import ConfigError
from harness.services.build import BuildStage
from harness.system.runner import CommandRunner, run

_GITRULES_TIMEOUT_S = 300.0
_EXCHANGE_RULES_PATTERNS = ("ExchangeRules.xml", "CorrespondentExchangeRules.xml")


class RulesReport(BaseModel):
    """Outcome of `harness rules unpack` or `harness rules pack`."""

    source: Path
    target: Path
    files: list[Path] = Field(default_factory=list)
    stages: list[BuildStage] = Field(default_factory=list)


def find_gitrules(explicit: Path | None = None) -> Path:
    """Locate the `gitrules` executable.

    Args:
        explicit: Explicit path passed by the user.

    Returns:
        Path to `gitrules`.

    Raises:
        ConfigError: If `gitrules` cannot be found.
    """
    if explicit is not None:
        if not explicit.is_file():
            msg = f"gitrules not found at {explicit}"
            raise ConfigError(msg)
        return explicit
    found = shutil.which("gitrules")
    if found is None:
        msg = "gitrules not found on PATH; install via `opm install gitrules`"
        raise ConfigError(msg)
    return Path(found)


def _find_unpacked_dirs(src_dir: Path) -> list[Path]:
    """Find unpacked `gitrules` directories containing `ПравилаОбмена.xml`."""
    if (src_dir / "ПравилаОбмена.xml").is_file():
        return [src_dir]
    dirs: list[Path] = []
    for child in sorted(src_dir.iterdir()):
        if child.is_dir() and child.name != "_raw" and (child / "ПравилаОбмена.xml").is_file():
            dirs.append(child)
    return dirs


def unpack_rules(
    source: Path,
    out: Path,
    *,
    gitrules: Path | None = None,
    runner: CommandRunner = run,
) -> RulesReport:
    """Unpack a 1C exchange rules `.zip` or `.xml` into a `gitrules` directory tree.

    Args:
        source: Path to `Правила конвертации.zip` or `ExchangeRules.xml`.
        out: Target directory for unpacked rule trees.
        gitrules: Explicit `gitrules` executable path.
        runner: Command runner, injectable for tests.

    Returns:
        Report with unpacked XML paths and `gitrules` execution stages.

    Raises:
        ConfigError: If `source` or `gitrules` is missing, or no rules XML is found.
    """
    src_path = source.resolve()
    if not src_path.is_file():
        msg = f"rules source not found: {src_path}"
        raise ConfigError(msg)
    executable = find_gitrules(gitrules)
    target_dir = out.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    xml_files: list[Path] = []
    if src_path.suffix.lower() == ".zip":
        raw_dir = target_dir / "_raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(src_path, "r") as archive:
            archive.extractall(raw_dir)
        for name in _EXCHANGE_RULES_PATTERNS:
            candidate = raw_dir / name
            if candidate.is_file():
                xml_files.append(candidate)
        if not xml_files:
            xml_files = sorted(raw_dir.glob("*.xml"))
    elif src_path.suffix.lower() == ".xml":
        xml_files = [src_path]
    else:
        msg = f"unsupported rules file type: {src_path.suffix} (expected .zip or .xml)"
        raise ConfigError(msg)

    if not xml_files:
        msg = f"no XML rules found inside {src_path}"
        raise ConfigError(msg)

    stages: list[BuildStage] = []
    for xml_file in xml_files:
        res = runner(
            [str(executable), "export", str(xml_file), str(target_dir)],
            timeout_s=_GITRULES_TIMEOUT_S,
        )
        stages.append(BuildStage(name=f"export:{xml_file.name}", seconds=res.duration_s, ok=True))

    return RulesReport(source=src_path, target=target_dir, files=xml_files, stages=stages)


def pack_rules(
    src_dir: Path,
    out: Path,
    *,
    gitrules: Path | None = None,
    runner: CommandRunner = run,
) -> RulesReport:
    """Assemble unpacked `gitrules` directories back into `.xml` or `.zip`.

    Args:
        src_dir: Directory containing unpacked rules (or a single rule folder).
        out: Output `.zip` archive, `.xml` file, or directory.
        gitrules: Explicit `gitrules` executable path.
        runner: Command runner, injectable for tests.

    Returns:
        Report with assembled output paths and `gitrules` execution stages.

    Raises:
        ConfigError: If `src_dir` or `gitrules` is missing, or no unpacked rules are found.
    """
    source_root = src_dir.resolve()
    if not source_root.is_dir():
        msg = f"unpacked rules directory not found: {source_root}"
        raise ConfigError(msg)
    executable = find_gitrules(gitrules)
    rule_dirs = _find_unpacked_dirs(source_root)
    if not rule_dirs:
        msg = f"no unpacked rules (missing ПравилаОбмена.xml) in {source_root}"
        raise ConfigError(msg)

    target_out = out.resolve()
    stages: list[BuildStage] = []
    produced: list[Path] = []

    with tempfile.TemporaryDirectory(prefix="harness-rules-") as tmp_dir:
        work_root = Path(tmp_dir)
        assembled_by_name: dict[str, Path] = {}
        for rule_dir in rule_dirs:
            stage_dir = work_root / rule_dir.name
            stage_dir.mkdir(parents=True, exist_ok=True)
            res = runner(
                [str(executable), "assembly", str(rule_dir), str(stage_dir)],
                timeout_s=_GITRULES_TIMEOUT_S,
            )
            stages.append(
                BuildStage(name=f"assembly:{rule_dir.name}", seconds=res.duration_s, ok=True)
            )
            assembled_xmls = sorted(stage_dir.glob("*.xml"))
            if not assembled_xmls:
                msg = f"gitrules assembly produced no XML for {rule_dir}"
                raise ConfigError(msg)
            out_name = rule_dir.name if rule_dir.name.endswith(".xml") else assembled_xmls[0].name
            assembled_by_name[out_name] = assembled_xmls[0]

        if target_out.suffix.lower() == ".zip":
            target_out.parent.mkdir(parents=True, exist_ok=True)
            raw_dir = source_root / "_raw"
            files_to_zip: dict[str, Path] = {}
            if raw_dir.is_dir():
                for raw_file in sorted(raw_dir.glob("*")):
                    if raw_file.is_file():
                        files_to_zip[raw_file.name] = raw_file
            files_to_zip.update(assembled_by_name)
            with zipfile.ZipFile(target_out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for arcname, file_path in sorted(files_to_zip.items()):
                    archive.write(file_path, arcname=arcname)
            produced = [target_out]
        elif target_out.suffix.lower() == ".xml":
            if len(assembled_by_name) > 1:
                msg = f"multiple rule sets found; pass a .zip or directory as output: {target_out}"
                raise ConfigError(msg)
            target_out.parent.mkdir(parents=True, exist_ok=True)
            only_xml = next(iter(assembled_by_name.values()))
            shutil.copy2(only_xml, target_out)
            produced = [target_out]
        else:
            target_out.mkdir(parents=True, exist_ok=True)
            for name, file_path in sorted(assembled_by_name.items()):
                dest = target_out / name
                shutil.copy2(file_path, dest)
                produced.append(dest)

    return RulesReport(source=source_root, target=target_out, files=produced, stages=stages)
