"""Tests for source-level tools: BSL parser, metadata, diff, extension helpers."""

import re
from pathlib import Path

import pytest

from dumps import CATALOG_UUID, MODULE_UUID, make_base, make_extension
from harness.errors import ConfigError
from harness.services.bsl import (
    body_lines,
    find_method,
    original_from_change_and_validate,
    param_names,
    parse_methods,
)
from harness.services.diff import diff_trees, unified_patch
from harness.services.extension import adopt, intercept, lint
from harness.services.meta import find_objects, render_summary, resolve_ref, show_object


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig", newline="\n")


# --- bsl -------------------------------------------------------------------------------


def test_parse_methods_headers_annotations_and_bodies() -> None:
    text = (
        '&НаСервере\n&Вместо("Старый")\nФункция Новый(А,\n\tЗнач Б = "(x)") Экспорт\n'
        "\tВозврат 1;\nКонецФункции\n\nПроцедура П()\nКонецПроцедуры\n"
    )
    methods = parse_methods(text)
    assert [m.name for m in methods] == ["Новый", "П"]
    first = methods[0]
    assert first.export is True
    assert first.line == 3 and first.end_line == 6
    assert first.annotations == ["НаСервере", 'Вместо("Старый")']
    assert (first.interception, first.target) == ("Вместо", "Старый")
    assert param_names(first.params) == ["А", "Б"]
    assert body_lines(text, first) == ["\tВозврат 1;"]
    assert find_method(methods, "п") is methods[1]


def test_change_and_validate_reconstruction() -> None:
    body = [
        "\tА = 1;",
        "\t#Вставка",
        "\tБ = 2;",
        "\t#КонецВставки",
        "\t#Удаление",
        "\tВ = 3;",
        "\t#КонецУдаления",
    ]
    assert original_from_change_and_validate(body) == ["\tА = 1;", "\tВ = 3;"]


# --- meta ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Catalog.Товары", "Catalog.Товары"),
        ("Справочник.Товары", "Catalog.Товары"),
        ("Catalogs/Товары", "Catalog.Товары"),
        ("общиймодуль.X", "CommonModule.X"),
        ("РегистрСведений.Цены", "InformationRegister.Цены"),
    ],
)
def test_resolve_ref(text: str, expected: str) -> None:
    assert str(resolve_ref(text)) == expected


def test_resolve_ref_rejects_unknown() -> None:
    with pytest.raises(ConfigError, match="unknown metadata class"):
        resolve_ref("Склад.Х")


def test_show_object_and_find(tmp_path: Path) -> None:
    base = make_base(tmp_path / "base")
    summary = show_object(base, resolve_ref("Справочник.Товары"), with_methods=True)
    text = render_summary(summary, base)
    assert summary.uuid == CATALOG_UUID
    assert "Attribute Артикул: String(25)" in text
    assert "Attribute Количество: Number(15,3)" in text
    assert "Forms: ФормаЭлемента" in text
    assert re.search(r"7\s+Функция Описание\(.*\) Export", text)
    hits = find_objects(base, "склад")
    assert [h.ref for h in hits] == ["Catalog.Товары"]
    assert find_objects(base, "товар", kind="ОбщийМодуль")[0].ref == "CommonModule.ТоварыСервер"


# --- diff ------------------------------------------------------------------------------


def test_diff_trees_and_patch(tmp_path: Path) -> None:
    old = make_base(tmp_path / "old")
    new = make_base(tmp_path / "new")
    module = "Catalogs/Товары/Ext/ObjectModule.bsl"
    _write(new / module, _read(new / module) + "// новое\n")
    (new / "CommonModules" / "ТоварыСервер" / "Ext" / "Module.bsl").unlink()
    _write(new / "Roles" / "Новая.xml", "<x/>")
    _write(new / "ConfigDumpInfo.xml", "<ignored/>")
    delta = diff_trees(old, new)
    assert delta.modified == [module]
    assert delta.added == ["Roles/Новая.xml"]
    assert delta.deleted == ["CommonModules/ТоварыСервер/Ext/Module.bsl"]
    assert "+// новое" in unified_patch(old, new, module)


# --- extension -------------------------------------------------------------------------


