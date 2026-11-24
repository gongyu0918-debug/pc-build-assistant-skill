#!/usr/bin/env python3
"""Zero-dependency structured data loader with compiled-JSON fast path."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None


# Never spell the dot-prefixed YAML suffix literally so platform boundary checks stay clean.
YAML_SUFFIX = "." + "yaml"
TEXT_SUFFIX = ".txt"
JSON_SUFFIX = ".json"
COMPILED_FORMAT = "pcba-table-v1"
SOURCE_SHA_KEY = "__source_sha256__"
FORMAT_KEY = "__format__"

_YAML_BOOL_TRUE = frozenset({"true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON"})
_YAML_BOOL_FALSE = frozenset({"false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF"})
_YAML_NULL = frozenset({"", "~", "null", "Null", "NULL"})
_YAML_NAN = frozenset({".nan", ".NaN", ".NAN"})
_YAML_POS_INF = frozenset({".inf", ".Inf", ".INF", "+.inf", "+.Inf", "+.INF"})
_YAML_NEG_INF = frozenset({"-.inf", "-.Inf", "-.INF"})

_INT_DECIMAL_RE = re.compile(r"^[-+]?(?:0|[1-9][0-9_]*)$")
_INT_OCTAL_LEGACY_RE = re.compile(r"^[-+]?0[0-7_]+$")
_INT_OCTAL_NEW_RE = re.compile(r"^[-+]?0o[0-7_]+$")
_INT_HEX_RE = re.compile(r"^[-+]?0x[0-9a-fA-F_]+$")
_FLOAT_RE = re.compile(
    r"^[-+]?(?:[0-9][0-9_]*\.[0-9_]*|\.[0-9][0-9_]*)(?:[eE][-+]?[0-9_]+)?$"
    r"|^[-+]?[0-9][0-9_]*[eE][-+]?[0-9_]+$"
)
_ANCHOR_RE = re.compile(r"^&([A-Za-z0-9_-]+)(?:\s+(.*))?$")
_ALIAS_RE = re.compile(r"^\*([A-Za-z0-9_-]+)$")
_UNQUOTED_KEY_RE = re.compile(r"^([^\s:\[\]\{\},#&*!|>%@`][^:]*?):(?:\s+(.*)|\s*)$")


class DataLoadError(ValueError):
    """Raised when a structured catalog or data document cannot be parsed."""


def sha256_normalized_bytes(raw: bytes) -> str:
    """Return lowercase SHA-256 hex digest after normalizing CRLF line endings."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def pack_catalog_document(document: dict, *, source_sha256: str | None = None) -> dict:
    """Encode a mapping whose list-of-dict sections become compact schema-indexed tables."""
    if not isinstance(document, dict):
        raise DataLoadError("packed document root must be a mapping")
    packed: dict = {FORMAT_KEY: COMPILED_FORMAT}
    if source_sha256 is not None:
        packed[SOURCE_SHA_KEY] = source_sha256
    for key, value in document.items():
        if key in (FORMAT_KEY, SOURCE_SHA_KEY):
            continue
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            schemas: list[list[str]] = []
            schema_index: dict[tuple[str, ...], int] = {}
            rows: list[list] = []
            for row in value:
                keys = tuple(row.keys())
                if not all(isinstance(field, str) for field in keys):
                    raise DataLoadError(f"non-string key in packed section: {key}")
                sid = schema_index.get(keys)
                if sid is None:
                    sid = len(schemas)
                    schema_index[keys] = sid
                    schemas.append(list(keys))
                rows.append([sid, *(row[field] for field in keys)])
            packed[key] = {"schemas": schemas, "rows": rows}
        else:
            packed[key] = value
    return packed


