"""Read configuration XML dumps: resolve object references, find objects, summarize them.

The point is to answer "what attributes / modules / methods does `Документ.Реализация` have"
without reading a 3000-line XML file.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from pydantic import BaseModel, Field

from harness.errors import ConfigError
from harness.services.bsl import Method, parse_methods

MD = "http://v8.1c.ru/8.3/MDClasses"
V8 = "http://v8.1c.ru/8.1/data/core"
XR = "http://v8.1c.ru/8.3/xcf/readable"
_NS = {"md": MD, "v8": V8, "xr": XR}

# English metadata class -> dump folder.
FOLDERS: dict[str, str] = {
    "Subsystem": "Subsystems",
    "CommonModule": "CommonModules",
    "SessionParameter": "SessionParameters",
    "Role": "Roles",
    "CommonAttribute": "CommonAttributes",
    "ExchangePlan": "ExchangePlans",
    "FilterCriterion": "FilterCriteria",
    "EventSubscription": "EventSubscriptions",
    "ScheduledJob": "ScheduledJobs",
    "Bot": "Bots",
    "FunctionalOption": "FunctionalOptions",
    "FunctionalOptionsParameter": "FunctionalOptionsParameters",
    "DefinedType": "DefinedTypes",
    "SettingsStorage": "SettingsStorages",
    "CommonForm": "CommonForms",
    "CommonCommand": "CommonCommands",
    "CommandGroup": "CommandGroups",
    "CommonTemplate": "CommonTemplates",
    "CommonPicture": "CommonPictures",
    "XDTOPackage": "XDTOPackages",
    "WebService": "WebServices",
    "HTTPService": "HTTPServices",
    "WSReference": "WSReferences",
    "IntegrationService": "IntegrationServices",
    "StyleItem": "StyleItems",
    "Style": "Styles",
    "Language": "Languages",
    "Constant": "Constants",
    "Catalog": "Catalogs",
    "Document": "Documents",
    "DocumentNumerator": "DocumentNumerators",
    "Sequence": "Sequences",
    "DocumentJournal": "DocumentJournals",
    "Enum": "Enums",
    "Report": "Reports",
    "DataProcessor": "DataProcessors",
    "ChartOfCharacteristicTypes": "ChartsOfCharacteristicTypes",
    "ChartOfAccounts": "ChartsOfAccounts",
    "ChartOfCalculationTypes": "ChartsOfCalculationTypes",
    "InformationRegister": "InformationRegisters",
    "AccumulationRegister": "AccumulationRegisters",
    "AccountingRegister": "AccountingRegisters",
    "CalculationRegister": "CalculationRegisters",
    "BusinessProcess": "BusinessProcesses",
    "Task": "Tasks",
    "ExternalDataSource": "ExternalDataSources",
}

RUSSIAN: dict[str, str] = {
    "Подсистема": "Subsystem",
    "ОбщийМодуль": "CommonModule",
    "ПараметрСеанса": "SessionParameter",
    "Роль": "Role",
    "ОбщийРеквизит": "CommonAttribute",
    "ПланОбмена": "ExchangePlan",
    "КритерийОтбора": "FilterCriterion",
    "ПодпискаНаСобытие": "EventSubscription",
    "РегламентноеЗадание": "ScheduledJob",
    "ФункциональнаяОпция": "FunctionalOption",
    "ОпределяемыйТип": "DefinedType",
    "ХранилищеНастроек": "SettingsStorage",
    "ОбщаяФорма": "CommonForm",
    "ОбщаяКоманда": "CommonCommand",
    "ГруппаКоманд": "CommandGroup",
    "ОбщийМакет": "CommonTemplate",
    "ОбщаяКартинка": "CommonPicture",
    "ПакетXDTO": "XDTOPackage",
    "WebСервис": "WebService",
    "HTTPСервис": "HTTPService",
    "Константа": "Constant",
    "Справочник": "Catalog",
    "Документ": "Document",
    "НумераторДокументов": "DocumentNumerator",
    "Последовательность": "Sequence",
    "ЖурналДокументов": "DocumentJournal",
    "Перечисление": "Enum",
    "Отчет": "Report",
    "Обработка": "DataProcessor",
    "ПланВидовХарактеристик": "ChartOfCharacteristicTypes",
    "ПланСчетов": "ChartOfAccounts",
    "ПланВидовРасчета": "ChartOfCalculationTypes",
    "РегистрСведений": "InformationRegister",
    "РегистрНакопления": "AccumulationRegister",
    "РегистрБухгалтерии": "AccountingRegister",
    "РегистрРасчета": "CalculationRegister",
    "БизнесПроцесс": "BusinessProcess",
    "Задача": "Task",
    "ВнешнийИсточникДанных": "ExternalDataSource",
}

_LOWER = {k.lower(): k for k in FOLDERS} | {k.lower(): v for k, v in RUSSIAN.items()}
_FOLDER_TO_TYPE = {v.lower(): k for k, v in FOLDERS.items()}

_MODULE_FILES = (
    "Module",
    "ObjectModule",
    "ManagerModule",
    "RecordSetModule",
    "ValueManagerModule",
    "CommandModule",
)

_CHILD_TAGS = (
    "Attribute",
    "TabularSection",
    "Dimension",
    "Resource",
    "EnumValue",
    "AccountingFlag",
    "ExtDimensionAccountingFlag",
    "AddressingAttribute",
    "Column",
    "Command",
)
_NAME_ONLY_TAGS = ("Form", "Template", "Recalculation")
_INTERESTING_PROPS = (
    "Hierarchical",
    "Owners",
    "Posting",
    "RegisterRecords",
    "BasedOn",
    "RegisterType",
    "InformationRegisterPeriodicity",
    "WriteMode",
    "Global",
    "Server",
    "ServerCall",
    "ClientManagedApplication",
    "ExternalConnection",
    "Privileged",
    "ReturnValuesReuse",
    "Source",
    "Event",
    "Handler",
    "MethodName",
    "ObjectBelonging",
    "ExtendedConfigurationObject",
)


class ObjectRef(BaseModel):
    """A resolved metadata object reference such as `Catalog.Товары`."""

    type: str
    name: str

    @property
    def folder(self) -> str:
        """Dump folder of the object's class."""
        return FOLDERS[self.type]

    def xml(self, root: Path) -> Path:
        """Path of the object's description file inside a dump."""
        return root / self.folder / f"{self.name}.xml"

    def directory(self, root: Path) -> Path:
        """Directory with the object's modules, forms and templates."""
        return root / self.folder / self.name

    def __str__(self) -> str:
        """Render as `Type.Name`."""
        return f"{self.type}.{self.name}"


