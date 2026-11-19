"""The text form of a plan. A small, readable language with exact error positions, and a canonical printer.

    plan "dispute-desk" {
      step ingest { cost 0.1  latency 2  outputs [raw] }
      step classify after ingest { model small  cost 0.2  latency 1  inputs [raw] }
      step refund after classify { risk 4  approval  retries 2  p_fail 0.05  idempotent false }
    }

Attributes are separated by whitespace, newlines or semicolons. ``#`` and ``//`` start comments. Flags such as
``approval`` and ``final`` need no value. Printing a parsed plan and parsing it again gives the same plan.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .model import _FIELDS, Plan, SpecError, StepSpec, load

_TOKEN = re.compile(
    r"""(?P<ws>[ \t\r\n]+|\#[^\n]*|//[^\n]*)
       |(?P<num>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?![\w.\-])
       |(?P<str>"(?:[^"\\\n]|\\.)*")
       |(?P<id>[A-Za-z_][\w.\-]*)
       |(?P<punct>[{}\[\],;])""",
    re.VERBOSE,
)

# attribute name -> kind
_ATTRS = {
    "model": "word", "cost": "num", "latency": "num", "approval": "flag", "risk": "num", "retries": "num", "p_fail": "num",
    "idempotent": "bool", "inputs": "list", "outputs": "list", "tags": "list", "final": "flag",
}


class ParseError(SpecError):
    def __init__(self, message: str, line: int, col: int, source: str) -> None:
        lines = source.splitlines() or [""]
        snippet = lines[line - 1] if 0 < line <= len(lines) else ""
        super().__init__(f"line {line}, column {col}: {message}\n    {snippet}\n    {' ' * (col - 1)}^")
        self.line, self.col = line, col


@dataclass
class _Tok:
    kind: str
    text: str
    line: int
    col: int


def _lex(source: str) -> list[_Tok]:
    toks: list[_Tok] = []
    pos, line, line_start = 0, 1, 0
    while pos < len(source):
        m = _TOKEN.match(source, pos)
        if m is None:
            raise ParseError(f"unexpected character {source[pos]!r}", line, pos - line_start + 1, source)
        kind = m.lastgroup or ""
        text = m.group(0)
        if kind != "ws":
            toks.append(_Tok(kind, text, line, pos - line_start + 1))
        newlines = text.count("\n")
        if newlines:
            line += newlines
            line_start = pos + text.rfind("\n") + 1
        pos = m.end()
    toks.append(_Tok("eof", "", line, pos - line_start + 1))
    return toks


class _Parser:
    def __init__(self, source: str) -> None:
        self.src, self.toks, self.i = source, _lex(source), 0

    @property
    def tok(self) -> _Tok:
        return self.toks[self.i]

    def fail(self, message: str, tok: _Tok | None = None) -> ParseError:
        t = tok or self.tok
        return ParseError(message, t.line, t.col, self.src)

    def take(self, kind: str, text: str | None = None, what: str = "") -> _Tok:
        t = self.tok
        if t.kind != kind or (text is not None and t.text != text):
            raise self.fail(f"expected {what or text or kind}, found {t.text!r}" if t.kind != "eof" else f"expected {what or text or kind}, found end of input")
        self.i += 1
        return t

    def accept(self, kind: str, text: str | None = None) -> bool:
        if self.tok.kind == kind and (text is None or self.tok.text == text):
            self.i += 1
            return True
        return False

    def word(self, what: str) -> str:
        t = self.tok
        if t.kind == "str":
            self.i += 1
            return _unquote(t.text)
        if t.kind == "id":
            self.i += 1
            return t.text
        raise self.fail(f"expected {what}")

    def plan(self) -> Plan:
        name = ""
        self.take("id", "plan", "'plan'")
        if self.tok.kind == "str":
            name = _unquote(self.take("str").text)
        self.take("punct", "{", "'{'")
        items: list[dict[str, Any]] = []
        seen: dict[str, _Tok] = {}
        while not self.accept("punct", "}"):
            if self.tok.kind == "eof":
                raise self.fail("expected '}' to close the plan")
            start = self.tok
            self.take("id", "step", "'step' or '}'")
            sid_tok = self.tok
            sid = self.word("a step id")
            if sid in seen:
                raise self.fail(f"duplicate step id: {sid}", sid_tok)
            seen[sid] = start
            item: dict[str, Any] = {"id": sid}
            if self.tok.kind == "id" and self.tok.text == "after":
                self.i += 1
                item["depends_on"] = [self.word("a step id")]
                while self.accept("punct", ","):
                    item["depends_on"].append(self.word("a step id"))
            self.take("punct", "{", "'{'")
            while not self.accept("punct", "}"):
                if self.accept("punct", ";"):
                    continue
                self.attr(item)
            items.append(item)
        if self.tok.kind != "eof":
            raise self.fail("unexpected text after the plan")
        try:
            return load({"name": name, "steps": items})
        except SpecError as exc:
            message = str(exc)
            lead = message.replace(":", " ").split()[0] if message.split() else ""
            raise self.fail(message, seen.get(lead, self.toks[0])) from None

    def attr(self, item: dict[str, Any]) -> None:
        name_tok = self.tok
        name = self.take("id", what="an attribute name").text
        kind = _ATTRS.get(name)
        if kind is None:
            raise self.fail(f"unknown attribute {name!r}", name_tok)
        if name in item:
            raise self.fail(f"{name} is set twice", name_tok)
        if kind == "flag":
            if self.tok.kind == "id" and self.tok.text in ("true", "false"):
                item[name] = self.take("id").text == "true"
            else:
                item[name] = True
        elif kind == "bool":
            t = self.take("id", what="true or false")
            if t.text not in ("true", "false"):
                raise self.fail("expected true or false", t)
            item[name] = t.text == "true"
        elif kind == "num":
            t = self.take("num", what="a number")
            v = float(t.text)
            item[name] = int(v) if v == int(v) and "." not in t.text and "e" not in t.text.lower() else v
        elif kind == "word":
            item[name] = self.word("a name")
        else:
            self.take("punct", "[", "'['")
            vals: list[str] = []
            while not self.accept("punct", "]"):
                vals.append(self.word("a name"))
                if not self.accept("punct", ","):
                    self.take("punct", "]", "',' or ']'")
                    break
            item[name] = vals


def _unquote(text: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), text[1:-1])


def loads(source: str) -> Plan:
    """Parse plan text. Syntax errors and semantic errors (unknown dependency, cycle...) both raise ``ParseError``/``SpecError``."""
    return _Parser(source).plan()


# ------------------------------------------------------------------------------------------------- printing
_IDENT = re.compile(r"[A-Za-z_][\w.\-]*\Z")
_RESERVED = {"true", "false", "plan", "step", "after"}


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t") + '"'


def _word(s: str) -> str:
    return s if _IDENT.match(s) and s not in _RESERVED else _quote(s)


def _number(v: float) -> str:
    return str(int(v)) if float(v) == int(v) else repr(float(v))


def dumps(plan: Plan) -> str:
    """The canonical text of a plan: attributes in a fixed order, defaults omitted."""
    defaults = StepSpec("x")
    out = [f"plan {_quote(plan.name)} {{" if plan.name else "plan {"]
    for s in plan.steps.values():
        head = f"  step {_word(s.id)}"
        if s.depends_on:
            head += " after " + ", ".join(_word(d) for d in s.depends_on)
        parts: list[str] = []
        for name, kind in _ATTRS.items():
            v = getattr(s, name)
            if v == getattr(defaults, name):
                continue
            if kind == "flag":
                parts.append(name)
            elif kind == "bool":
                parts.append(f"{name} {'true' if v else 'false'}")
            elif kind == "num":
                parts.append(f"{name} {_number(v)}")
            elif kind == "word":
                parts.append(f"{name} {_word(v)}")
            else:
                parts.append(f"{name} [{', '.join(_word(x) for x in v)}]")
        out.append(f"{head} {{ {'  '.join(parts)} }}" if parts else f"{head} {{ }}")
    out.append("}")
    return "\n".join(out) + "\n"


assert set(_ATTRS) == _FIELDS - {"id", "depends_on"}, "the language must cover every step field"