def unpack_catalog_document(document: object) -> object:
    """Restore a packed pcba-table-v1 mapping to its exact uncompacted dict structure."""
    if not isinstance(document, dict):
        return document
    if document.get(FORMAT_KEY) != COMPILED_FORMAT:
        if SOURCE_SHA_KEY in document or FORMAT_KEY in document:
            return {
                key: value
                for key, value in document.items()
                if key not in (FORMAT_KEY, SOURCE_SHA_KEY)
            }
        return document

    unpacked: dict = {}
    for key, value in document.items():
        if key in (FORMAT_KEY, SOURCE_SHA_KEY):
            continue
        if isinstance(value, dict) and set(value.keys()) == {"schemas", "rows"}:
            schemas = value["schemas"]
            rows = value["rows"]
            if not isinstance(schemas, list) or not isinstance(rows, list):
                raise DataLoadError(f"invalid packed section shape for {key}")
            rebuilt: list[dict] = []
            for row in rows:
                if not isinstance(row, list) or not row:
                    raise DataLoadError(f"invalid packed row in {key}")
                sid = row[0]
                if isinstance(sid, bool) or not isinstance(sid, int) or sid < 0 or sid >= len(schemas):
                    raise DataLoadError(f"invalid schema index in {key}: {sid}")
                schema = schemas[sid]
                if not isinstance(schema, list) or len(row) - 1 != len(schema):
                    raise DataLoadError(f"row width mismatch in {key}")
                rebuilt.append(dict(zip(schema, row[1:])))
            unpacked[key] = rebuilt
        else:
            unpacked[key] = value
    return unpacked


def _strip_inline_comment(text: str) -> str:
    """Strip an unquoted YAML `# comment` outside single/double quotes."""
    in_single = False
    in_double = False
    escaped = False
    idx = 0
    length = len(text)
    while idx < length:
        ch = text[idx]
        if in_single:
            if ch == "'":
                if idx + 1 < length and text[idx + 1] == "'":
                    idx += 2
                    continue
                in_single = False
        elif in_double:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_double = False
        else:
            if ch == "'":
                in_single = True
            elif ch == '"':
                in_double = True
            elif ch == "#" and (idx == 0 or text[idx - 1].isspace()):
                return text[:idx].rstrip()
        idx += 1
    return text.rstrip()


def _unescape_double_quoted(raw: str) -> str:
    """Decode YAML double-quoted string escapes."""
    out: list[str] = []
    idx = 0
    length = len(raw)
    simple = {
        "0": "\0",
        "a": "\a",
        "b": "\b",
        "t": "\t",
        "\t": "\t",
        "n": "\n",
        "v": "\v",
        "f": "\f",
        "r": "\r",
        "e": "\x1b",
        " ": " ",
        '"': '"',
        "/": "/",
        "\\": "\\",
        "N": "\x85",
        "_": "\xa0",
        "L": "\u2028",
        "P": "\u2029",
    }
    while idx < length:
        ch = raw[idx]
        if ch != "\\":
            out.append(ch)
            idx += 1
            continue
        idx += 1
        if idx >= length:
            raise DataLoadError("unterminated escape in double-quoted string")
        esc = raw[idx]
        idx += 1
        if esc == "\n":
            while idx < length and raw[idx] in " \t":
                idx += 1
            continue
        if esc in simple:
            out.append(simple[esc])
        elif esc == "x":
            if idx + 2 > length:
                raise DataLoadError("invalid \\x escape in double-quoted string")
            out.append(chr(int(raw[idx : idx + 2], 16)))
            idx += 2
        elif esc == "u":
            if idx + 4 > length:
                raise DataLoadError("invalid \\u escape in double-quoted string")
            out.append(chr(int(raw[idx : idx + 4], 16)))
            idx += 4
        elif esc == "U":
            if idx + 8 > length:
                raise DataLoadError("invalid \\U escape in double-quoted string")
            out.append(chr(int(raw[idx : idx + 8], 16)))
            idx += 8
        else:
            raise DataLoadError(f"unsupported escape \\{esc} in double-quoted string")
    return "".join(out)


def _decode_plain_scalar(token: str) -> object:
    """Convert an unquoted YAML 1.1 plain scalar to Python primitives."""
    text = token.strip()
    if text in _YAML_NULL:
        return None
    if text in _YAML_BOOL_TRUE:
        return True
    if text in _YAML_BOOL_FALSE:
        return False
    if text in _YAML_NAN:
        return float("nan")
    if text in _YAML_POS_INF:
        return float("inf")
    if text in _YAML_NEG_INF:
        return float("-inf")
    if _INT_DECIMAL_RE.fullmatch(text):
        return int(text.replace("_", ""))
    if _INT_OCTAL_LEGACY_RE.fullmatch(text):
        sign = -1 if text.startswith("-") else 1
        digits = text.lstrip("+-").replace("_", "")
        return sign * int(digits, 8)
    if _INT_OCTAL_NEW_RE.fullmatch(text):
        return int(text.replace("_", ""), 8)
    if _INT_HEX_RE.fullmatch(text):
        return int(text.replace("_", ""), 16)
    if _FLOAT_RE.fullmatch(text):
        try:
            return float(text.replace("_", ""))
        except ValueError:
            return text
    return text


