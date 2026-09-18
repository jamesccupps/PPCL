"""AST node definitions for PPCL.

The tree is deliberately shallow. PPCL has no block structure: a line holds at
most one statement, and the only nesting is an IF's THEN/ELSE clause and the
statement trailing a SAMPLE. Keeping the tree flat makes the control-flow
analysis in :mod:`ppcl.analyzer` a straightforward walk over line numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# --------------------------------------------------------------------------
# Expressions
# --------------------------------------------------------------------------


class Expr:
    """Base class for every expression node."""

    col: int = 0


@dataclass
class Num(Expr):
    """A numeric literal. ``raw`` is kept because PPCL distinguishes ``5``
    from ``5.0`` -- several commands reject bare integers."""

    value: float
    raw: str
    col: int = 0

    @property
    def is_integer_literal(self) -> bool:
        return "." not in self.raw


@dataclass
class TimeLit(Expr):
    """A military-time literal such as ``17:00``."""

    hour: int
    minute: int
    raw: str
    col: int = 0

    @property
    def as_decimal_hours(self) -> float:
        return self.hour + self.minute / 60.0


@dataclass
class Ref(Expr):
    """A reference to a point, local variable, resident point, or status word.

    ``quoted`` records whether the source wrote the name in double quotes,
    which PPCL requires for names longer than six characters or containing
    anything outside ``A-Z0-9``.
    """

    name: str
    quoted: bool = False
    col: int = 0

    @property
    def is_local(self) -> bool:
        return self.name.startswith("$")

    @property
    def bare_name(self) -> str:
        """The name with any leading ``$`` local-variable sigil removed."""
        return self.name[1:] if self.name.startswith("$") else self.name


@dataclass
class PriorityRef(Expr):
    """An ``@EMER`` / ``@NONE`` / ... priority indicator."""

    name: str
    col: int = 0


@dataclass
class MacroRef(Expr):
    """A ``%ABBREV%`` reference introduced by a DEFINE statement."""

    name: str
    col: int = 0

    @property
    def bare_name(self) -> str:
        return self.name.strip("%")


@dataclass
class FuncCall(Expr):
    """One of the single-argument arithmetic or special functions."""

    name: str
    arg: Expr
    col: int = 0


@dataclass
class BinOp(Expr):
    """A binary arithmetic, relational, or logical operation."""

    op: str
    left: Expr
    right: Expr
    col: int = 0


@dataclass
class UnaryOp(Expr):
    """Unary plus or minus."""

    op: str
    operand: Expr
    col: int = 0


# --------------------------------------------------------------------------
# Statements
# --------------------------------------------------------------------------


class Stmt:
    """Base class for every statement node."""

    col: int = 0


@dataclass
class Comment(Stmt):
    """A ``C``-prefixed comment line."""

    text: str
    col: int = 0


@dataclass
class Assignment(Stmt):
    """``target = expr``."""

    target: Expr
    expr: Expr
    col: int = 0


@dataclass
class CommandCall(Stmt):
    """A call to a named PPCL command.

    ``priority`` is split out of ``args`` when the first argument is an
    ``@``-priority indicator, because the manual counts the priority against
    the command's argument budget but treats it as a distinct concept.
    """

    name: str
    args: list = field(default_factory=list)
    priority: PriorityRef = None
    parenthesised: bool = True
    col: int = 0


@dataclass
class If(Stmt):
    """``IF (cond) THEN then_stmt [ELSE else_stmt]``."""

    cond: Expr
    then_stmt: Stmt = None
    else_stmt: Stmt = None
    col: int = 0


@dataclass
class Goto(Stmt):
    """``GOTO line``."""

    target: int
    col: int = 0


@dataclass
class Gosub(Stmt):
    """``GOSUB line [arg, ...]``."""

    target: int
    args: list = field(default_factory=list)
    col: int = 0


@dataclass
class Return(Stmt):
    """``RETURN`` -- the end of a subroutine."""

    col: int = 0


@dataclass
class Sampled(Stmt):
    """``SAMPLE(sec) <statement>`` -- rate-limits the trailing statement."""

    seconds: Expr
    statement: Stmt = None
    col: int = 0


@dataclass
class ParameterDecl(Stmt):
    """``PARAMETER NAME = value`` -- a compile-time constant."""

    name: str
    value: Expr
    col: int = 0


@dataclass
class Unparsed(Stmt):
    """A statement the parser could not understand.

    Keeping the raw text rather than aborting lets the linter report every
    other problem in a file instead of stopping at the first bad line.
    """

    raw: str
    error: str = ""
    col: int = 0


# --------------------------------------------------------------------------
# Lines and programs
# --------------------------------------------------------------------------


@dataclass
class Line:
    """One numbered PPCL program line."""

    number: int
    stmt: Stmt
    raw: str
    source_line: int  # 1-based index within the source file
    #: Raw text of the statement portion, after the line number.
    body: str = ""
    #: True if this line's text was assembled from ``&`` continuations.
    continued: bool = False
    #: Set from a panel report: the line exists in the program but the panel
    #: is not evaluating it. Nothing in a text export records this, so it is
    #: False unless a PPCL DISPLAY REPORT said otherwise. See ``ppcl.report``.
    disabled: bool = False
    #: The compiler did not recognise this line's command and wrapped it in an
    #: ``UNKNOWN (...)`` marker. Unlike ``disabled``, this one IS in the text:
    #: "Any unknown PPCL commands will be added with an UNKNOWN (...) marker
    #: and ignored by the compiler upon saving a program." -- Desigo PXC.A Web
    #: Interface User Guide (A6V12893115), PPCL Diagnostics.
    unknown: bool = False

    @property
    def is_comment(self) -> bool:
        return isinstance(self.stmt, Comment)

    @property
    def is_executable(self) -> bool:
        """Whether the panel evaluates this line.

        A disabled line is not executed, so for flow analysis it behaves
        exactly like a comment -- control passes over it, and a branch aimed
        at it lands on the next line the panel will actually run.
        """
        if self.disabled or self.unknown:
            return False
        return not isinstance(self.stmt, (Comment, ParameterDecl))


@dataclass
class Program:
    """A parsed PPCL program."""

    lines: list = field(default_factory=list)
    #: Statements that carried no line number (PARAMETER directives).
    directives: list = field(default_factory=list)
    name: str = ""
    path: str = ""
    #: Parse diagnostics, as (source_line, line_number_or_None, message).
    errors: list = field(default_factory=list)

    def by_number(self) -> dict:
        """Map line number to the FIRST line carrying it.

        Duplicate line numbers are a real defect that appears in production
        PPCL; the duplicates are reported by the linter rather than silently
        overwriting each other here.
        """
        out = {}
        for ln in self.lines:
            out.setdefault(ln.number, ln)
        return out

    def executable_lines(self) -> list:
        return [ln for ln in self.lines if ln.is_executable]
