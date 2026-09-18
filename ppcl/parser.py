"""Recursive-descent parser for PPCL.

Parsing is error-tolerant by design. A line that cannot be understood becomes
an :class:`~ppcl.ast_nodes.Unparsed` node and parsing continues, so a single
malformed line does not hide the rest of a program's problems. Every failure is
recorded on ``Program.errors``.
"""

from __future__ import annotations

import re

from . import spec
from .ast_nodes import (
    Assignment,
    BinOp,
    Comment,
    CommandCall,
    Expr,
    FuncCall,
    Gosub,
    Goto,
    If,
    Line,
    MacroRef,
    Num,
    ParameterDecl,
    PriorityRef,
    Program,
    Ref,
    Return,
    Sampled,
    Stmt,
    TimeLit,
    UnaryOp,
    Unparsed,
)
from .lexer import LexError, Tok, Token, tokenize

#: A program line starts with a run of digits, then whitespace, then the body.
#: Real exports zero-pad to five digits and separate with a tab; hand-entered
#: code often uses spaces and no padding. Both are accepted.
_LINE_RE = re.compile(r"^\s*(\d{1,5})[ \t]+(.*)$")
#: A line number with nothing after it is legal (an empty numbered line).
_BARE_LINE_RE = re.compile(r"^\s*(\d{1,5})\s*$")


class ParseError(Exception):
    """Raised internally when a statement cannot be parsed."""

    def __init__(self, message: str, col: int = 0):
        super().__init__(message)
        self.message = message
        self.col = col


def split_line_number(text: str):
    """Split ``text`` into ``(line_number, body)``.

    Returns ``(None, text)`` when the line carries no line number, which is
    legal for a PARAMETER directive.
    """
    m = _LINE_RE.match(text)
    if m:
        return int(m.group(1)), m.group(2)
    m = _BARE_LINE_RE.match(text)
    if m:
        return int(m.group(1)), ""
    return None, text.strip()


def _is_comment_body(body: str) -> bool:
    """True if ``body`` is a ``C``-prefixed comment.

    ``C`` alone is a comment, as is ``C`` followed by whitespace. ``CRTIME``
    is not, so the character after the C must not continue an identifier.
    """
    if not body:
        return False
    if body[0] not in "Cc":
        return False
    return len(body) == 1 or not (body[1].isalnum() or body[1] in "_$(")


# --------------------------------------------------------------------------
# Statement parser
# --------------------------------------------------------------------------


