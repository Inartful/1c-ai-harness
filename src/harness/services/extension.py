"""Configuration extension (`.cfe`) source helpers: adopt, intercept, lint.

All operations work on XML dumps (`harness unpack`): the extension dump and the base dump.
The generated XML follows what `ibcmd` 8.3.27 accepts and round-trips; `harness pack`
then validates it with a real import and `config apply`.
"""

import re
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from pydantic import BaseModel, Field

from harness.errors import ConfigError
from harness.services.bsl import (
    Method,
    body_lines,
    find_method,
    normalize_body,
    original_from_change_and_validate,
    param_names,
    parse_methods,
)
from harness.services.meta import (
    _NS,
    FOLDERS,
    ObjectRef,
    local,
    read_root,
    ref_from_path,
)

MODES = {
    "before": "Перед",
    "after": "После",
    "instead": "Вместо",
    "change": "ИзменениеИКонтроль",
}
MODULES = (
    "Module",
    "ObjectModule",
    "ManagerModule",
    "RecordSetModule",
    "ValueManagerModule",
    "CommandModule",
)
# Properties that the platform keeps on an adopted common module (verified round-trip).
_COMMON_MODULE_FLAGS = (
    "Global",
    "ClientManagedApplication",
    "Server",
    "ExternalConnection",
    "ClientOrdinaryApplication",
    "ServerCall",
)
_UUID_TAG_RE = re.compile(r"(<xr:(?:TypeId|ValueId|ObjectId)>)[0-9a-fA-F-]{36}(</xr:)")
_PROPERTY_STATE_RE = re.compile(r"\s*<xr:PropertyState>.*?</xr:PropertyState>", re.DOTALL)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _write(path: Path, text: str) -> None:
    """Write UTF-8 with BOM, keeping CRLF only if the existing file already used it.

    Platform exports (8.3.27) are LF + BOM; a git checkout with autocrlf may be CRLF.
    """
    crlf = path.is_file() and b"\r\n" in path.read_bytes()[:4096]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig", newline="\r\n" if crlf else "\n")


def _header(ext_src: Path) -> str:
    match = re.search(r"<MetaDataObject[^>]*>", _read(ext_src / "Configuration.xml"))
    if match is None:
        msg = f"no MetaDataObject header in {ext_src / 'Configuration.xml'}"
        raise ConfigError(msg)
    return match.group(0)


class ExtensionInfo(BaseModel):
    """Name, prefix and script variant of an extension dump."""

    name: str
    prefix: str
    english: bool


def extension_info(ext_src: Path) -> ExtensionInfo:
    """Read the extension's name, name prefix and script variant."""
    root = read_root(ext_src / "Configuration.xml")
    props = root.find("md:Properties", _NS)
    if props is None or props.findtext("md:ObjectBelonging", namespaces=_NS) != "Adopted":
        msg = f"{ext_src} is not an extension dump (Configuration is not Adopted)"
        raise ConfigError(msg)
    name = props.findtext("md:Name", default="", namespaces=_NS)
    prefix = props.findtext("md:NamePrefix", default="", namespaces=_NS) or ""
    english = props.findtext("md:ScriptVariant", default="Russian", namespaces=_NS) == "English"
    return ExtensionInfo(name=name, prefix=prefix or f"{name}_", english=english)


def registered_objects(ext_src: Path) -> set[str]:
    """Return `Type.Name` of every object listed in `Configuration.xml` ChildObjects."""
    root = read_root(ext_src / "Configuration.xml")
    children = root.find("md:ChildObjects", _NS)
    if children is None:
        return set()
    return {f"{local(c.tag)}.{(c.text or '').strip()}" for c in children}


