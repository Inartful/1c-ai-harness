"""Tests for `decompile` and `rules` services."""

import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest

from fakes import FakeRunner
from harness.errors import ConfigError
from harness.services.decompile import decompile_cf
from harness.services.rules import pack_rules, unpack_rules
from harness.system.runner import CommandResult


def _exe(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text("", encoding="utf-8")
    return path


def test_decompile_cf_with_temp_ib(tmp_path: Path) -> None:
    cf = tmp_path / "app.cf"
    cf.write_bytes(b"cf-data")
    out = tmp_path / "src"
    runner = FakeRunner()
    report = decompile_cf(cf, out, ibcmd=_exe(tmp_path, "ibcmd.exe"), runner=runner)
    assert [s.name for s in report.stages] == ["create-ib", "load-cf", "export-xml"]
    assert runner.calls[0][1:3] == ("infobase", "create")
    assert runner.calls[1][1:4] == ("infobase", "config", "load")
    assert "--force" in runner.calls[1]
    assert runner.calls[2][1:4] == ("infobase", "config", "export")
    assert out.is_dir()


def test_decompile_cf_skips_create_when_ib_exists(tmp_path: Path) -> None:
    cf = tmp_path / "app.cf"
    cf.write_bytes(b"cf-data")
    ib = tmp_path / "ib"
    ib.mkdir()
    (ib / "1Cv8.1CD").write_bytes(b"db")
    runner = FakeRunner()
    report = decompile_cf(
        cf, tmp_path / "out", ib=ib, ibcmd=_exe(tmp_path, "ibcmd.exe"), runner=runner
    )
    assert [s.name for s in report.stages] == ["load-cf", "export-xml"]


def test_decompile_cf_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="configuration file not found"):
        decompile_cf(tmp_path / "missing.cf", tmp_path / "out", ibcmd=_exe(tmp_path, "ibcmd.exe"))


def test_unpack_rules_from_zip(tmp_path: Path) -> None:
    archive = tmp_path / "rules.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ExchangeRules.xml", "<ПравилаОбмена/>")
        zf.writestr("RegistrationRules.xml", "<ПравилаРегистрации/>")
    out = tmp_path / "unpacked"
    runner = FakeRunner()
    report = unpack_rules(archive, out, gitrules=_exe(tmp_path, "gitrules.bat"), runner=runner)
    assert [s.name for s in report.stages] == ["export:ExchangeRules.xml"]
    assert runner.calls[0][1] == "export"
    assert (out / "_raw" / "RegistrationRules.xml").is_file()


def test_unpack_rules_missing_gitrules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    xml = tmp_path / "ExchangeRules.xml"
    xml.write_text("<ПравилаОбмена/>", encoding="utf-8")
    monkeypatch.setattr("harness.services.rules.shutil.which", lambda _: None)
    with pytest.raises(ConfigError, match="gitrules not found"):
        unpack_rules(xml, tmp_path / "out", runner=FakeRunner())


def test_pack_rules_to_zip_preserves_raw_files(tmp_path: Path) -> None:
    src = tmp_path / "unpacked"
    (src / "_raw").mkdir(parents=True)
    (src / "_raw" / "RegistrationRules.xml").write_text("<Reg/>", encoding="utf-8")
    (src / "_raw" / "ExchangeRules.xml").write_text("<Old/>", encoding="utf-8")
    rule_dir = src / "ExchangeRules.xml"
    rule_dir.mkdir()
    (rule_dir / "ПравилаОбмена.xml").write_text("<Meta/>", encoding="utf-8")

    class _AssemblyRunner(FakeRunner):
        def __call__(
            self,
            argv: Sequence[str],
            *,
            timeout_s: float,
            cwd: Path | None = None,
            check: bool = True,
            input_text: str | None = None,
        ) -> CommandResult:
            res = super().__call__(
                argv, timeout_s=timeout_s, cwd=cwd, check=check, input_text=input_text
            )
            stage_dir = Path(argv[3])
            (stage_dir / "ExchangeRules.xml").write_text("<New/>", encoding="utf-8")
            return res

    out_zip = tmp_path / "out.zip"
    report = pack_rules(
        src, out_zip, gitrules=_exe(tmp_path, "gitrules.bat"), runner=_AssemblyRunner()
    )
    assert report.files == [out_zip]
    with zipfile.ZipFile(out_zip, "r") as zf:
        assert sorted(zf.namelist()) == ["ExchangeRules.xml", "RegistrationRules.xml"]
        assert zf.read("ExchangeRules.xml").decode("utf-8") == "<New/>"
        assert zf.read("RegistrationRules.xml").decode("utf-8") == "<Reg/>"