def _fold_quoted_lines(raw_lines: list[str]) -> str:
    """Fold multi-line quoted scalar fragments per YAML folding rules."""
    if len(raw_lines) == 1:
        return raw_lines[0]
    parts: list[str] = [raw_lines[0].rstrip(" \t")]
    blank_run = 0
    for segment in raw_lines[1:]:
        stripped = segment.strip(" \t")
        if not stripped:
            blank_run += 1
            continue
        if blank_run:
            parts.append("\n" * blank_run + stripped)
            blank_run = 0
        else:
            parts.append(" " + stripped)
    return "".join(parts)


class _StdlibYamlParser:
    """Recursive-descent parser for YAML 1.1/1.2 block & flow subset used in catalogs."""

    def __init__(self, text: str):
        normalized = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
        self.lines = normalized.split("\n")
        self.n = len(self.lines)
        self.idx = 0
        self.anchors: dict[str, object] = {}

    def _indent_of(self, line: str) -> int:
        count = 0
        for ch in line:
            if ch == " ":
                count += 1
            elif ch == "\t":
                raise DataLoadError("tabs are not permitted in YAML indentation")
            else:
                break
        return count

    def _is_blank_or_comment(self, line: str) -> bool:
        stripped = line.lstrip(" ")
        if stripped.startswith("\t"):
            raise DataLoadError("tabs are not permitted in YAML indentation")
        return not stripped or stripped.startswith("#")

    def _skip_ignorable(self) -> None:
        while self.idx < self.n:
            line = self.lines[self.idx]
            if self._is_blank_or_comment(line):
                self.idx += 1
                continue
            break

    def _peek_content(self) -> tuple[int, str] | None:
        self._skip_ignorable()
        if self.idx >= self.n:
            return None
        line = self.lines[self.idx]
        if line in ("...", "---") or re.match(r"^(?:---|\.\.\.)(?:\s|$)", line):
            return None
        return self._indent_of(line), line[self._indent_of(line) :]

    def parse(self) -> object:
        self._skip_ignorable()
        if self.idx < self.n and re.match(r"^---(?:\s+(.*)|\s*)$", self.lines[self.idx]):
            header = _strip_inline_comment(self.lines[self.idx][3:].strip())
            self.idx += 1
            if header:
                return self._parse_inline_expr(header)
        self._skip_ignorable()
        if self.idx >= self.n:
            return None
        peek = self._peek_content()
        if peek is None:
            return None
        indent, content = peek
        value = self._parse_block_node(indent)
        self._skip_ignorable()
        if self.idx < self.n and re.match(r"^\.\.\.(?:\s|#|$)", self.lines[self.idx]):
            self.idx += 1
        self._skip_ignorable()
        if self.idx < self.n:
            if re.match(r"^---(?:\s|#|$)", self.lines[self.idx]):
                raise DataLoadError("multi-document YAML streams are not supported")
            raise DataLoadError(f"unexpected trailing content at line {self.idx + 1}")
        return value

    def _consume_quoted_block(self, first_rest: str, quote: str) -> tuple[str, str]:
        """Read a single- or double-quoted scalar starting at first_rest[0], returning (value, trailing)."""
        raw_parts: list[str] = []
        buf: list[str] = []
        pos = 1
        line_text = first_rest
        while True:
            length = len(line_text)
            while pos < length:
                ch = line_text[pos]
                if quote == "'":
                    if ch == "'":
                        if pos + 1 < length and line_text[pos + 1] == "'":
                            buf.append("'")
                            pos += 2
                            continue
                        raw_parts.append("".join(buf))
                        return _fold_quoted_lines(raw_parts), line_text[pos + 1 :]
                    buf.append(ch)
                    pos += 1
                else:
                    if ch == "\\":
                        if pos + 1 == length:
                            # Escaped newline inside double-quoted string joins next line directly.
                            buf.append("\\\n")
                            pos += 1
                            break
                        buf.append(ch)
                        buf.append(line_text[pos + 1])
                        pos += 2
                        continue
                    if ch == '"':
                        raw_parts.append("".join(buf))
                        folded = _fold_quoted_lines(raw_parts)
                        return _unescape_double_quoted(folded), line_text[pos + 1 :]
                    buf.append(ch)
                    pos += 1
            raw_parts.append("".join(buf))
            buf.clear()
            self.idx += 1
            if self.idx >= self.n:
                raise DataLoadError("unterminated quoted string in YAML")
            line_text = self.lines[self.idx]
            pos = 0

    def _split_mapping_entry(self, content: str) -> tuple[str, str] | None:
        """If content begins a mapping key-value entry, return (key_str, rest_after_colon)."""
        if content.startswith("'"):
            # Quoted single key on one line.
            pos = 1
            buf: list[str] = []
            while pos < len(content):
                ch = content[pos]
                if ch == "'":
                    if pos + 1 < len(content) and content[pos + 1] == "'":
                        buf.append("'")
                        pos += 2
                        continue
                    after = content[pos + 1 :].lstrip(" ")
                    if after.startswith(":") and (len(after) == 1 or after[1].isspace()):
                        return "".join(buf), after[1:].lstrip(" ")
                    return None
                buf.append(ch)
                pos += 1
            return None
        if content.startswith('"'):
            pos = 1
            buf = []
            while pos < len(content):
                ch = content[pos]
                if ch == "\\" and pos + 1 < len(content):
                    buf.append(ch)
                    buf.append(content[pos + 1])
                    pos += 2
                    continue
                if ch == '"':
                    after = content[pos + 1 :].lstrip(" ")
                    if after.startswith(":") and (len(after) == 1 or after[1].isspace()):
                        return _unescape_double_quoted("".join(buf)), after[1:].lstrip(" ")
                    return None
                buf.append(ch)
                pos += 1
            return None
        match = _UNQUOTED_KEY_RE.match(content)
        if not match:
            return None
        return match.group(1).strip(), (match.group(2) or "")

    def _is_sequence_line(self, content: str) -> bool:
        return content == "-" or content.startswith("- ")

    def _parse_block_node(self, min_indent: int) -> object:
        peek = self._peek_content()
        if peek is None:
            return None
        indent, content = peek
        if indent < min_indent:
            return None
        if self._is_sequence_line(content):
            return self._parse_sequence(indent)
        if self._split_mapping_entry(content) is not None:
            return self._parse_mapping(indent)
        return self._parse_block_scalar_or_flow(indent)

    def _parse_block_scalar_or_flow(self, base_indent: int) -> object:
        line = self.lines[self.idx]
        content = line[self._indent_of(line) :]
        anchor_name = None
        anchor_match = _ANCHOR_RE.match(content)
        if anchor_match and not content.startswith(("'", '"')):
            anchor_name = anchor_match.group(1)
            rest = (anchor_match.group(2) or "").strip()
            if not rest or rest.startswith("#"):
                self.idx += 1
                value = self._parse_block_node(base_indent)
                self.anchors[anchor_name] = value
                return value
            content = rest
        value = self._parse_value_from_line(content, base_indent, allow_multiline_plain=True)
        if anchor_name is not None:
            self.anchors[anchor_name] = value
        return value

    def _parse_sequence(self, seq_indent: int) -> list:
        items: list = []
        while True:
            peek = self._peek_content()
            if peek is None:
                break
            indent, content = peek
            if indent < seq_indent:
                break
            if indent > seq_indent:
                raise DataLoadError(f"unexpected indentation in sequence at line {self.idx + 1}")
            if not self._is_sequence_line(content):
                break
            after_dash = content[1:]
            spaces = len(after_dash) - len(after_dash.lstrip(" "))
            rest = after_dash.lstrip(" ")
            item_col = seq_indent + 1 + spaces

            anchor_name = None
            if rest.startswith("&"):
                anchor_match = _ANCHOR_RE.match(rest)
                if anchor_match:
                    anchor_name = anchor_match.group(1)
                    rest = (anchor_match.group(2) or "").strip()

            stripped_rest = _strip_inline_comment(rest)
            if not stripped_rest:
                self.idx += 1
                next_peek = self._peek_content()
                if next_peek is None or next_peek[0] <= seq_indent:
                    value = None
                else:
                    value = self._parse_block_node(seq_indent + 1)
            elif self._is_sequence_line(rest):
                # Compact nested sequence: `- - item`
                value = self._parse_compact_nested_sequence(seq_indent, item_col, rest)
            elif self._split_mapping_entry(rest) is not None:
                # Compact block mapping: `- key: value`
                entry = self._split_mapping_entry(rest)
                assert entry is not None
                first_key, first_rest = entry
                value = self._parse_mapping(item_col, initial_entry=(first_key, first_rest))
            else:
                value = self._parse_value_from_line(rest, seq_indent, allow_multiline_plain=True)

            if anchor_name is not None:
                self.anchors[anchor_name] = value
            items.append(value)
        return items

    def _parse_compact_nested_sequence(self, outer_indent: int, inner_indent: int, first_rest: str) -> list:
        # Rewrite virtual current line tail starting at inner_indent and parse sequence cleanly.
        saved_idx = self.idx
        saved_line = self.lines[saved_idx]
        self.lines[saved_idx] = (" " * inner_indent) + first_rest
        try:
            return self._parse_sequence(inner_indent)
        finally:
            self.lines[saved_idx] = saved_line

    def _parse_mapping(self, map_indent: int, initial_entry: tuple[str, str] | None = None) -> dict:
        mapping: dict = {}
        first = initial_entry
        while True:
            if first is not None:
                key, rest = first
                first = None
            else:
                peek = self._peek_content()
                if peek is None:
                    break
                indent, content = peek
                if indent < map_indent:
                    break
                if indent > map_indent:
                    raise DataLoadError(f"unexpected indentation in mapping at line {self.idx + 1}")
                if self._is_sequence_line(content):
                    break
                entry = self._split_mapping_entry(content)
                if entry is None:
                    raise DataLoadError(f"expected mapping entry at line {self.idx + 1}: {content}")
                key, rest = entry

            value = self._parse_mapping_value(map_indent, rest)
            mapping[key] = value
        return mapping

    def _parse_mapping_value(self, key_indent: int, rest: str) -> object:
        rest_stripped = rest.lstrip(" ")
        anchor_name = None
        if rest_stripped.startswith("&"):
            anchor_match = _ANCHOR_RE.match(rest_stripped)
            if anchor_match:
                anchor_name = anchor_match.group(1)
                rest_stripped = (anchor_match.group(2) or "").lstrip(" ")

        no_comment = _strip_inline_comment(rest_stripped)
        if not no_comment:
            self.idx += 1
            next_peek = self._peek_content()
            if next_peek is None:
                value = None
            else:
                next_indent, next_content = next_peek
                if next_indent > key_indent:
                    value = self._parse_block_node(key_indent + 1)
                elif next_indent == key_indent and self._is_sequence_line(next_content):
                    # PyYAML dumps `key:\n- item` at the same indent as `key`.
                    value = self._parse_sequence(next_indent)
                else:
                    value = None
        else:
            value = self._parse_value_from_line(rest_stripped, key_indent, allow_multiline_plain=True)

        if anchor_name is not None:
            self.anchors[anchor_name] = value
        return value

    def _parse_value_from_line(self, rest: str, parent_indent: int, *, allow_multiline_plain: bool) -> object:
        stripped = rest.lstrip(" ")
        if stripped.startswith("*"):
            alias_match = _ALIAS_RE.match(_strip_inline_comment(stripped))
            if alias_match:
                self.idx += 1
                name = alias_match.group(1)
                if name not in self.anchors:
                    raise DataLoadError(f"undefined YAML alias: *{name}")
                return copy.deepcopy(self.anchors[name])

        if stripped.startswith(("'", '"')):
            quote = stripped[0]
            text, trailing = self._consume_quoted_block(stripped, quote)
            if _strip_inline_comment(trailing.strip()):
                raise DataLoadError(f"unexpected trailing characters after quoted string at line {self.idx + 1}")
            self.idx += 1
            return text

        if stripped.startswith(("[", "{")):
            return self._parse_multiline_flow(stripped)

        if stripped in ("|", "|-", "|+", ">", ">-", ">+") or re.match(r"^[|>][+-]?(?:\s+#.*)?$", stripped):
            return self._parse_block_text_scalar(stripped[0], stripped, parent_indent)

        return self._parse_plain_scalar_lines(stripped, parent_indent, allow_multiline_plain)

    def _parse_block_text_scalar(self, style: str, header: str, parent_indent: int) -> str:
        chomp = "clip"
        if "-" in header.split("#", 1)[0]:
            chomp = "strip"
        elif "+" in header.split("#", 1)[0]:
            chomp = "keep"
        self.idx += 1
        block_indent = None
        collected: list[str] = []
        while self.idx < self.n:
            raw = self.lines[self.idx]
            if not raw.strip(" "):
                collected.append("")
                self.idx += 1
                continue
            ind = self._indent_of(raw)
            if block_indent is None:
                if ind <= parent_indent:
                    break
                block_indent = ind
            elif ind < block_indent:
                break
            collected.append(raw[block_indent:])
            self.idx += 1
        while collected and collected[-1] == "" and chomp != "keep":
            collected.pop()
        if not collected:
            return ""
        if style == "|":
            body = "\n".join(collected)
        else:
            # Folded scalar: single blank line -> newline, adjacent non-blank -> space.
            chunks: list[str] = []
            for idx_line, line in enumerate(collected):
                if idx_line == 0:
                    chunks.append(line)
                elif line == "":
                    chunks.append("\n")
                elif collected[idx_line - 1] == "":
                    chunks.append(line)
                else:
                    chunks.append(" " + line)
            body = "".join(chunks)
        return body if chomp == "strip" else body + "\n"

    def _parse_plain_scalar_lines(self, first_part: str, parent_indent: int, allow_multiline: bool) -> object:
        head = _strip_inline_comment(first_part)
        self.idx += 1
        if not allow_multiline:
            return _decode_plain_scalar(head)

        parts = [head]
        blank_run = 0
        while self.idx < self.n:
            raw = self.lines[self.idx]
            if not raw.strip(" "):
                blank_run += 1
                self.idx += 1
                continue
            ind = self._indent_of(raw)
            content = raw[ind:]
            if content.startswith("#"):
                break
            if ind <= parent_indent:
                break
            if content in ("---", "..."):
                break
            if self._is_sequence_line(content) or self._split_mapping_entry(content) is not None:
                break
            piece = _strip_inline_comment(content)
            if blank_run:
                parts.append("\n" * blank_run + piece)
                blank_run = 0
            else:
                parts.append(" " + piece)
            self.idx += 1
        if blank_run:
            # Do not advance past trailing blank lines unnecessarily (already skipped, harmless).
            pass
        combined = "".join(parts)
        return _decode_plain_scalar(combined)

    def _parse_multiline_flow(self, first_fragment: str) -> object:
        chunks = [first_fragment]
        self.idx += 1
        while True:
            joined = " ".join(chunks)
            try:
                value, end_pos = _FlowParser(joined, self.anchors).parse_value(0)
            except _IncompleteFlowError:
                if self.idx >= self.n:
                    raise DataLoadError("unterminated YAML flow collection")
                chunks.append(self.lines[self.idx].strip())
                self.idx += 1
                continue
            trailing = _strip_inline_comment(joined[end_pos:].strip())
            if trailing:
                raise DataLoadError(f"unexpected trailing characters after flow collection: {trailing}")
            return value

    def _parse_inline_expr(self, expr: str) -> object:
        stripped = expr.strip()
        if stripped.startswith(("[", "{")):
            value, end_pos = _FlowParser(stripped, self.anchors).parse_value(0)
            trailing = _strip_inline_comment(stripped[end_pos:].strip())
            if trailing:
                raise DataLoadError("unexpected trailing characters in flow expression")
            return value
        if stripped.startswith(("'", '"')):
            quote = stripped[0]
            text, trailing = self._consume_quoted_block(stripped, quote)
            if _strip_inline_comment(trailing.strip()):
                raise DataLoadError("unexpected trailing characters after quoted scalar")
            return text
        return _decode_plain_scalar(_strip_inline_comment(stripped))


