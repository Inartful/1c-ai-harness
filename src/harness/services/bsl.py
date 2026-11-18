"""Minimal BSL module parser: method headers, annotations and bodies.

It is line-based on purpose: it only has to find `Процедура`/`Функция` blocks reliably, which
is what extension interception and `meta show --methods` need.
"""

import re

from pydantic import BaseModel

_HEADER_RE = re.compile(r"^\s*(Процедура|Функция|Procedure|Function)\s+(\w+)\s*\(", re.IGNORECASE)
_END_RE = re.compile(r"^\s*(КонецПроцедуры|КонецФункции|EndProcedure|EndFunction)\b", re.IGNORECASE)
_ANNOTATION_RE = re.compile(r"^\s*&\s*(\w+)\s*(?:\(\s*\"([^\"]*)\"\s*\))?")
_MAX_HEADER_LINES = 60
_EXPORT_RE = re.compile(r"^\s*(Экспорт|Export)\b", re.IGNORECASE)

INTERCEPTIONS: dict[str, str] = {
    "перед": "Перед",
    "before": "Перед",
    "после": "После",
    "after": "После",
    "вместо": "Вместо",
    "around": "Вместо",
    "изменениеиконтроль": "ИзменениеИКонтроль",
    "changeandvalidate": "ИзменениеИКонтроль",
}


class Method(BaseModel):
    """A procedure or function found in a module."""

    name: str
    kind: str
    params: str
    export: bool
    line: int
    end_line: int
    annotations: list[str]
    interception: str | None = None
    target: str | None = None

    @property
    def is_function(self) -> bool:
        """Whether this is a function (returns a value)."""
        return self.kind.lower() in ("функция", "function")


def _split_params(text: str, start: int) -> tuple[str, int]:
    """Return the parameter text after `(` at `start` and the index after the closing `)`."""
    depth = 1
    in_string = False
    i = start
    while i < len(text):
        ch = text[i]
        if ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return text[start:i], i + 1
        i += 1
    return text[start:], len(text)


def parse_methods(text: str) -> list[Method]:
    """Parse all top-level methods of a BSL module.

    Args:
        text: Module source.

    Returns:
        Methods in source order, with 1-based line numbers.
    """
    lines = text.splitlines()
    methods: list[Method] = []
    i = 0
    while i < len(lines):
        match = _HEADER_RE.match(lines[i])
        if match is None:
            i += 1
            continue
        annotations: list[str] = []
        interception: str | None = None
        target: str | None = None
        j = i - 1
        while j >= 0 and lines[j].strip().startswith("&"):
            ann = _ANNOTATION_RE.match(lines[j])
            if ann is not None:
                raw = lines[j].strip()[1:].strip()
                annotations.insert(0, raw)
                kind = INTERCEPTIONS.get(ann.group(1).lower())
                if kind is not None:
                    interception, target = kind, ann.group(2)
            j -= 1
        rest = "\n".join(lines[i : i + _MAX_HEADER_LINES])
        params, after = _split_params(rest, match.end())
        params_rest = rest[after:]
        header_lines = rest[:after].count("\n")
        export = bool(_EXPORT_RE.match(params_rest))
        end = i + header_lines
        while end < len(lines) and _END_RE.match(lines[end]) is None:
            end += 1
        methods.append(
            Method(
                name=match.group(2),
                kind=match.group(1),
                params=" ".join(params.split()),
                export=export,
                line=i + 1,
                end_line=min(end, len(lines) - 1) + 1,
                annotations=annotations,
                interception=interception,
                target=target,
            )
        )
        i = end + 1
    return methods


def find_method(methods: list[Method], name: str) -> Method | None:
    """Find a method by name (case-insensitive, as BSL is)."""
    lowered = name.lower()
    return next((m for m in methods if m.name.lower() == lowered), None)


def method_lines(text: str, method: Method) -> list[str]:
    """Return the method's source lines from header to `КонецПроцедуры`/`КонецФункции`."""
    return text.splitlines()[method.line - 1 : method.end_line]


def body_lines(text: str, method: Method) -> list[str]:
    """Return the lines strictly between the header (incl. multi-line params) and the end."""
    lines = method_lines(text, method)
    joined = "\n".join(lines)
    start = joined.find("(")
    _, after = _split_params(joined, start + 1)
    header_end = joined[:after].count("\n")
    return lines[header_end + 1 : -1]


def param_names(params: str) -> list[str]:
    """Extract parameter names from a parameter list (drops `Знач`/`Val` and defaults)."""
    names: list[str] = []
    for raw in _split_top_level(params):
        part = raw.split("=", 1)[0].strip()
        part = re.sub(r"^(Знач|Val)\s+", "", part, flags=re.IGNORECASE)
        if part:
            names.append(part)
    return names


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    in_string = False
    current: list[str] = []
    for ch in text:
        if ch == '"':
            in_string = not in_string
        elif not in_string and ch == "(":
            depth += 1
        elif not in_string and ch == ")":
            depth -= 1
        elif not in_string and depth == 0 and ch == ",":
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if "".join(current).strip():
        parts.append("".join(current))
    return parts


_INSERT_START = re.compile(r"^\s*#(Вставка|Insert)\b", re.IGNORECASE)
_INSERT_END = re.compile(r"^\s*#(КонецВставки|EndInsert)\b", re.IGNORECASE)
_DELETE_START = re.compile(r"^\s*#(Удаление|Delete)\b", re.IGNORECASE)
_DELETE_END = re.compile(r"^\s*#(КонецУдаления|EndDelete)\b", re.IGNORECASE)


def original_from_change_and_validate(body: list[str]) -> list[str]:
    """Rebuild the base method body that a `&ИзменениеИКонтроль` body claims to modify.

    `#Вставка` blocks are dropped and `#Удаление` blocks are kept without their markers.
    """
    result: list[str] = []
    inserting = False
    for line in body:
        if _INSERT_START.match(line):
            inserting = True
            continue
        if _INSERT_END.match(line):
            inserting = False
            continue
        if _DELETE_START.match(line) or _DELETE_END.match(line):
            continue
        if not inserting:
            result.append(line)
    return result


def normalize_body(lines: list[str]) -> list[str]:
    """Normalize lines for comparison: strip, drop blanks and full-line comments."""
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        out.append(" ".join(stripped.split()))
    return out
