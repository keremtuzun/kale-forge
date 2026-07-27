"""S-expression tokenizer + parser for KiCad files.

A character-level scanner (not regex splitting): handles quoted strings with escapes,
embedded newlines, symbols, and numbers. Parse result is a nested Python list; atoms are
str / int / float. The first element of each list is conventionally the tag symbol.
"""
from __future__ import annotations

from typing import Any, Iterator, Optional

SExpr = list  # nested lists of atoms


class SExprError(ValueError):
    def __init__(self, message: str, line: int, column: int):
        super().__init__(f"{message} at line {line}, column {column}")
        self.line = line
        self.column = column


_WHITESPACE = " \t\r\n"


def _tokenize(text: str) -> Iterator[tuple[str, Any, int, int]]:
    """Yields (kind, value, line, col); kind in {'(', ')', 'atom'}."""
    i, n = 0, len(text)
    line, col = 1, 1

    def advance(ch: str) -> None:
        nonlocal line, col
        if ch == "\n":
            line += 1
            col = 1
        else:
            col += 1

    while i < n:
        ch = text[i]
        if ch in _WHITESPACE:
            advance(ch)
            i += 1
            continue
        if ch == "(" or ch == ")":
            yield (ch, ch, line, col)
            advance(ch)
            i += 1
            continue
        if ch == '"':
            start_line, start_col = line, col
            advance(ch)
            i += 1
            buf: list[str] = []
            closed = False
            while i < n:
                ch = text[i]
                if ch == "\\" and i + 1 < n:
                    esc = text[i + 1]
                    buf.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\", "r": "\r"}.get(esc, esc))
                    advance(ch)
                    advance(esc)
                    i += 2
                    continue
                if ch == '"':
                    closed = True
                    advance(ch)
                    i += 1
                    break
                buf.append(ch)
                advance(ch)
                i += 1
            if not closed:
                raise SExprError("unterminated string", start_line, start_col)
            yield ("atom", "".join(buf), start_line, start_col)
            continue
        # bare symbol / number
        start_line, start_col = line, col
        buf = []
        while i < n and text[i] not in _WHITESPACE and text[i] not in '()"':
            buf.append(text[i])
            advance(text[i])
            i += 1
        yield ("atom", _coerce("".join(buf)), start_line, start_col)


def _coerce(token: str) -> Any:
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token


def parse_sexpr(text: str) -> SExpr:
    """Parse a single top-level S-expression (KiCad files have exactly one)."""
    stack: list[list] = []
    root: Optional[list] = None
    last_line, last_col = 1, 1
    for kind, value, line, col in _tokenize(text):
        last_line, last_col = line, col
        if kind == "(":
            new: list = []
            if stack:
                stack[-1].append(new)
            elif root is None:
                root = new
            else:
                raise SExprError("multiple top-level expressions", line, col)
            stack.append(new)
        elif kind == ")":
            if not stack:
                raise SExprError("unbalanced ')'", line, col)
            stack.pop()
        else:
            if not stack:
                raise SExprError("atom outside expression", line, col)
            stack[-1].append(value)
    if stack:
        raise SExprError("unclosed '('", last_line, last_col)
    if root is None:
        raise SExprError("empty input", 1, 1)
    return root


# --- tree helpers --------------------------------------------------------------------------


def is_node(item: Any, tag: str | None = None) -> bool:
    return isinstance(item, list) and bool(item) and (tag is None or item[0] == tag)


def find_all(node: SExpr, tag: str) -> list[SExpr]:
    """Direct children of `node` that are lists starting with `tag`."""
    return [child for child in node if is_node(child, tag)]


def find_first(node: SExpr, tag: str) -> Optional[SExpr]:
    for child in node:
        if is_node(child, tag):
            return child
    return None


def atoms(node: SExpr) -> list[Any]:
    """Non-list items after the tag."""
    return [item for item in node[1:] if not isinstance(item, list)]


def first_atom(node: Optional[SExpr], default: Any = None) -> Any:
    if node is None:
        return default
    for item in node[1:]:
        if not isinstance(item, list):
            return item
    return default