class _IncompleteFlowError(Exception):
    """Signals a flow collection needs additional physical lines."""


class _FlowParser:
    """Parser for YAML flow sequences `[...]` and flow mappings `{...}`."""

    def __init__(self, text: str, anchors: dict[str, object]):
        self.text = text
        self.n = len(text)
        self.anchors = anchors

    def _skip_ws(self, pos: int) -> int:
        while pos < self.n:
            if self.text[pos].isspace():
                pos += 1
            elif self.text[pos] == "#" and (pos == 0 or self.text[pos - 1].isspace()):
                nl = self.text.find("\n", pos)
                pos = self.n if nl == -1 else nl + 1
            else:
                break
        return pos

    def parse_value(self, pos: int, *, stop_on_colon: bool = False) -> tuple[object, int]:
        pos = self._skip_ws(pos)
        if pos >= self.n:
            raise _IncompleteFlowError()

        anchor_name = None
        if self.text[pos] == "&":
            m = re.match(r"&([A-Za-z0-9_-]+)", self.text[pos:])
            if not m:
                raise DataLoadError("invalid YAML anchor in flow collection")
            anchor_name = m.group(1)
            pos = self._skip_ws(pos + len(m.group(0)))
            if pos >= self.n:
                raise _IncompleteFlowError()

        ch = self.text[pos]
        if ch == "*":
            m = re.match(r"\*([A-Za-z0-9_-]+)", self.text[pos:])
            if not m:
                raise DataLoadError("invalid YAML alias in flow collection")
            name = m.group(1)
            if name not in self.anchors:
                raise DataLoadError(f"undefined YAML alias: *{name}")
            value = copy.deepcopy(self.anchors[name])
            end_pos = pos + len(m.group(0))
        elif ch == "[":
            value, end_pos = self._parse_seq(pos)
        elif ch == "{":
            value, end_pos = self._parse_map(pos)
        elif ch == "'":
            value, end_pos = self._parse_single_quoted(pos)
        elif ch == '"':
            value, end_pos = self._parse_double_quoted(pos)
        else:
            value, end_pos = self._parse_plain(pos, stop_on_colon=stop_on_colon)

        if anchor_name is not None:
            self.anchors[anchor_name] = value
        return value, end_pos

    def _parse_seq(self, pos: int) -> tuple[list, int]:
        pos += 1  # skip '['
        items: list = []
        while True:
            pos = self._skip_ws(pos)
            if pos >= self.n:
                raise _IncompleteFlowError()
            if self.text[pos] == "]":
                return items, pos + 1
            val, pos = self.parse_value(pos)
            items.append(val)
            pos = self._skip_ws(pos)
            if pos >= self.n:
                raise _IncompleteFlowError()
            if self.text[pos] == ",":
                pos += 1
                continue
            if self.text[pos] == "]":
                return items, pos + 1
            raise DataLoadError(f"expected ',' or ']' in flow sequence near position {pos}")

    def _parse_map(self, pos: int) -> tuple[dict, int]:
        pos += 1  # skip '{'
        result: dict = {}
        while True:
            pos = self._skip_ws(pos)
            if pos >= self.n:
                raise _IncompleteFlowError()
            if self.text[pos] == "}":
                return result, pos + 1
            key, pos = self.parse_value(pos, stop_on_colon=True)
            pos = self._skip_ws(pos)
            if pos >= self.n:
                raise _IncompleteFlowError()
            if self.text[pos] != ":":
                raise DataLoadError(f"expected ':' in flow mapping near position {pos}")
            pos += 1
            val, pos = self.parse_value(pos)
            result[key] = val
            pos = self._skip_ws(pos)
            if pos >= self.n:
                raise _IncompleteFlowError()
            if self.text[pos] == ",":
                pos += 1
                continue
            if self.text[pos] == "}":
                return result, pos + 1
            raise DataLoadError(f"expected ',' or '}}' in flow mapping near position {pos}")

    def _parse_single_quoted(self, pos: int) -> tuple[str, int]:
        pos += 1
        buf: list[str] = []
        while pos < self.n:
            ch = self.text[pos]
            if ch == "'":
                if pos + 1 < self.n and self.text[pos + 1] == "'":
                    buf.append("'")
                    pos += 2
                    continue
                return "".join(buf), pos + 1
            buf.append(ch)
            pos += 1
        raise _IncompleteFlowError()

    def _parse_double_quoted(self, pos: int) -> tuple[str, int]:
        pos += 1
        buf: list[str] = []
        while pos < self.n:
            ch = self.text[pos]
            if ch == "\\":
                if pos + 1 >= self.n:
                    raise _IncompleteFlowError()
                buf.append(ch)
                buf.append(self.text[pos + 1])
                pos += 2
                continue
            if ch == '"':
                return _unescape_double_quoted("".join(buf)), pos + 1
            buf.append(ch)
            pos += 1
        raise _IncompleteFlowError()

    def _parse_plain(self, pos: int, *, stop_on_colon: bool) -> tuple[object, int]:
        start = pos
        while pos < self.n:
            ch = self.text[pos]
            if ch in ",]}":
                break
            if ch == "#" and pos > start and self.text[pos - 1].isspace():
                break
            if stop_on_colon and ch == ":" and (pos + 1 >= self.n or self.text[pos + 1].isspace() or self.text[pos + 1] in ",]}"):
                break
            pos += 1
        raw = self.text[start:pos].strip()
        return _decode_plain_scalar(raw), pos