class _StatementParser:
    """Parses the token stream of a single PPCL statement."""

    def __init__(self, tokens: list):
        self.toks = tokens
        self.pos = 0

    # -- token helpers -----------------------------------------------------

    @property
    def cur(self) -> Token:
        return self.toks[self.pos]

    def at(self, kind: Tok, text: str = None) -> bool:
        t = self.cur
        if t.kind is not kind:
            return False
        return text is None or t.upper == text

    def advance(self) -> Token:
        t = self.toks[self.pos]
        if t.kind is not Tok.EOF:
            self.pos += 1
        return t

    def expect(self, kind: Tok, what: str) -> Token:
        if self.cur.kind is not kind:
            raise ParseError(
                "expected %s but found %r" % (what, self.cur.text or "end of line"),
                self.cur.col,
            )
        return self.advance()

    def at_end(self) -> bool:
        return self.cur.kind is Tok.EOF

    # -- statements --------------------------------------------------------

    def parse_statement(self) -> Stmt:
        """Parse one statement from the current position."""
        t = self.cur

        if t.kind is Tok.IDENT:
            word = t.upper
            if word == "IF":
                return self._parse_if()
            if word == "GOTO":
                return self._parse_goto()
            if word == "GOSUB":
                return self._parse_gosub()
            if word == "RETURN":
                self.advance()
                return Return(col=t.col)
            if word == "SAMPLE":
                return self._parse_sample()
            if word == spec.PARAMETER_KEYWORD:
                return self._parse_parameter()
            # Anything of the form NAME(...) is parsed as a command call even
            # when NAME is unknown, so the linter can report a misspelling
            # precisely instead of the whole line failing to parse.
            if self._next_is(Tok.LPAREN) and word not in spec.FUNCTIONS:
                return self._parse_command()

        # Anything else must be an assignment: <ref> = <expr>
        return self._parse_assignment()

    def _next_is(self, kind: Tok) -> bool:
        return (
            self.pos + 1 < len(self.toks) and self.toks[self.pos + 1].kind is kind
        )

    def _parse_if(self) -> Stmt:
        tok = self.advance()  # IF
        self.expect(Tok.LPAREN, "'(' after IF")
        cond = self.parse_expr()
        self.expect(Tok.RPAREN, "')' closing the IF condition")
        if not self.at(Tok.IDENT, "THEN"):
            raise ParseError("expected THEN after the IF condition", self.cur.col)
        self.advance()
        then_stmt = None
        if not self.at_end() and not self.at(Tok.IDENT, "ELSE"):
            then_stmt = self.parse_statement()
        else_stmt = None
        if self.at(Tok.IDENT, "ELSE"):
            self.advance()
            if not self.at_end():
                else_stmt = self.parse_statement()
        return If(cond, then_stmt, else_stmt, col=tok.col)

    def _parse_goto(self) -> Stmt:
        tok = self.advance()
        # Parentheses are not standard here but appear in the wild.
        paren = self.at(Tok.LPAREN)
        if paren:
            self.advance()
        num = self.expect(Tok.NUMBER, "a line number after GOTO")
        if paren and self.at(Tok.RPAREN):
            self.advance()
        return Goto(int(float(num.text)), col=tok.col)

    def _parse_gosub(self) -> Stmt:
        tok = self.advance()
        paren = self.at(Tok.LPAREN)
        if paren:
            self.advance()
        num = self.expect(Tok.NUMBER, "a line number after GOSUB")
        target = int(float(num.text))
        args = []
        # Arguments may follow with or without a comma and with or without
        # surrounding parentheses; the manual permits all of these forms.
        if self.at(Tok.COMMA):
            self.advance()
        if not paren and self.at(Tok.LPAREN):
            paren = True
            self.advance()
        while not self.at_end() and not self.at(Tok.RPAREN):
            args.append(self.parse_expr())
            if self.at(Tok.COMMA):
                self.advance()
            else:
                break
        if paren and self.at(Tok.RPAREN):
            self.advance()
        return Gosub(target, args, col=tok.col)

    def _parse_sample(self) -> Stmt:
        tok = self.advance()
        self.expect(Tok.LPAREN, "'(' after SAMPLE")
        seconds = self.parse_expr()
        self.expect(Tok.RPAREN, "')' closing the SAMPLE interval")
        inner = None
        if not self.at_end():
            inner = self.parse_statement()
        return Sampled(seconds, inner, col=tok.col)

    def _parse_parameter(self) -> Stmt:
        tok = self.advance()
        name_tok = self.cur
        if name_tok.kind not in (Tok.IDENT, Tok.QUOTED):
            raise ParseError("expected a label name after PARAMETER", name_tok.col)
        self.advance()
        self.expect(Tok.ASSIGN, "'=' in a PARAMETER declaration")
        value = self.parse_expr()
        return ParameterDecl(name_tok.text, value, col=tok.col)

    def _parse_command(self) -> Stmt:
        tok = self.advance()  # command name
        name = tok.upper
        self.expect(Tok.LPAREN, "'(' after %s" % name)
        args = []
        priority = None
        if not self.at(Tok.RPAREN):
            while True:
                if self.cur.kind is Tok.PRIORITY and not args and priority is None:
                    p = self.advance()
                    priority = PriorityRef(p.upper, col=p.col)
                else:
                    args.append(self.parse_expr())
                if self.at(Tok.COMMA):
                    self.advance()
                    continue
                break
        self.expect(Tok.RPAREN, "')' closing %s" % name)
        return CommandCall(name, args, priority, col=tok.col)

    def _parse_assignment(self) -> Stmt:
        start = self.cur
        target = self.parse_primary()
        if not self.at(Tok.ASSIGN):
            raise ParseError(
                "expected '=' -- %r is not a known command and the line is not "
                "an assignment" % start.text,
                start.col,
            )
        self.advance()
        expr = self.parse_expr()
        return Assignment(target, expr, col=start.col)

    # -- expressions -------------------------------------------------------

    def parse_expr(self, min_prec: int = 99) -> Expr:
        """Parse an expression using the manual's precedence table.

        PPCL numbers precedence with 1 as the *highest*, so a lower number
        binds tighter. We therefore recurse while the operator's level is
        numerically below the caller's limit.
        """
        left = self.parse_unary()
        while True:
            op = self._peek_operator()
            if op is None:
                break
            prec = spec.PRECEDENCE[op]
            if prec > min_prec:
                break
            tok = self.advance()
            right = self.parse_expr(prec - 1)
            left = BinOp(op, left, right, col=tok.col)
        return left

    def _peek_operator(self):
        t = self.cur
        if t.kind is Tok.DOTOP and t.upper in spec.PRECEDENCE:
            return t.upper
        if t.kind is Tok.OP and t.text in spec.PRECEDENCE:
            return t.text
        return None

    def parse_unary(self) -> Expr:
        t = self.cur
        if t.kind is Tok.OP and t.text in "+-":
            self.advance()
            return UnaryOp(t.text, self.parse_unary(), col=t.col)
        return self.parse_primary()

    def parse_primary(self) -> Expr:
        t = self.cur

        if t.kind is Tok.LPAREN:
            self.advance()
            inner = self.parse_expr()
            self.expect(Tok.RPAREN, "')' closing a parenthesised expression")
            return inner

        if t.kind is Tok.NUMBER:
            self.advance()
            return Num(float(t.text), t.text, col=t.col)

        if t.kind is Tok.TIME:
            self.advance()
            hh, mm = t.text.split(":")
            return TimeLit(int(hh), int(mm), t.text, col=t.col)

        if t.kind is Tok.QUOTED:
            self.advance()
            return Ref(t.text, quoted=True, col=t.col)

        if t.kind is Tok.PRIORITY:
            self.advance()
            return PriorityRef(t.upper, col=t.col)

        if t.kind is Tok.ATNAME:
            self.advance()
            return Ref(t.text, col=t.col)

        if t.kind is Tok.MACRO:
            self.advance()
            return MacroRef(t.text, col=t.col)

        if t.kind is Tok.IDENT:
            name = t.upper
            # Anything of the form NAME(...) inside an expression is parsed as
            # a function call even when NAME is unknown, so the linter can
            # name the offending function instead of the line failing to parse.
            if self._next_is(Tok.LPAREN):
                self.advance()
                self.advance()
                arg = self.parse_expr()
                self.expect(Tok.RPAREN, "')' closing %s" % name)
                return FuncCall(name, arg, col=t.col)
            self.advance()
            return Ref(t.text, col=t.col)

        raise ParseError("unexpected %r in an expression" % (t.text or "end of line"), t.col)