def resolve_ref(text: str) -> ObjectRef:
    """Parse `Catalog.X`, `Справочник.X`, `Catalogs.X` or `Catalogs/X`.

    Args:
        text: Reference in English, Russian or folder form.

    Returns:
        The normalized reference.

    Raises:
        ConfigError: If the class is unknown or the name is missing.
    """
    parts = re.split(r"[./\\]", text.strip(), maxsplit=1)
    if len(parts) != 2 or not parts[1]:
        msg = f"expected <Type>.<Name>, got {text!r}"
        raise ConfigError(msg)
    kind, name = parts
    resolved = _LOWER.get(kind.lower()) or _FOLDER_TO_TYPE.get(kind.lower())
    if resolved is None:
        msg = f"unknown metadata class {kind!r}"
        raise ConfigError(msg)
    return ObjectRef(type=resolved, name=name)


def ref_from_path(root: Path, path: Path) -> ObjectRef | None:
    """Map a file inside a dump to the top-level object that owns it."""
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    if len(rel.parts) < 2:
        return None
    kind = _FOLDER_TO_TYPE.get(rel.parts[0].lower())
    if kind is None:
        return None
    name = rel.parts[1]
    return ObjectRef(type=kind, name=name[:-4] if name.endswith(".xml") else name)


def read_root(path: Path) -> ET.Element:
    """Parse a metadata XML file and return the object element under `MetaDataObject`."""
    try:
        tree = ET.parse(path)  # noqa: S314 - local platform dumps, not untrusted network input
    except ET.ParseError as exc:
        msg = f"cannot parse {path}: {exc}"
        raise ConfigError(msg) from exc
    children = list(tree.getroot())
    if not children:
        msg = f"empty metadata file: {path}"
        raise ConfigError(msg)
    return children[0]


def local(tag: str) -> str:
    """Strip the XML namespace from a tag."""
    return tag.rsplit("}", 1)[-1]


def synonym(props: ET.Element | None) -> str:
    """Return the first synonym text (prefers `ru`)."""
    if props is None:
        return ""
    items = props.findall("md:Synonym/v8:item", _NS)
    best = ""
    for item in items:
        lang = item.findtext("v8:lang", default="", namespaces=_NS)
        content = item.findtext("v8:content", default="", namespaces=_NS)
        if lang == "ru":
            return content
        best = best or content
    return best


