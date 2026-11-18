"""Tests for platform wrappers: unpack/pack, compile log parsing, changed-only check."""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from dumps import make_base, make_extension
from fakes import FakeRunner
from harness.errors import CommandFailedError, ConfigError
from harness.services.artifacts import Tools, pack, unpack
from harness.services.check import parse_report, run_check_changed
from harness.services.compile import compile_check, parse_check_modules_log

LOG = """{ОбщийМодуль.ТоварыСервер.Модуль(2,33)}: Ошибка в выражении
\tВозврат Х +<<?>> ; (Проверка: Сервер)
{ОбщийМодуль.ТоварыСервер.Модуль(2,33)}: Ошибка в выражении
\tВозврат Х +<<?>> ; (Проверка: Внешнее соединение)
{ДоработкиТест Справочник.Товары.МодульОбъекта(21,9)}: Ошибка в выражении
\tА = 1 +<<?>>; (Проверка: Сервер)
"""


def _exe(tmp_path: Path, name: str) -> Path:
    path = tmp_path / "bin" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def _tools(tmp_path: Path) -> Tools:
    return Tools(ibcmd=_exe(tmp_path, "ibcmd.exe"), designer=_exe(tmp_path, "1cv8.exe"))


def _stages(runner: FakeRunner) -> list[str]:
    """Compact view of ibcmd calls: the words after `infobase` up to the first option."""
    out: list[str] = []
    for argv in runner.calls:
        if "infobase" not in argv:
            out.append(Path(argv[0]).name)
            continue
        words = argv[argv.index("infobase") + 1 :]
        out.append(
            " ".join(w for w in words if not w.startswith("-") and not Path(w).is_absolute())
        )
    return out


def test_parse_check_modules_log_merges_modes() -> None:
    errors = parse_check_modules_log(LOG)
    assert len(errors) == 2
    first = errors[0]
    assert (first.module, first.line, first.column) == ("ОбщийМодуль.ТоварыСервер.Модуль", 2, 33)
    assert first.modes == ["Сервер", "Внешнее соединение"]
    assert first.context == "Возврат Х +<<?>> ;"
    assert errors[1].module.startswith("ДоработкиТест ")


def test_compile_check_reports_and_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(self: object, args: Sequence[str], *, timeout_s: float = 0) -> object:
        from harness.services.onec import DesignerResult

        assert "/CheckModules" in args and "-Extension" in args
        return DesignerResult(returncode=101, log=LOG, seconds=1.0)

    monkeypatch.setattr("harness.services.onec.Designer.run", fake_run)
    report = compile_check(tmp_path, extension="X", designer=_exe(tmp_path, "1cv8.exe"))
    assert not report.ok and len(report.errors) == 2

    def broken(self: object, args: Sequence[str], *, timeout_s: float = 0) -> object:
        from harness.services.onec import DesignerResult

        return DesignerResult(returncode=1, log="Ошибка лицензии", seconds=1.0)

    monkeypatch.setattr("harness.services.onec.Designer.run", broken)
    with pytest.raises(CommandFailedError, match="лицензии"):
        compile_check(tmp_path, designer=_exe(tmp_path, "1cv8.exe"))


def test_unpack_cfe_into_kept_infobase_reregisters_real_name(tmp_path: Path) -> None:
    cfe = tmp_path / "ext.cfe"
    cfe.write_bytes(b"x")

    def on_call(argv: Sequence[str]) -> None:
        if "export" in argv:
            make_extension(Path(argv[-1]))
        if "create" in argv:
            db = next(a for a in argv if a.startswith("--db-path=")).split("=", 1)[1]
            (Path(db) / "1Cv8.1CD").write_bytes(b"")

    runner = FakeRunner(on_call=on_call)
    report = unpack(
        cfe, tmp_path / "out", ib=tmp_path / "ib", tools=_tools(tmp_path), runner=runner
    )
    assert report.extensions == ["ДоработкиТест"]
    assert _stages(runner) == [
        "create",
        "config load",
        "config export",
        "config extension delete",
        "config load",
        "config apply",
    ]
    assert any("--extension=ДоработкиТест" in a for a in runner.calls[4])