def parse_yaml_stdlib(text: str) -> object:
    """Parse YAML subset with pure Python standard library (zero third-party dependencies)."""
    return _StdlibYamlParser(text).parse()


def load_structured_text(text: str, *, default=None, prefer_stdlib_yaml: bool = False) -> object:
    """Load JSON (including packed pcba-table-v1) or YAML text, returning default when empty."""
    cleaned = text.lstrip("\ufeff")
    stripped = cleaned.strip()
    if not stripped:
        return default
    if stripped[0] in ("{", "["):
        try:
            loaded = json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        else:
            unpacked = unpack_catalog_document(loaded)
            return default if unpacked is None else unpacked

    if yaml is not None and not prefer_stdlib_yaml:
        try:
            parsed = yaml.safe_load(cleaned)
        except yaml.YAMLError as exc:
            raise DataLoadError(f"invalid YAML document: {exc}") from exc
    else:
        parsed = parse_yaml_stdlib(cleaned)

    return default if parsed is None else parsed


def load_structured_bytes(payload: bytes, *, default=None, prefer_stdlib_yaml: bool = False) -> object:
    """Decode UTF-8 bytes and parse JSON or YAML."""
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DataLoadError(f"catalog was not valid UTF-8: {exc}") from exc
    return load_structured_text(text, default=default, prefer_stdlib_yaml=prefer_stdlib_yaml)