def _register(ext_src: Path, ref: ObjectRef) -> None:
    path = ext_src / "Configuration.xml"
    text = _read(path)
    entry = f"<{ref.type}>{ref.name}</{ref.type}>"
    if entry in text:
        return
    if "<ChildObjects/>" in text:
        text = text.replace(
            "<ChildObjects/>", f"<ChildObjects>\n\t\t\t{entry}\n\t\t</ChildObjects>", 1
        )
    else:
        idx = text.rfind("</ChildObjects>")
        if idx < 0:
            msg = f"no ChildObjects in {path}"
            raise ConfigError(msg)
        line_start = text.rfind("\n", 0, idx) + 1
        text = f"{text[:line_start]}\t\t\t{entry}\n{text[line_start:]}"
    _write(path, text)


class AdoptResult(BaseModel):
    """Outcome of adopting one object."""

    ref: str
    path: Path
    created: bool


def adopt(ext_src: Path, base_src: Path, ref: ObjectRef) -> AdoptResult:
    """Adopt a base object into the extension dump (like "Добавить в расширение").

    Args:
        ext_src: Extension XML dump.
        base_src: Base configuration XML dump.
        ref: Object to adopt.

    Returns:
        Where the object XML lives and whether it was created now.

    Raises:
        ConfigError: If the base object is missing or the extension has an own object with
            the same name.
    """
    base_xml = ref.xml(base_src)
    if not base_xml.is_file():
        msg = f"{ref} not found in base dump {base_src}"
        raise ConfigError(msg)
    ext_xml = ref.xml(ext_src)
    if ext_xml.is_file():
        belonging = read_root(ext_xml).findtext("md:Properties/md:ObjectBelonging", namespaces=_NS)
        if belonging != "Adopted":
            msg = f"{ref} exists in the extension as an own object, not adopted"
            raise ConfigError(msg)
        _register(ext_src, ref)
        return AdoptResult(ref=str(ref), path=ext_xml, created=False)

    text = _read(base_xml)
    opening = re.search(rf"<{ref.type} uuid=\"([0-9a-fA-F-]{{36}})\"", text)
    if opening is None:
        msg = f"cannot find <{ref.type} uuid=...> in {base_xml}"
        raise ConfigError(msg)
    base_uuid = opening.group(1)
    props_at = text.find("<Properties>", opening.end())
    head = text[opening.end() : props_at]
    info_match = re.search(r"<InternalInfo>.*?</InternalInfo>", head, re.DOTALL)
    internal = ""
    if info_match is not None:
        internal = _PROPERTY_STATE_RE.sub("", info_match.group(0))
        internal = _UUID_TAG_RE.sub(lambda m: f"{m.group(1)}{uuid.uuid4()}{m.group(2)}", internal)

    base_element = read_root(base_xml)
    base_props = base_element.find("md:Properties", _NS)
    # The platform rejects an adopted Catalog without <ChildObjects/> but a CommonModule with
    # one: mirror whether the base object has the node at all.
    has_children = base_element.find("md:ChildObjects", _NS) is not None
    extra: list[str] = []
    if ref.type == "CommonModule" and base_props is not None:
        for flag in _COMMON_MODULE_FLAGS:
            value = base_props.findtext(f"md:{flag}", namespaces=_NS)
            if value is not None:
                extra.append(f"\t\t\t<{flag}>{value}</{flag}>")
    properties = "\n".join(
        [
            "\t\t<Properties>",
            "\t\t\t<ObjectBelonging>Adopted</ObjectBelonging>",
            f"\t\t\t<Name>{ref.name}</Name>",
            "\t\t\t<Comment/>",
            f"\t\t\t<ExtendedConfigurationObject>{base_uuid}</ExtendedConfigurationObject>",
            *extra,
            "\t\t</Properties>",
        ]
    )
    body = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            _header(ext_src),
            f'\t<{ref.type} uuid="{uuid.uuid4()}">',
            *([f"\t\t{internal}"] if internal else []),
            properties,
            *(["\t\t<ChildObjects/>"] if has_children else []),
            f"\t</{ref.type}>",
            "</MetaDataObject>",
            "",
        ]
    )
    _write(ext_xml, body)
    _register(ext_src, ref)
    return AdoptResult(ref=str(ref), path=ext_xml, created=True)