def read_config_name(src: Path) -> str:
    """Read `Configuration/Properties/Name` from a dump (the extension name for `.cfe`)."""
    root = read_root(src / "Configuration.xml")
    name = root.findtext("md:Properties/md:Name", default="", namespaces=_NS)
    if not name:
        msg = f"no configuration name in {src / 'Configuration.xml'}"
        raise ConfigError(msg)
    return name


def format_type(type_el: ET.Element | None) -> str:
    """Render a `<Type>` element compactly: `String(25)`, `Number(15,3)`, `CatalogRef.X`."""
    if type_el is None:
        return ""
    names: list[str] = []
    for child in type_el:
        tag = local(child.tag)
        if tag in ("Type", "TypeSet"):
            names.append((child.text or "").split(":", 1)[-1])
    rendered: list[str] = []
    for name in names:
        if name == "string":
            length = type_el.findtext("v8:StringQualifiers/v8:Length", default="", namespaces=_NS)
            rendered.append(f"String({length})" if length and length != "0" else "String")
        elif name == "decimal":
            digits = type_el.findtext("v8:NumberQualifiers/v8:Digits", default="", namespaces=_NS)
            frac = type_el.findtext(
                "v8:NumberQualifiers/v8:FractionDigits", default="0", namespaces=_NS
            )
            rendered.append(f"Number({digits},{frac})" if digits else "Number")
        elif name == "dateTime":
            fractions = type_el.findtext(
                "v8:DateQualifiers/v8:DateFractions", default="", namespaces=_NS
            )
            rendered.append({"Date": "Date", "Time": "Time"}.get(fractions, "DateTime"))
        elif name == "boolean":
            rendered.append("Boolean")
        else:
            rendered.append(name)
    return " | ".join(rendered)


class MetaField(BaseModel):
    """One child element of an object (attribute, dimension, tabular section...)."""

    kind: str
    name: str
    type: str = ""
    synonym: str = ""
    belonging: str = ""
    children: list["MetaField"] = Field(default_factory=list)


class ModuleInfo(BaseModel):
    """A module file and its methods."""

    name: str
    path: Path
    methods: list[Method] = Field(default_factory=list)


class ObjectSummary(BaseModel):
    """Compact view of one metadata object."""

    ref: str
    uuid: str
    synonym: str = ""
    properties: dict[str, str] = Field(default_factory=dict)
    fields: list[MetaField] = Field(default_factory=list)
    forms: list[str] = Field(default_factory=list)
    templates: list[str] = Field(default_factory=list)
    modules: list[ModuleInfo] = Field(default_factory=list)


def _prop_text(el: ET.Element) -> str:
    items = [i.text or "" for i in el if local(i.tag) == "Item"]
    if items:
        return ", ".join(items)
    return (el.text or "").strip()


def _fields(child_objects: ET.Element | None) -> tuple[list[MetaField], dict[str, list[str]]]:
    fields: list[MetaField] = []
    names: dict[str, list[str]] = {}
    if child_objects is None:
        return fields, names
    for child in child_objects:
        tag = local(child.tag)
        if tag in _NAME_ONLY_TAGS:
            names.setdefault(tag, []).append((child.text or "").strip())
            continue
        if tag not in _CHILD_TAGS:
            continue
        props = child.find("md:Properties", _NS)
        name = props.findtext("md:Name", default="", namespaces=_NS) if props is not None else ""
        nested, _ = _fields(child.find("md:ChildObjects", _NS))
        fields.append(
            MetaField(
                kind=tag,
                name=name,
                type=format_type(props.find("md:Type", _NS)) if props is not None else "",
                synonym=synonym(props),
                belonging=(
                    props.findtext("md:ObjectBelonging", default="", namespaces=_NS)
                    if props is not None
                    else ""
                ),
                children=nested,
            )
        )
    return fields, names


def module_files(root: Path, ref: ObjectRef) -> list[tuple[str, Path]]:
    """List `(label, path)` of the object's modules, including form and command modules."""
    directory = ref.directory(root)
    found: list[tuple[str, Path]] = []
    ext = directory / "Ext"
    for name in _MODULE_FILES:
        path = ext / f"{name}.bsl"
        if path.is_file():
            found.append((name, path))
    form_module = ext / "Form" / "Module.bsl"
    if form_module.is_file():
        found.append(("Form", form_module))
    for sub, label in (("Forms", "Form"), ("Commands", "Command")):
        base = directory / sub
        if not base.is_dir():
            continue
        for item in sorted(base.iterdir()):
            if not item.is_dir():
                continue
            for candidate in (
                item / "Ext" / "Form" / "Module.bsl",
                item / "Ext" / "CommandModule.bsl",
            ):
                if candidate.is_file():
                    found.append((f"{label}.{item.name}", candidate))
    return found