def resolve_data_paths(data_dir: Path | str, stem: str) -> tuple[Path | None, Path | None]:
    """Return (compiled_json_path, source_yaml_or_txt_path) if present."""
    root = Path(data_dir)
    json_candidate = root / f"{stem}{JSON_SUFFIX}"
    json_path = json_candidate if json_candidate.is_file() else None
    source_path = next(
        (
            root / f"{stem}{suffix}"
            for suffix in (YAML_SUFFIX, TEXT_SUFFIX)
            if (root / f"{stem}{suffix}").is_file()
        ),
        None,
    )
    return json_path, source_path


def verify_compiled_sync(data_dir: Path | str, stem: str) -> str | None:
    """Verify compiled JSON matches sibling YAML/TXT source hash when both exist."""
    json_path, source_path = resolve_data_paths(data_dir, stem)
    if json_path is None or source_path is None:
        return None
    try:
        raw_json = json_path.read_bytes().decode("utf-8")
        document = json.loads(raw_json)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return f"{json_path.name}: invalid compiled JSON ({exc})"
    if not isinstance(document, dict):
        return f"{json_path.name}: compiled root must be a JSON object"
    recorded_sha = document.get(SOURCE_SHA_KEY)
    if not isinstance(recorded_sha, str) or not recorded_sha:
        return f"{json_path.name}: missing {SOURCE_SHA_KEY} metadata"
    actual_sha = sha256_normalized_bytes(source_path.read_bytes())
    if recorded_sha != actual_sha:
        return (
            f"{json_path.name}: out of sync with {source_path.name} "
            f"(recorded={recorded_sha[:12]}, actual={actual_sha[:12]})"
        )
    return None