class InterceptResult(BaseModel):
    """Outcome of `ext intercept`."""

    module: Path
    method: str
    code: str
    adopted: bool


def module_rel(ref: ObjectRef, module: str | None) -> Path:
    """Relative path of an object module inside a dump."""
    name = module or ("Module" if ref.type == "CommonModule" else "ObjectModule")
    if name not in MODULES:
        msg = f"unknown module {name!r}; expected one of {', '.join(MODULES)}"
        raise ConfigError(msg)
    return Path(ref.folder) / ref.name / "Ext" / f"{name}.bsl"


def _stub(base_text: str, method: Method, mode: str, new_name: str, *, english: bool) -> list[str]:
    kw_proc, kw_func = ("Procedure", "Function") if english else ("Процедура", "Функция")
    end_proc, end_func = (
        ("EndProcedure", "EndFunction")
        if english
        else (
            "КонецПроцедуры",
            "КонецФункции",
        )
    )
    proceed = "ProceedWithCall" if english else "ПродолжитьВызов"
    result_var = "Result" if english else "Результат"
    ret = "Return" if english else "Возврат"
    directives = [f"&{a}" for a in method.annotations if not a.startswith(("Перед", "После"))]
    annotation = f'&{MODES[mode]}("{method.name}")'
    func = method.is_function
    keyword, end = (kw_func, end_func) if func else (kw_proc, end_proc)
    if mode in ("before", "after"):
        keyword, end = kw_proc, end_proc
    lines = [*directives, annotation, f"{keyword} {new_name}({method.params})"]
    args = ", ".join(param_names(method.params))
    if mode in ("before", "after"):
        lines.append("\t// TODO")
    elif mode == "instead":
        if func:
            lines += [f"\t{result_var} = {proceed}({args});", f"\t{ret} {result_var};"]
        else:
            lines.append(f"\t{proceed}({args});")
    else:
        lines += body_lines(base_text, method)
    lines.append(end)
    return lines


def intercept(
    ext_src: Path,
    base_src: Path,
    ref: ObjectRef,
    method_name: str,
    mode: str,
    *,
    module: str | None = None,
) -> InterceptResult:
    """Add an interception stub for a base method to the extension module.

    The object is adopted first when needed. For `change` (`&ИзменениеИКонтроль`) the base
    method body is copied verbatim so `#Вставка`/`#Удаление` markers can be added around it.

    Args:
        ext_src: Extension XML dump.
        base_src: Base configuration XML dump.
        ref: Object that owns the module.
        method_name: Base method name.
        mode: `before`, `after`, `instead` or `change`.
        module: Module file name (`ObjectModule`, `ManagerModule`, ...); auto by default.

    Returns:
        The module path and the generated code.

    Raises:
        ConfigError: If the base module or method is missing or already intercepted.
    """
    if mode not in MODES:
        msg = f"unknown mode {mode!r}; expected one of {', '.join(MODES)}"
        raise ConfigError(msg)
    rel = module_rel(ref, module)
    base_module = base_src / rel
    if not base_module.is_file():
        msg = f"base module not found: {base_module}"
        raise ConfigError(msg)
    base_text = _read(base_module)
    method = find_method(parse_methods(base_text), method_name)
    if method is None:
        msg = f"method {method_name} not found in {rel.as_posix()}"
        raise ConfigError(msg)
    info = extension_info(ext_src)
    ext_module = ext_src / rel
    ext_text = _read(ext_module) if ext_module.is_file() else ""
    for existing in parse_methods(ext_text):
        if (
            existing.target
            and existing.target.lower() == method.name.lower()
            and existing.interception == MODES[mode]
        ):
            msg = f"{method.name} is already intercepted with &{MODES[mode]} in {rel.as_posix()}"
            raise ConfigError(msg)
    adopted = adopt(ext_src, base_src, ref).created
    taken = {m.name.lower() for m in parse_methods(ext_text)}
    candidate = f"{info.prefix}{method.name}"
    if candidate.lower() in taken:
        candidate = f"{candidate}{MODES[mode]}"
    stem, suffix = candidate, 2
    while candidate.lower() in taken:
        candidate = f"{stem}{suffix}"
        suffix += 1
    code = "\n".join(_stub(base_text, method, mode, candidate, english=info.english))
    existing_text = ext_text.rstrip()
    _write(ext_module, f"{existing_text}\n\n{code}\n" if existing_text else f"{code}\n")
    return InterceptResult(module=ext_module, method=candidate, code=code, adopted=adopted)