def show_object(root: Path, ref: ObjectRef, *, with_methods: bool = False) -> ObjectSummary:
    """Summarize one object of a dump.

    Args:
        root: Dump root (directory with `Configuration.xml`).
        ref: Object reference.
        with_methods: Parse modules and list their methods.

    Returns:
        The summary.

    Raises:
        ConfigError: If the object is not in the dump.
    """
    path = ref.xml(root)
    if not path.is_file():
        msg = f"{ref} not found in {root} (expected {path})"
        raise ConfigError(msg)
    element = read_root(path)
    props = element.find("md:Properties", _NS)
    properties: dict[str, str] = {}
    if props is not None:
        for prop in props:
            tag = local(prop.tag)
            if tag in _INTERESTING_PROPS:
                value = _prop_text(prop)
                if value:
                    properties[tag] = value
    fields, names = _fields(element.find("md:ChildObjects", _NS))
    modules = [
        ModuleInfo(
            name=label,
            path=module_path,
            methods=parse_methods(module_path.read_text(encoding="utf-8-sig", errors="replace"))
            if with_methods
            else [],
        )
        for label, module_path in module_files(root, ref)
    ]
    return ObjectSummary(
        ref=str(ref),
        uuid=element.get("uuid", ""),
        synonym=synonym(props),
        properties=properties,
        fields=fields,
        forms=names.get("Form", []),
        templates=names.get("Template", []),
        modules=modules,
    )


class FoundObject(BaseModel):
    """One `meta find` hit."""

    ref: str
    synonym: str = ""


_SYNONYM_RE = re.compile(
    r"<Synonym>\s*<v8:item>\s*<v8:lang>[^<]*</v8:lang>\s*<v8:content>([^<]*)</v8:content>"
)


def find_objects(
    root: Path, pattern: str, *, kind: str | None = None, limit: int = 200
) -> list[FoundObject]:
    """Find objects whose name or synonym matches a case-insensitive regex.

    Args:
        root: Dump root.
        pattern: Regular expression (a plain substring works too).
        kind: Restrict to one class (`Catalog`, `Справочник`...).
        limit: Maximum number of hits.

    Returns:
        Matching objects sorted by class and name.
    """
    regex = re.compile(pattern, re.IGNORECASE)
    types = [resolve_ref(f"{kind}.x").type] if kind else list(FOLDERS)
    hits: list[FoundObject] = []
    for type_name in types:
        folder = root / FOLDERS[type_name]
        if not folder.is_dir():
            continue
        for xml in sorted(folder.glob("*.xml")):
            name = xml.stem
            head = xml.read_text(encoding="utf-8-sig", errors="replace")[:6000]
            match = _SYNONYM_RE.search(head)
            syn = match.group(1) if match else ""
            if regex.search(name) or (syn and regex.search(syn)):
                hits.append(FoundObject(ref=f"{type_name}.{name}", synonym=syn))
                if len(hits) >= limit:
                    return hits
    return hits


def render_summary(summary: ObjectSummary, root: Path) -> str:
    """Render a summary as compact plain text (cheap to read for an agent)."""
    lines = [f"{summary.ref}" + (f" — {summary.synonym}" if summary.synonym else "")]
    lines.append(f"  uuid: {summary.uuid}")
    for key, value in summary.properties.items():
        lines.append(f"  {key}: {value}")

    def emit(fields: list[MetaField], indent: str) -> None:
        for f in fields:
            parts = [f"{indent}{f.kind} {f.name}"]
            if f.type:
                parts.append(f": {f.type}")
            if f.synonym and f.synonym != f.name:
                parts.append(f"  «{f.synonym}»")
            if f.belonging:
                parts.append(f"  [{f.belonging}]")
            lines.append("".join(parts))
            emit(f.children, indent + "    ")

    emit(summary.fields, "  ")
    if summary.forms:
        lines.append(f"  Forms: {', '.join(summary.forms)}")
    if summary.templates:
        lines.append(f"  Templates: {', '.join(summary.templates)}")
    for module in summary.modules:
        rel = module.path.relative_to(root) if module.path.is_relative_to(root) else module.path
        lines.append(f"  Module {module.name}: {rel.as_posix()}")
        for m in module.methods:
            flags = " Export" if m.export else ""
            notes = f" {' '.join('&' + a for a in m.annotations)}" if m.annotations else ""
            lines.append(f"      {m.line:>5}  {m.kind} {m.name}({m.params}){flags}{notes}")
    return "\n".join(lines)