# --------------------------------------------------------------------------
# Program parser
# --------------------------------------------------------------------------


def _join_continuations(raw_lines: list):
    """Fold ``&``-continued source lines into single logical lines.

    Yields ``(source_line_number, text, was_continued)``.
    """
    buf = None
    start = 0
    continued = False
    for idx, raw in enumerate(raw_lines, start=1):
        stripped = raw.rstrip("\r\n")
        if buf is None:
            start, buf, continued = idx, stripped, False
        else:
            # Drop the continuation's own line number if it carries one, since
            # the fragment belongs to the statement that opened the buffer.
            num, body = split_line_number(stripped)
            buf += body if num is not None else stripped.strip()
            continued = True
        if buf.rstrip().endswith("&"):
            buf = buf.rstrip()[:-1]
            continue
        yield start, buf, continued
        buf = None
    if buf is not None:
        yield start, buf, continued


def parse_statement_text(body: str) -> Stmt:
    """Parse a bare statement string. Raises ParseError or LexError."""
    tokens = tokenize(body)
    p = _StatementParser(tokens)
    stmt = p.parse_statement()
    if not p.at_end():
        raise ParseError("unexpected trailing %r" % p.cur.text, p.cur.col)
    return stmt


def parse(text: str, name: str = "", path: str = "") -> Program:
    """Parse a complete PPCL program."""
    prog = Program(name=name, path=path)

    for src_no, logical, continued in _join_continuations(text.splitlines()):
        if not logical.strip():
            continue

        number, body = split_line_number(logical)
        body = body.strip()

        if number is None:
            # Only PARAMETER may legally omit a line number.
            if not body:
                continue
            try:
                stmt = parse_statement_text(body)
            except (ParseError, LexError) as exc:
                prog.errors.append((src_no, None, exc.message))
                continue
            if isinstance(stmt, ParameterDecl):
                prog.directives.append(stmt)
            else:
                prog.errors.append(
                    (src_no, None, "statement has no line number: %r" % body)
                )
            continue

        if _is_comment_body(body):
            text_after_c = body[1:].strip()
            stmt = Comment(text_after_c)
            prog.lines.append(
                Line(number, stmt, logical, src_no, body=body, continued=continued)
            )
            continue

        if not body:
            # A numbered but empty line: treat as an empty comment so it still
            # occupies its line number for reference checks.
            prog.lines.append(
                Line(number, Comment(""), logical, src_no, body="", continued=continued)
            )
            continue

        try:
            stmt = parse_statement_text(body)
        except (ParseError, LexError) as exc:
            stmt = Unparsed(body, exc.message, col=getattr(exc, "col", 0))
            prog.errors.append((src_no, number, exc.message))

        prog.lines.append(
            Line(number, stmt, logical, src_no, body=body, continued=continued)
        )

    return prog


def parse_file(path: str) -> Program:
    """Parse a PPCL file from disk."""
    import os

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    name = os.path.splitext(os.path.basename(path))[0]
    return parse(text, name=name, path=path)