def test_pack_cfe_partial_import_and_apply(tmp_path: Path) -> None:
    src = make_extension(tmp_path / "src")
    baseline = make_extension(tmp_path / "baseline")
    (src / "Roles").mkdir()
    (src / "Roles" / "Новая.xml").write_text("<x/>", encoding="utf-8")
    ib = tmp_path / "ib"
    ib.mkdir()
    (ib / "1Cv8.1CD").write_bytes(b"")
    runner = FakeRunner(stdout='name : "ДоработкиТест"\n')
    report = pack(
        src,
        tmp_path / "out" / "e.cfe",
        ib=ib,
        changed_from=baseline,
        tools=_tools(tmp_path),
        runner=runner,
    )
    assert _stages(runner) == [
        "config extension list",
        "config import files Roles/Новая.xml",
        "config apply",
        "config save",
    ]
    assert report.extensions == ["ДоработкиТест"]


def test_pack_cfe_without_base_warns(tmp_path: Path) -> None:
    src = make_extension(tmp_path / "src")
    runner = FakeRunner()
    report = pack(src, tmp_path / "e.cfe", tools=_tools(tmp_path), runner=runner)
    assert "config apply" not in _stages(runner)
    assert any("not applied" in n for n in report.notes)


def test_pack_epf_uses_designer_on_scratch_ib(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    src = tmp_path / "epf"
    src.mkdir()
    (src / "Обработка.xml").write_text("<x/>", encoding="utf-8")
    runner = FakeRunner()
    pack(src, tmp_path / "out.epf", tools=_tools(tmp_path), runner=runner)
    designer_call = runner.calls[-1]
    assert designer_call[1] == "DESIGNER"
    assert "/LoadExternalDataProcessorOrReportFromFiles" in designer_call
    assert str(src / "Обработка.xml") in designer_call


def test_pack_rejects_unknown_suffix(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unsupported output"):
        pack(
            make_base(tmp_path / "b"),
            tmp_path / "x.zip",
            tools=_tools(tmp_path),
            runner=FakeRunner(),
        )


def test_parse_report_and_changed_check(tmp_path: Path) -> None:
    old = make_base(tmp_path / "old")
    new = make_base(tmp_path / "new")
    module = new / "CommonModules" / "ТоварыСервер" / "Ext" / "Module.bsl"
    module.write_text(module.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    def on_call(argv: Sequence[str]) -> None:
        root = Path(argv[argv.index("-s") + 1])
        out = Path(argv[argv.index("-o") + 1])
        uri = (root / "CommonModules" / "ТоварыСервер" / "Ext" / "Module.bsl").as_uri()
        diags = [
            {"code": "Old", "message": "m", "severity": "Hint", "range": {"start": {"line": 0}}}
        ]
        if root.name == "new":
            diags.append(
                {
                    "code": "New",
                    "message": "n",
                    "severity": "Error",
                    "range": {"start": {"line": 3}},
                }
            )
        report = {"fileinfos": [{"path": uri, "diagnostics": diags}]}
        (out / "bsl-json.json").write_text(json.dumps(report), encoding="utf-8")

    runner = FakeRunner(on_call=on_call)
    result = run_check_changed(new, old, bsl=_exe(tmp_path, "bsl.exe"), runner=runner)
    assert result.files == ["CommonModules/ТоварыСервер/Ext/Module.bsl"]
    assert [(d.code, d.line) for d in result.diagnostics] == [("New", 4)]
    assert all(cwd is not None for cwd in runner.cwds)  # bsl-ls needs cwd on the same drive
    report = tmp_path / "r.json"
    report.write_text(
        json.dumps({"fileinfos": [{"path": (tmp_path / "a b.bsl").as_uri(), "diagnostics": []}]}),
        encoding="utf-8",
    )
    assert parse_report(report, tmp_path) == []