class LintIssue(BaseModel):
    """One `ext lint` finding."""

    level: str
    path: str
    line: int = 0
    message: str


class LintReport(BaseModel):
    """Outcome of `ext lint`."""

    extension: str
    objects: int = 0
    interceptions: int = 0
    issues: list[LintIssue] = Field(default_factory=list)

    @property
    def errors(self) -> int:
        """Number of error-level issues."""
        return sum(1 for i in self.issues if i.level == "error")


def _child_index(element: ET.Element) -> dict[tuple[str, str], str]:
    """Map `(tag, name)` of nested children to their uuid."""
    found: dict[tuple[str, str], str] = {}
    for child in element.iter():
        props = child.find("md:Properties", _NS)
        if props is None or child is element:
            continue
        name = props.findtext("md:Name", default="", namespaces=_NS)
        if name:
            found[(local(child.tag), name)] = child.get("uuid", "")
    return found


def _lint_objects(ext_src: Path, base_src: Path, report: LintReport, prefix: str) -> None:
    registered = registered_objects(ext_src)
    on_disk: set[str] = set()
    for type_name, folder in FOLDERS.items():
        directory = ext_src / folder
        if not directory.is_dir():
            continue
        for xml in sorted(directory.glob("*.xml")):
            ref = ObjectRef(type=type_name, name=xml.stem)
            on_disk.add(str(ref))
            report.objects += 1
            rel = xml.relative_to(ext_src).as_posix()
            if str(ref) not in registered:
                report.issues.append(
                    LintIssue(level="error", path=rel, message="not listed in Configuration.xml")
                )
            element = read_root(xml)
            props = element.find("md:Properties", _NS)
            belonging = (
                props.findtext("md:ObjectBelonging", namespaces=_NS) if props is not None else None
            )
            if belonging != "Adopted":
                if type_name != "Role" and prefix and not ref.name.startswith(prefix):
                    report.issues.append(
                        LintIssue(
                            level="warning",
                            path=rel,
                            message=f"own object without name prefix {prefix!r}",
                        )
                    )
                continue
            base_xml = ref.xml(base_src)
            if not base_xml.is_file():
                report.issues.append(
                    LintIssue(level="error", path=rel, message=f"adopted {ref} missing in base")
                )
                continue
            base_el = read_root(base_xml)
            expected = (
                props.findtext("md:ExtendedConfigurationObject", namespaces=_NS)
                if props is not None
                else None
            )
            if expected != base_el.get("uuid"):
                report.issues.append(
                    LintIssue(
                        level="error",
                        path=rel,
                        message=(
                            f"ExtendedConfigurationObject {expected} != base uuid "
                            f"{base_el.get('uuid')} (object re-created in base?)"
                        ),
                    )
                )
            base_children = _child_index(base_el)
            for child in element.iter():
                child_props = child.find("md:Properties", _NS)
                if child is element or child_props is None:
                    continue
                if child_props.findtext("md:ObjectBelonging", namespaces=_NS) != "Adopted":
                    continue
                key = (
                    local(child.tag),
                    child_props.findtext("md:Name", default="", namespaces=_NS),
                )
                want = child_props.findtext("md:ExtendedConfigurationObject", namespaces=_NS)
                if key not in base_children:
                    report.issues.append(
                        LintIssue(
                            level="error",
                            path=rel,
                            message=f"adopted {key[0]} {key[1]} missing in base {ref}",
                        )
                    )
                elif want and base_children[key] != want:
                    report.issues.append(
                        LintIssue(
                            level="error",
                            path=rel,
                            message=f"adopted {key[0]} {key[1]}: uuid differs from base",
                        )
                    )
    for missing in sorted(
        r for r in registered - on_disk if not r.startswith(("Language.", "Role."))
    ):
        report.issues.append(
            LintIssue(
                level="error",
                path="Configuration.xml",
                message=f"{missing} listed but its XML is missing",
            )
        )