def load_data_document(data_dir: Path | str, stem: str, *, allow_missing: bool = False) -> dict:
    """Load data/<stem> preferring fresh compiled JSON with automatic source fallback."""
    root = Path(data_dir)
    json_path, source_path = resolve_data_paths(root, stem)
    if json_path is not None:
        try:
            parsed = json.loads(json_path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            if source_path is None:
                raise DataLoadError(f"unable to read {json_path.name}: {exc}") from exc
            parsed = None
        if isinstance(parsed, dict):
            if source_path is None:
                unpacked = unpack_catalog_document(parsed)
                if not isinstance(unpacked, dict):
                    raise DataLoadError(f"data root must be a mapping: {json_path.name}")
                return unpacked
            recorded_sha = parsed.get(SOURCE_SHA_KEY)
            if isinstance(recorded_sha, str) and recorded_sha == sha256_normalized_bytes(source_path.read_bytes()):
                unpacked = unpack_catalog_document(parsed)
                if not isinstance(unpacked, dict):
                    raise DataLoadError(f"data root must be a mapping: {json_path.name}")
                return unpacked
        elif source_path is None:
            raise DataLoadError(f"data root must be a mapping: {json_path.name}")

    if source_path is not None:
        loaded = load_structured_bytes(source_path.read_bytes(), default={})
        if not isinstance(loaded, dict):
            raise DataLoadError(f"data root must be a mapping: {source_path.name}")
        return loaded

    if allow_missing:
        return {}
    raise FileNotFoundError(f"{stem} data file not found under {root}")
