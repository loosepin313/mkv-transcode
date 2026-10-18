"""Read a TOML file on any Python from 3.9 upwards.

Python 3.11 added ``tomllib``. Rocky Linux 9 and Fedora 38 ship older Python
builds. This module uses ``tomllib`` or ``tomli`` when present. Otherwise it
uses the small parser below. The parser covers the subset of TOML that this
project uses: tables, key/value pairs, strings, integers, floats, booleans and
arrays of scalars. It raises ``ValueError`` on anything it does not understand.
"""

from __future__ import annotations

import re
from typing import Any, Dict

try:  # Python 3.11+
    import tomllib as _toml_lib
except ModuleNotFoundError:  # pragma: no cover - depends on the interpreter
    try:
        import tomli as _toml_lib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        _toml_lib = None

_STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
_KEY_RE = re.compile(r'^[A-Za-z0-9_.\-"]+$')


def _unquote(raw: str) -> str:
    body = raw[1:-1]
    escapes = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
    out = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\":
            index += 1
            if index >= len(body):
                raise ValueError("string ends with a backslash")
            out.append(escapes.get(body[index], body[index]))
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _split_array(body: str) -> list:
    parts, depth, current, in_string = [], 0, [], False
    index = 0
    while index < len(body):
        char = body[index]
        if char == '"':
            match = _STRING_RE.match(body[index:])
            if not match:
                raise ValueError("unterminated string in array")
            current.append(match.group(0))
            index += match.end()
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        if char == "," and depth == 0 and not in_string:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    if depth != 0:
        raise ValueError("unbalanced array brackets")
    if current:
        parts.append("".join(current))
    return parts


def _scalar(token: str) -> Any:
    token = token.strip()
    if not token:
        raise ValueError("empty value")
    if token.startswith('"'):
        match = _STRING_RE.fullmatch(token)
        if not match:
            raise ValueError("bad string: %s" % token)
        return _unquote(match.group(0))
    lowered = token.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    plain = token.replace("_", "")
    try:
        return int(plain)
    except ValueError:
        pass
    try:
        return float(plain)
    except ValueError:
        raise ValueError("bad value: %s" % token)


def _parse_value(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("["):
        if not raw.endswith("]"):
            raise ValueError("unbalanced array: %s" % raw)
        body = raw[1:-1].strip()
        if not body:
            return []
        return [_parse_value(part) for part in _split_array(body)]
    return _scalar(raw)


def _strip_comment(line: str) -> str:
    out, in_string, index = [], False, 0
    while index < len(line):
        char = line[index]
        if char == '"':
            match = _STRING_RE.match(line[index:])
            if match:
                out.append(match.group(0))
                index += match.end()
                continue
        if char == "#" and not in_string:
            break
        out.append(char)
        index += 1
    return "".join(out).strip()


def _loads(text: str) -> Dict[str, Any]:
    root: Dict[str, Any] = {}
    table = root
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw_line)
        if not line:
            continue
        if line.startswith("["):
            if not line.endswith("]"):
                raise ValueError("line %d: bad table header" % number)
            name = line[1:-1].strip().strip('"')
            if not _KEY_RE.match(name):
                raise ValueError("line %d: bad table name" % number)
            table = root
            for part in name.split("."):
                child = table.setdefault(part, {})
                if not isinstance(child, dict):
                    raise ValueError("line %d: %s is not a table" % (number, part))
                table = child
            continue
        if "=" not in line:
            raise ValueError("line %d: expected key = value" % number)
        key, value = line.split("=", 1)
        key = key.strip().strip('"')
        if not _KEY_RE.match(key):
            raise ValueError("line %d: bad key" % number)
        table[key] = _parse_value(value)
    return root


def load(path: str) -> Dict[str, Any]:
    """Return the parsed TOML file at ``path`` as a dictionary."""
    with open(path, "rb") as handle:
        data = handle.read()
    if _toml_lib is not None:
        return _toml_lib.loads(data.decode("utf-8"))
    return _loads(data.decode("utf-8"))
