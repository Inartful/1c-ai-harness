"""Tiny configuration/extension XML dumps shaped like `ibcmd` 8.3.27 exports."""

from pathlib import Path

HEADER = (
    '<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" '
    'xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config" '
    'xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" '
    'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" version="2.20">'
)
CATALOG_UUID = "a6e9b25c-a880-4986-ae73-8024d30566c3"
MODULE_UUID = "7870d4d7-a8c8-4061-9079-2c4b87554deb"


def _syn(text: str) -> str:
    return (
        f"<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>{text}</v8:content>"
        "</v8:item></Synonym>"
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig", newline="\n")


OBJECT_MODULE = """Процедура ПередЗаписью(Отказ)
\tЕсли ПустаяСтрока(Артикул) Тогда
\t\tАртикул = "НЕТ";
\tКонецЕсли;
КонецПроцедуры

Функция Описание(Знач Префикс = "(", Суффикс) Экспорт
\tВозврат Префикс + Наименование + Суффикс;
КонецФункции
"""

COMMON_MODULE = """// Цены
Функция ЦенаТовара(Товар,
\tЗнач Дата = Неопределено) Экспорт
\tВозврат 100;
КонецФункции
"""


def make_base(root: Path) -> Path:
    """Write a base dump with `Catalog.Товары` and `CommonModule.ТоварыСервер`."""
    _write(
        root / "Configuration.xml",
        f'<?xml version="1.0" encoding="UTF-8"?>\n{HEADER}\n'
        '\t<Configuration uuid="c1ad8356-8417-404f-9eb1-356bc0b15b28">\n'
        "\t\t<Properties><Name>Конфигурация</Name></Properties>\n"
        "\t\t<ChildObjects>\n\t\t\t<Language>Русский</Language>\n"
        "\t\t\t<CommonModule>ТоварыСервер</CommonModule>\n\t\t\t<Catalog>Товары</Catalog>\n"
        "\t\t</ChildObjects>\n\t</Configuration>\n</MetaDataObject>\n",
    )
    gen = "".join(
        f'<xr:GeneratedType name="{kind}.Товары" category="{cat}">'
        f"<xr:TypeId>11111111-1111-1111-1111-11111111111{i}</xr:TypeId>"
        f"<xr:ValueId>22222222-2222-2222-2222-22222222222{i}</xr:ValueId></xr:GeneratedType>"
        for i, (kind, cat) in enumerate(
            [("CatalogObject", "Object"), ("CatalogRef", "Ref"), ("CatalogManager", "Manager")]
        )
    )
    _write(
        root / "Catalogs" / "Товары.xml",
        f'<?xml version="1.0" encoding="UTF-8"?>\n{HEADER}\n'
        f'\t<Catalog uuid="{CATALOG_UUID}">\n\t\t<InternalInfo>{gen}</InternalInfo>\n'
        f"\t\t<Properties><Name>Товары</Name>{_syn('Номенклатура склада')}<Comment/>"
        "<Hierarchical>false</Hierarchical></Properties>\n"
        "\t\t<ChildObjects>\n"
        '\t\t\t<Attribute uuid="33333333-3333-3333-3333-333333333333"><Properties>'
        f"<Name>Артикул</Name>{_syn('Артикул')}<Type><v8:Type>xs:string</v8:Type>"
        "<v8:StringQualifiers><v8:Length>25</v8:Length></v8:StringQualifiers></Type>"
        "</Properties></Attribute>\n"
        '\t\t\t<TabularSection uuid="44444444-4444-4444-4444-444444444444"><InternalInfo/>'
        "<Properties><Name>Состав</Name></Properties><ChildObjects>"
        '<Attribute uuid="55555555-5555-5555-5555-555555555555"><Properties>'
        "<Name>Количество</Name><Type><v8:Type>xs:decimal</v8:Type><v8:NumberQualifiers>"
        "<v8:Digits>15</v8:Digits><v8:FractionDigits>3</v8:FractionDigits>"
        "</v8:NumberQualifiers></Type></Properties></Attribute></ChildObjects>"
        "</TabularSection>\n"
        "\t\t\t<Form>ФормаЭлемента</Form>\n"
        "\t\t</ChildObjects>\n\t</Catalog>\n</MetaDataObject>\n",
    )
    _write(root / "Catalogs" / "Товары" / "Ext" / "ObjectModule.bsl", OBJECT_MODULE)
    _write(
        root / "CommonModules" / "ТоварыСервер.xml",
        f'<?xml version="1.0" encoding="UTF-8"?>\n{HEADER}\n'
        f'\t<CommonModule uuid="{MODULE_UUID}">\n'
        f"\t\t<Properties><Name>ТоварыСервер</Name>{_syn('Товары сервер')}<Comment/>"
        "<Global>false</Global><Server>true</Server><ServerCall>false</ServerCall>"
        "<Privileged>false</Privileged></Properties>\n"
        "\t</CommonModule>\n</MetaDataObject>\n",
    )
    _write(root / "CommonModules" / "ТоварыСервер" / "Ext" / "Module.bsl", COMMON_MODULE)
    return root


def make_extension(root: Path, *, prefix: str = "дт_") -> Path:
    """Write an empty extension dump (as `ibcmd ... extension create` + export produces)."""
    _write(
        root / "Configuration.xml",
        f'<?xml version="1.0" encoding="UTF-8"?>\n{HEADER}\n'
        '\t<Configuration uuid="73ca99cb-f208-4da1-b42e-d70774b80231">\n'
        "\t\t<Properties>\n\t\t\t<ObjectBelonging>Adopted</ObjectBelonging>\n"
        "\t\t\t<Name>ДоработкиТест</Name>\n"
        f"\t\t\t<NamePrefix>{prefix}</NamePrefix>\n"
        "\t\t\t<ScriptVariant>Russian</ScriptVariant>\n\t\t</Properties>\n"
        "\t\t<ChildObjects>\n\t\t\t<Role>ОсновнаяРоль</Role>\n\t\t</ChildObjects>\n"
        "\t</Configuration>\n</MetaDataObject>\n",
    )
    return root
