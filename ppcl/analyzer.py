"""Control-flow and data-flow analysis for a parsed PPCL program.

PPCL's execution model is simple but unforgiving:

* Lines run in ascending line-number order.
* Falling off the last line wraps back to the first line -- the program is an
  infinite loop by construction.
* GOTO and GOSUB are the only branches, and a GOSUB body runs until RETURN.

Almost every interesting defect in a PPCL program is a consequence of that
model: a backward GOTO that skips the last line silently breaks every
time-based command in the program, an unreachable subroutine never runs, and a
point written from two places fights itself. This module builds the graph those
checks need.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    ParameterDecl,
    PriorityRef,
    Program,
    Ref,
    Return,
    Sampled,
    Stmt,
    UnaryOp,
    Unparsed,
)

# --------------------------------------------------------------------------
# Which command arguments are written rather than read
# --------------------------------------------------------------------------

#: Commands where every point argument is commanded (written).
_ALL_ARGS_WRITTEN = frozenset(
    {
        "ON", "OFF", "AUTO", "FAST", "SLOW", "ALARM", "NORMAL", "DISALM",
        "ENALM", "DISCOV", "ENCOV", "RELEAS", "DAY", "NIGHT", "EMON", "EMOFF",
        "EMAUTO", "EMFAST", "EMSLOW",
    }
)

#: Commands whose leading argument is a value, with the rest written.
_SKIP_FIRST_WRITTEN = frozenset({"SET", "EMSET", "HLIMIT", "LLIMIT", "INITTO", "STATE"})

#: Explicit written-argument index sets for the irregular commands.
_WRITTEN_INDEXES = {
    "MAX": (0,),
    "MIN": (0,),
    "TABLE": (1,),
    "TIMAVG": (0,),
    "LOOP": (2,),
    "WAIT": (2,),
    "SSTO": (2, 3),
    "PDLSET": (1,),
}

#: Commands that reference PPCL *line numbers* rather than points.
LINE_REFERENCING = frozenset({"ACT", "DEACT", "ENABLE", "DISABL", "ONPWRT"})


def written_arg_indexes(call: CommandCall) -> set:
    """Indexes into ``call.args`` that the command writes to."""
    name = call.name
    n = len(call.args)
    if name in _WRITTEN_INDEXES:
        return {i for i in _WRITTEN_INDEXES[name] if i < n}
    if name in _ALL_ARGS_WRITTEN:
        return set(range(n))
    if name in _SKIP_FIRST_WRITTEN:
        return set(range(1, n))
    if name == "DBSWIT":
        return set(range(4, n))
    if name == "TOD":
        return set(range(4, n))
    if name == "TODSET":
        return set(range(6, n))
    if name == "DC":
        return {i for i in range(0, n, 2)}
    if name == "DCR":
        return {i for i in range(0, n, 4)}
    if name == "PDLMTR":
        return set()
    if name == "LOCAL":
        return set(range(n))
    return set()


# --------------------------------------------------------------------------
# Reference collection
# --------------------------------------------------------------------------


def walk_expr(node):
    """Yield every expression node in ``node``, depth first."""
    if node is None:
        return
    yield node
    if isinstance(node, BinOp):
        yield from walk_expr(node.left)
        yield from walk_expr(node.right)
    elif isinstance(node, UnaryOp):
        yield from walk_expr(node.operand)
    elif isinstance(node, FuncCall):
        yield from walk_expr(node.arg)


def expr_refs(node):
    """Yield every :class:`Ref` inside an expression."""
    for sub in walk_expr(node):
        if isinstance(sub, Ref):
            yield sub


def substatements(stmt):
    """Yield ``stmt`` and every statement nested inside it."""
    if stmt is None:
        return
    yield stmt
    if isinstance(stmt, If):
        yield from substatements(stmt.then_stmt)
        yield from substatements(stmt.else_stmt)
    elif isinstance(stmt, Sampled):
        yield from substatements(stmt.statement)


# --------------------------------------------------------------------------
# Analysis result
# --------------------------------------------------------------------------


@dataclass
class PointUse:
    """One read or write of a point, local variable, or resident point."""

    name: str
    line: int
    kind: str  # "read" | "write" | "declare"
    context: str  # command name, "assign", or "condition"
    priority: str = None
    quoted: bool = False


@dataclass
class Subroutine:
    """A GOSUB target and the span of lines it covers."""

    entry: int
    lines: list = field(default_factory=list)
    returns: list = field(default_factory=list)
    callers: list = field(default_factory=list)
    falls_through: bool = False


@dataclass
class Analysis:
    """Everything the lint rules need to know about a program."""

    program: Program
    #: Line number -> Line, first occurrence wins.
    index: dict = field(default_factory=dict)
    #: Sorted list of line numbers actually present.
    numbers: list = field(default_factory=list)
    #: Line numbers reachable from normal top-of-program flow.
    reachable: set = field(default_factory=set)
    #: Line numbers reachable only as part of a subroutine body.
    subroutine_lines: set = field(default_factory=set)
    subroutines: dict = field(default_factory=dict)
    #: (from_line, to_line, kind) for every branch, kind in goto/gosub/ref.
    edges: list = field(default_factory=list)
    #: Every point reference in the program.
    uses: list = field(default_factory=list)
    #: Locals declared via LOCAL(...).
    declared_locals: set = field(default_factory=set)
    #: Abbreviations declared via DEFINE(...).
    defines: dict = field(default_factory=dict)
    #: Labels declared via PARAMETER.
    parameters: dict = field(default_factory=dict)
    #: True when the final line is reached on a normal pass.
    last_line_reachable: bool = False
    #: Lines that execute repeatedly once the program is running, i.e. lines
    #: lying on a cycle of the control-flow graph.
    steady_state: set = field(default_factory=set)
    #: Reachable lines that run once at startup and never again.
    one_shot: set = field(default_factory=set)
    #: Lowest line number of the main steady-state loop, if there is one.
    loop_entry: int = None

    # -- convenience -------------------------------------------------------

    def line_at(self, number: int):
        return self.index.get(number)

    def next_number(self, number: int):
        """The next line number present after ``number``, or None."""
        import bisect

        i = bisect.bisect_right(self.numbers, number)
        return self.numbers[i] if i < len(self.numbers) else None

    def resolve_target(self, number: int):
        """Resolve a branch target the way the field panel does.

        The manual states that if the target line does not exist, execution
        transfers to the next line after the specified number.
        """
        if number in self.index:
            return number
        return self.next_number(number)

    def writes_of(self, name: str):
        key = name.upper()
        return [u for u in self.uses if u.name.upper() == key and u.kind == "write"]

    def reads_of(self, name: str):
        key = name.upper()
        return [u for u in self.uses if u.name.upper() == key and u.kind == "read"]

    def all_point_names(self):
        seen = {}
        for u in self.uses:
            seen.setdefault(u.name.upper(), u.name)
        return sorted(seen.values())


# --------------------------------------------------------------------------
# The analyzer
# --------------------------------------------------------------------------


def analyze(program: Program) -> Analysis:
    """Build an :class:`Analysis` for ``program``."""
    a = Analysis(program=program)
    a.index = program.by_number()
    a.numbers = sorted(a.index)

    _collect_declarations(program, a)
    _collect_uses(program, a)
    _collect_edges(program, a)
    _compute_reachability(program, a)
    _compute_steady_state(program, a)
    _find_subroutines(program, a)
    return a


def _collect_declarations(program: Program, a: Analysis) -> None:
    for directive in program.directives:
        a.parameters[directive.name.upper()] = directive.value
    for ln in program.lines:
        for stmt in substatements(ln.stmt):
            if isinstance(stmt, ParameterDecl):
                a.parameters[stmt.name.upper()] = stmt.value
            elif isinstance(stmt, CommandCall):
                if stmt.name == "LOCAL":
                    for arg in stmt.args:
                        if isinstance(arg, Ref):
                            a.declared_locals.add(arg.bare_name.upper())
                elif stmt.name == "DEFINE" and len(stmt.args) >= 2:
                    key = stmt.args[0]
                    val = stmt.args[1]
                    if isinstance(key, Ref):
                        a.defines[key.name.upper()] = (
                            val.name if isinstance(val, Ref) else ""
                        )


def _record(a: Analysis, ref: Ref, line: int, kind: str, context: str,
            priority=None) -> None:
    # An @name that is not a priority is a BACnet property specifier -- the
    # third argument of GETVAL, the second of SETVAL. It names a property of a
    # point, not a point, so recording it as one would put it in the point
    # inventory and mark it unresolved against any loaded point database.
    if ref.name.startswith("@"):
        return
    a.uses.append(
        PointUse(
            name=ref.name,
            line=line,
            kind=kind,
            context=context,
            priority=priority,
            quoted=ref.quoted,
        )
    )


def _collect_uses(program: Program, a: Analysis) -> None:
    for ln in program.lines:
        _walk_stmt_uses(a, ln.number, ln.stmt)


def _walk_stmt_uses(a: Analysis, line: int, stmt, in_condition: bool = False) -> None:
    if stmt is None or isinstance(stmt, (Comment, Unparsed)):
        return

    if isinstance(stmt, Assignment):
        if isinstance(stmt.target, Ref):
            _record(a, stmt.target, line, "write", "assign")
        for ref in expr_refs(stmt.expr):
            _record(a, ref, line, "read", "assign")
        return

    if isinstance(stmt, If):
        for ref in expr_refs(stmt.cond):
            _record(a, ref, line, "read", "condition")
        _walk_stmt_uses(a, line, stmt.then_stmt)
        _walk_stmt_uses(a, line, stmt.else_stmt)
        return

    if isinstance(stmt, Sampled):
        for ref in expr_refs(stmt.seconds):
            _record(a, ref, line, "read", "SAMPLE")
        _walk_stmt_uses(a, line, stmt.statement)
        return

    if isinstance(stmt, Gosub):
        for ref in stmt.args:
            if isinstance(ref, Ref):
                _record(a, ref, line, "read", "GOSUB")
        return

    if isinstance(stmt, CommandCall):
        if stmt.name in LINE_REFERENCING:
            return
        priority = stmt.priority.name if stmt.priority else None
        written = written_arg_indexes(stmt)
        for i, arg in enumerate(stmt.args):
            kind = "write" if i in written else "read"
            if stmt.name == "LOCAL":
                # LOCAL declares a name without the sigil but every reference
                # to it carries one. Record the sigil form so the declaration
                # and its uses line up in the point inventory.
                kind = "declare"
                if isinstance(arg, Ref) and not arg.name.startswith("$"):
                    arg = Ref("$" + arg.name, arg.quoted, arg.col)
            if isinstance(arg, Ref):
                _record(a, arg, line, kind, stmt.name, priority)
            else:
                for ref in expr_refs(arg):
                    _record(a, ref, line, "read", stmt.name, priority)
        return


def _collect_edges(program: Program, a: Analysis) -> None:
    for ln in program.lines:
        for stmt in substatements(ln.stmt):
            if isinstance(stmt, Goto):
                a.edges.append((ln.number, stmt.target, "goto"))
            elif isinstance(stmt, Gosub):
                a.edges.append((ln.number, stmt.target, "gosub"))
            elif isinstance(stmt, CommandCall) and stmt.name in LINE_REFERENCING:
                for arg in stmt.args:
                    from .ast_nodes import Num

                    if isinstance(arg, Num):
                        a.edges.append((ln.number, int(arg.value), "ref"))
            elif isinstance(stmt, CommandCall) and stmt.name == "LSQ2":
                # Only the trailing startline#/endline# name PPCL lines; they
                # delimit the block of LSQDAT statements supplying the data.
                from .ast_nodes import Num

                numbers = [a2 for a2 in stmt.args if isinstance(a2, Num)]
                for arg in numbers[-2:]:
                    a.edges.append((ln.number, int(arg.value), "ref"))


def _successors(a: Analysis, ln: Line):
    """Line numbers control can reach from ``ln`` on a normal pass.

    A GOSUB is treated as returning to the following line: the body is walked
    separately by :func:`_find_subroutines`, so that a subroutine's lines are
    not confused with straight-line flow.
    """
    stmt = ln.stmt
    nxt = a.next_number(ln.number)

    if isinstance(stmt, Goto):
        t = a.resolve_target(stmt.target)
        return [t] if t is not None else []

    if isinstance(stmt, Return):
        return []

    if isinstance(stmt, If):
        out = []
        branches = [stmt.then_stmt, stmt.else_stmt]
        for branch in branches:
            if isinstance(branch, Goto):
                t = a.resolve_target(branch.target)
                if t is not None:
                    out.append(t)
        # An IF always has an implicit fall-through unless BOTH clauses branch
        # away unconditionally.
        both_branch = (
            isinstance(stmt.then_stmt, Goto)
            and isinstance(stmt.else_stmt, Goto)
        )
        if not both_branch and nxt is not None:
            out.append(nxt)
        return out

    return [nxt] if nxt is not None else []


def _compute_reachability(program: Program, a: Analysis) -> None:
    if not a.numbers:
        return
    start = a.numbers[0]
    seen = set()
    stack = [start]
    while stack:
        num = stack.pop()
        if num in seen or num not in a.index:
            continue
        seen.add(num)
        for succ in _successors(a, a.index[num]):
            if succ not in seen:
                stack.append(succ)
    a.reachable = seen
    a.last_line_reachable = a.numbers[-1] in seen


def _successors_wrapping(a: Analysis, ln: Line):
    """Successors including PPCL's implicit wrap from the last line to the first.

    The manual is explicit that when the last line of the program is reached,
    the computer automatically returns control to the first line. Modelling
    that edge is what makes the difference between "this program loops" and
    "this program falls off the end", and it is the basis of the steady-state
    analysis below.
    """
    succ = _successors(a, ln)
    if not succ and a.numbers and ln.number == a.numbers[-1]:
        return [a.numbers[0]]
    if a.numbers and ln.number == a.numbers[-1] and succ == []:
        return [a.numbers[0]]
    return succ


def _compute_steady_state(program: Program, a: Analysis) -> None:
    """Find the lines that keep executing once the program is up.

    Uses Tarjan's algorithm over the reachable subgraph. Any line on a cycle
    runs on every pass; a reachable line that is not on a cycle runs once at
    startup and is then left behind -- which is correct for initialisation but
    a defect for anything time-based.
    """
    nodes = [n for n in a.numbers if n in a.reachable]
    if not nodes:
        return

    index = {}
    low = {}
    on_stack = set()
    stack = []
    counter = [0]
    components = []

    def succs(n):
        ln = a.index.get(n)
        if ln is None:
            return []
        return [s for s in _successors_wrapping(a, ln) if s in a.reachable]

    for root in nodes:
        if root in index:
            continue
        # Iterative Tarjan to stay safe on long programs.
        work = [(root, iter(succs(root)))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(succs(nxt))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                components.append(comp)

    cyclic = set()
    for comp in components:
        if len(comp) > 1:
            cyclic.update(comp)
        else:
            only = comp[0]
            if only in succs(only):
                cyclic.add(only)

    a.steady_state = cyclic
    a.one_shot = set(nodes) - cyclic
    if cyclic:
        a.loop_entry = min(cyclic)


def _find_subroutines(program: Program, a: Analysis) -> None:
    """Walk each GOSUB target forward to its RETURN."""
    callers = {}
    for src, dst, kind in a.edges:
        if kind == "gosub":
            callers.setdefault(dst, []).append(src)

    for entry in sorted(callers):
        resolved = a.resolve_target(entry)
        if resolved is None:
            a.subroutines[entry] = Subroutine(entry=entry, callers=callers[entry])
            continue

        sub = Subroutine(entry=resolved, callers=callers[entry])
        seen = set()
        stack = [resolved]
        while stack:
            num = stack.pop()
            if num in seen or num not in a.index:
                continue
            seen.add(num)
            ln = a.index[num]
            if isinstance(ln.stmt, Return):
                sub.returns.append(num)
                continue
            nested_return = any(
                isinstance(s, Return) for s in substatements(ln.stmt)
            )
            if nested_return:
                sub.returns.append(num)
            for succ in _successors(a, ln):
                if succ not in seen:
                    stack.append(succ)
            if not _successors(a, ln) and not nested_return:
                sub.falls_through = True

        sub.lines = sorted(seen)
        # If the walk ran past the end of the program without a RETURN, the
        # subroutine falls through into whatever follows.
        if not sub.returns:
            sub.falls_through = True
        a.subroutines[entry] = sub
        a.subroutine_lines |= seen