def test_adopt_catalog_and_common_module(tmp_path: Path) -> None:
    base = make_base(tmp_path / "base")
    ext = make_extension(tmp_path / "ext")
    result = adopt(ext, base, resolve_ref("Catalog.Товары"))
    xml = _read(result.path)
    assert result.created
    assert f"<ExtendedConfigurationObject>{CATALOG_UUID}</ExtendedConfigurationObject>" in xml
    assert "<ObjectBelonging>Adopted</ObjectBelonging>" in xml
    assert 'name="CatalogObject.Товары"' in xml
    assert "11111111-1111-1111-1111-111111111110" not in xml  # type ids are regenerated
    assert "<ChildObjects/>" in xml  # required by the platform for catalogs
    assert "<Catalog>Товары</Catalog>" in _read(ext / "Configuration.xml")
    assert not adopt(ext, base, resolve_ref("Catalog.Товары")).created

    module_xml = _read(adopt(ext, base, resolve_ref("CommonModule.ТоварыСервер")).path)
    assert "<Server>true</Server>" in module_xml
    assert "<Privileged>" not in module_xml
    assert "<ChildObjects" not in module_xml
    assert "InternalInfo" not in module_xml


def test_adopt_missing_object(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found in base"):
        adopt(make_extension(tmp_path / "e"), make_base(tmp_path / "b"), resolve_ref("Catalog.Нет"))


def test_intercept_generates_stubs(tmp_path: Path) -> None:
    base = make_base(tmp_path / "base")
    ext = make_extension(tmp_path / "ext")
    instead = intercept(
        ext, base, resolve_ref("CommonModule.ТоварыСервер"), "ЦенаТовара", "instead"
    )
    assert instead.adopted
    assert instead.method == "дт_ЦенаТовара"
    assert '&Вместо("ЦенаТовара")' in instead.code
    assert "Результат = ПродолжитьВызов(Товар, Дата);" in instead.code
    with pytest.raises(ConfigError, match="already intercepted"):
        intercept(ext, base, resolve_ref("CommonModule.ТоварыСервер"), "ЦенаТовара", "instead")

    change = intercept(ext, base, resolve_ref("Catalog.Товары"), "ПередЗаписью", "change")
    assert 'Артикул = "НЕТ";' in change.code
    after = intercept(ext, base, resolve_ref("Catalog.Товары"), "ПередЗаписью", "after")
    assert after.method == "дт_ПередЗаписьюПосле"
    module = _read(ext / "Catalogs" / "Товары" / "Ext" / "ObjectModule.bsl")
    assert module.count("КонецПроцедуры") == 2

    with pytest.raises(ConfigError, match="not found"):
        intercept(ext, base, resolve_ref("Catalog.Товары"), "Нет", "before")


def test_lint_clean_then_catches_platform_blind_spots(tmp_path: Path) -> None:
    base = make_base(tmp_path / "base")
    ext = make_extension(tmp_path / "ext")
    intercept(ext, base, resolve_ref("Catalog.Товары"), "ПередЗаписью", "change")
    intercept(ext, base, resolve_ref("CommonModule.ТоварыСервер"), "ЦенаТовара", "instead")
    module = ext / "Catalogs" / "Товары" / "Ext" / "ObjectModule.bsl"
    text = _read(module).replace(
        '\t\tАртикул = "НЕТ";\n', '\t\tАртикул = "НЕТ";\n\t#Вставка\n\tА = 1;\n\t#КонецВставки\n'
    )
    _write(module, text)
    clean = lint(ext, base)
    assert clean.errors == 0, clean.issues
    assert clean.interceptions == 2

    _write(
        module,
        text.replace('Артикул = "НЕТ";', 'Артикул = "ПУСТО";')
        + '\n&Перед("НетТакого")\nПроцедура дт_Х()\nКонецПроцедуры\n',
    )
    catalog_xml = ext / "Catalogs" / "Товары.xml"
    _write(catalog_xml, _read(catalog_xml).replace(CATALOG_UUID, MODULE_UUID))
    _write(
        ext / "Configuration.xml",
        _read(ext / "Configuration.xml").replace("<CommonModule>ТоварыСервер</CommonModule>", ""),
    )
    messages = [i.message for i in lint(ext, base).issues if i.level == "error"]
    assert any("body differs from base" in m for m in messages)
    assert any('&Перед("НетТакого"): no such method' in m for m in messages)
    assert any("ExtendedConfigurationObject" in m for m in messages)
    assert any("not listed in Configuration.xml" in m for m in messages)


def test_lint_warns_on_own_object_without_prefix(tmp_path: Path) -> None:
    base = make_base(tmp_path / "base")
    ext = make_extension(tmp_path / "ext")
    _write(ext / "Catalogs" / "Склады.xml", _read(base / "Catalogs" / "Товары.xml"))
    _write(
        ext / "Configuration.xml",
        _read(ext / "Configuration.xml").replace(
            "<Role>ОсновнаяРоль</Role>", "<Role>ОсновнаяРоль</Role><Catalog>Склады</Catalog>"
        ),
    )
    issues = lint(ext, base).issues
    assert [i.level for i in issues] == ["warning"]
    assert "prefix" in issues[0].message