def _lint_modules(ext_src: Path, base_src: Path, report: LintReport) -> None:
    for module in sorted(ext_src.rglob("*.bsl")):
        rel = module.relative_to(ext_src)
        rel_text = rel.as_posix()
        ext_text = _read(module)
        methods = parse_methods(ext_text)
        intercepting = [m for m in methods if m.interception]
        if not intercepting:
            continue
        base_module = base_src / rel
        owner = ref_from_path(ext_src, module)
        if not base_module.is_file():
            for m in intercepting:
                report.issues.append(
                    LintIssue(
                        level="error",
                        path=rel_text,
                        line=m.line,
                        message=f"&{m.interception}: base module missing ({owner})",
                    )
                )
            continue
        base_text = _read(base_module)
        base_methods = parse_methods(base_text)
        seen: set[tuple[str, str]] = set()
        for m in intercepting:
            report.interceptions += 1
            target = m.target or ""
            key = (m.interception or "", target.lower())
            if key in seen:
                report.issues.append(
                    LintIssue(
                        level="error",
                        path=rel_text,
                        line=m.line,
                        message=f'duplicate &{m.interception}("{target}")',
                    )
                )
            seen.add(key)
            base_method = find_method(base_methods, target)
            if base_method is None:
                report.issues.append(
                    LintIssue(
                        level="error",
                        path=rel_text,
                        line=m.line,
                        message=(
                            f'&{m.interception}("{target}"): no such method in base '
                            "(the platform does not report this at build time)"
                        ),
                    )
                )
                continue
            if m.interception == "ИзменениеИКонтроль":
                claimed = normalize_body(original_from_change_and_validate(body_lines(ext_text, m)))
                actual = normalize_body(body_lines(base_text, base_method))
                if claimed != actual:
                    first = next(
                        (
                            i
                            for i, (a, b) in enumerate(zip(claimed, actual, strict=False))
                            if a != b
                        ),
                        min(len(claimed), len(actual)),
                    )
                    hint = actual[first] if first < len(actual) else "<end of base method>"
                    report.issues.append(
                        LintIssue(
                            level="error",
                            path=rel_text,
                            line=m.line,
                            message=(
                                f'&ИзменениеИКонтроль("{target}"): body differs from base at '
                                f"statement {first + 1}; base has: {hint}"
                            ),
                        )
                    )
            if (
                m.params
                and base_method.params
                and (len(param_names(m.params)) != len(param_names(base_method.params)))
            ):
                report.issues.append(
                    LintIssue(
                        level="warning",
                        path=rel_text,
                        line=m.line,
                        message=(
                            f"{m.name}: {len(param_names(m.params))} params, base "
                            f"{base_method.name} has {len(param_names(base_method.params))}"
                        ),
                    )
                )


def lint(ext_src: Path, base_src: Path) -> LintReport:
    """Check an extension dump against the base dump.

    Catches what the platform silently accepts: interceptions of non-existent methods,
    `&ИзменениеИКонтроль` bodies that no longer match the base, adopted objects whose uuid
    no longer matches, and objects missing from `Configuration.xml`.

    Args:
        ext_src: Extension XML dump.
        base_src: Base configuration XML dump.

    Returns:
        The report.
    """
    info = extension_info(ext_src)
    report = LintReport(extension=info.name)
    raw_prefix = read_root(ext_src / "Configuration.xml").findtext(
        "md:Properties/md:NamePrefix", default="", namespaces=_NS
    )
    _lint_objects(ext_src, base_src, report, raw_prefix or "")
    _lint_modules(ext_src, base_src, report)
    return report
