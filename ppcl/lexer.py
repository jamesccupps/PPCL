"""Tokenizer for PPCL source text.

PPCL is line-oriented: every statement begins with a line number, and the
remainder of the line is either a comment (introduced by ``C``) or a single
statement. This module turns the text after the line number into tokens.

The one genuinely awkward part of PPCL lexing is the dot. A dot can be:

* the decimal point of a number       -- ``80.0``
* part of a dotted operator           -- ``.EQ.`` ``.AND.`` ``.ROOT.``
* a literal character inside a name   -- ``"BUILDING1.AHU01.SFAN"``

We resolve this by matching dotted operators only against the closed set in
``spec.DOTTED_OPS``. Anything else beginning with a dot is either a number or
part of an identifier, which removes the ambiguity entirely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from . import spec


class Tok(str, Enum):
    """Token kinds produced by the lexer."""

    IDENT = "ident"  # bare point name, command, or keyword
    QUOTED = "quoted"  # "LONG.POINT.NAME" or "$LOCAL"
    NUMBER = "number"
    TIME = "time"  # 17:00
    DOTOP = "dotop"  # .EQ. .AND. .ROOT. ...
    PRIORITY = "priority"  # @EMER @NONE ...
    ATNAME = "atname"  # @1FAN - point name starting with a digit
    MACRO = "macro"  # %ABBREV% from a DEFINE
    LPAREN = "lparen"
    RPAREN = "rparen"
    COMMA = "comma"
    ASSIGN = "assign"
    OP = "op"  # + - * /
    AMP = "amp"  # & line continuation
    EOF = "eof"


@dataclass
class Token:
    """A single lexical token with its source column."""

    kind: Tok
    text: str
    col: int

    @property
    def upper(self) -> str:
        return self.text.upper()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Token(%s,%r,%d)" % (self.kind.value, self.text, self.col)


class LexError(Exception):
    """Raised when a character cannot begin any valid token."""

    def __init__(self, message: str, col: int):
        super().__init__(message)
        self.message = message
        self.col = col


# A time literal must be matched before a bare number so that "17:00" does not
# lex as NUMBER(17) COLON NUMBER(00).
_TIME_RE = re.compile(r"\d{1,2}:\d{2}")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?|\.\d+")
# A colon qualifies a point reference: ``Dev201:DAY_CLG_STPT`` names an FLN
# subpoint, and ``PROGRAM:name`` reaches another program's local. A time
# literal always begins with a digit, so it is matched before this and the
# two never collide.
_IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*(?::[A-Za-z0-9_$.]+)?")
_MACRO_RE = re.compile(r"%[A-Za-z0-9_.]+%")
_ATNAME_RE = re.compile(r"@[A-Za-z0-9_.]+")
# ``[NodeName]PointSystemName`` names a point on another PXC.A device on the
# same ALN -- A6V10374898 Ch.1, "BACnet Point Naming". The node name is a
# device name rather than an expression, so the whole thing lexes as one point
# reference. Nothing else in PPCL uses square brackets, so there is no
# ambiguity to resolve.
_NODEREF_RE = re.compile(
    r"\[[A-Za-z0-9_.$ -]+\][A-Za-z0-9_$]*(?::[A-Za-z0-9_$.]+)?"
)

# Longest-first so that ".NAND." is preferred over any shorter prefix.
_DOTOPS = sorted(spec.DOTTED_OPS, key=len, reverse=True)


def tokenize(text: str) -> list:
    """Tokenize the statement portion of a PPCL line.

    ``text`` must NOT include the leading line number; use
    :func:`ppcl.parser.split_line_number` first.
    """
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        if ch in " \t":
            i += 1
            continue

        # -- quoted point name or string literal ---------------------------
        if ch == '"':
            end = text.find('"', i + 1)
            if end == -1:
                raise LexError("unterminated quoted name", i)
            tokens.append(Token(Tok.QUOTED, text[i + 1 : end], i))
            i = end + 1
            continue

        # -- dotted operator ----------------------------------------------
        if ch == ".":
            upper_rest = text[i:].upper()
            for op in _DOTOPS:
                if upper_rest.startswith(op):
                    tokens.append(Token(Tok.DOTOP, op, i))
                    i += len(op)
                    break
            else:
                m = _NUMBER_RE.match(text, i)
                if m:
                    tokens.append(Token(Tok.NUMBER, m.group(), i))
                    i = m.end()
                else:
                    raise LexError("stray '.' - not a valid operator", i)
            continue

        # -- @priority, or a point name beginning with a digit -------------
        if ch == "@":
            m = _ATNAME_RE.match(text, i)
            if not m:
                raise LexError("'@' must be followed by a name", i)
            word = m.group()
            # The dot problem again, and this branch did not guard it:
            # @NONE.AND.$ARG3 lexed as one @-name plus $ARG3, so every
            # condition written without spaces around a dotted operator after
            # an @priority failed to parse. Siemens' own optimum-start-stop
            # programs are written that way throughout.
            for opword in _DOTOPS:
                cut = word.upper().find(opword)
                if cut > 0:
                    word = word[:cut]
                    break
            kind = Tok.PRIORITY if word.upper() in spec.PRIORITY_RANK else Tok.ATNAME
            tokens.append(Token(kind, word, i))
            i += len(word)
            continue

        # -- [Node]Point reference on another PXC.A device -----------------
        if ch == "[":
            m = _NODEREF_RE.match(text, i)
            if not m:
                raise LexError(
                    "'[' starts a [NodeName]PointName reference, which must "
                    "close its bracket and be followed by the point name", i
                )
            end = m.end()
            # A dotted system name may follow the bracket, the same way it may
            # follow a bare identifier.
            while end < n and text[end] == ".":
                rest = text[end:].upper()
                if any(rest.startswith(op) for op in _DOTOPS):
                    break
                seg = _IDENT_RE.match(text, end + 1)
                if not seg:
                    break
                end = seg.end()
            tokens.append(Token(Tok.IDENT, text[i:end], i))
            i = end
            continue

        # -- %MACRO% from a DEFINE ----------------------------------------
        if ch == "%":
            m = _MACRO_RE.match(text, i)
            if not m:
                raise LexError("unterminated %macro% reference", i)
            end = m.end()
            # A DEFINE abbreviation is a name PREFIX, so what follows it with
            # no space belongs to the same point name: DEFINE(X,"BLD1.AHU1.")
            # then %X%RDP is one object, not two tokens. Siemens' own shipped
            # application library writes these unquoted throughout, and 146
            # lines of it failed to parse until this joined them.
            # The tail may start with a digit -- %X%1AL is a real name in
            # Siemens' own library -- so this is a plain character run rather
            # than _IDENT_RE, which requires a leading letter.
            while end < n and (text[end].isalnum() or text[end] in "_$"):
                end += 1
            # The same dot problem as everywhere else: %X%NAL.GT.%X%OAL is a
            # name, a dotted operator and another name -- not one long name.
            # Guarded exactly as the [NodeName] branch above guards it.
            while end < n and text[end] == ".":
                rest = text[end:].upper()
                if any(rest.startswith(op) for op in _DOTOPS):
                    break
                seg = _IDENT_RE.match(text, end + 1)
                if not seg:
                    break
                end = seg.end()
            tokens.append(Token(Tok.MACRO, text[i:end], i))
            i = end
            continue

        # -- numbers and times --------------------------------------------
        if ch.isdigit():
            m = _TIME_RE.match(text, i)
            if m:
                tokens.append(Token(Tok.TIME, m.group(), i))
                i = m.end()
                continue
            m = _NUMBER_RE.match(text, i)
            tokens.append(Token(Tok.NUMBER, m.group(), i))
            i = m.end()
            continue

        # -- identifiers ---------------------------------------------------
        if ch.isalpha() or ch in "_$":
            m = _IDENT_RE.match(text, i)
            start = i
            end = m.end()
            # Absorb dotted segments of a system name written without quotes,
            # as in Siemens' own DEFINE example: DEFINE(A01,Bld01.Ahu01).
            # A dotted operator always wins, so RMTEMP.GT.80.0 still lexes as
            # three tokens rather than one long name.
            while end < n and text[end] == ".":
                rest = text[end:].upper()
                if any(rest.startswith(op) for op in _DOTOPS):
                    break
                seg = _IDENT_RE.match(text, end + 1)
                if not seg:
                    break
                end = seg.end()
            tokens.append(Token(Tok.IDENT, text[start:end], start))
            i = end
            continue

        # -- punctuation ---------------------------------------------------
        simple = {
            "(": Tok.LPAREN,
            ")": Tok.RPAREN,
            ",": Tok.COMMA,
            "=": Tok.ASSIGN,
            "&": Tok.AMP,
        }
        if ch in simple:
            tokens.append(Token(simple[ch], ch, i))
            i += 1
            continue

        if ch in "+-*/":
            tokens.append(Token(Tok.OP, ch, i))
            i += 1
            continue

        raise LexError("unexpected character %r" % ch, i)

    tokens.append(Token(Tok.EOF, "", n))
    return tokens
