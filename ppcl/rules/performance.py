"""Optimisation rules.

PPCL runs on a fixed line-evaluation budget: the manual quotes roughly 350
lines/second on a Version 3.0 controller board and 500 on a 4.0, shared across
every enabled program in the panel and reduced further by attached FLN devices.
Lines removed here are lines the panel gets back.
"""

from __future__ import annotations

from .. import spec
from ..analyzer import substatements
from ..ast_nodes import (
    Assignment,
    BinOp,
    CommandCall,
    Comment,
    Gosub,
    Goto,
    If,
    Num,
    Ref,
    Return,
    Sampled,
    TimeLit,
)
from ..diagnostics import Diagnostic, Severity
from ..linter import rule

#: Manual, Chapter 2 PPCL guidelines: average lines evaluated per second.
LINES_PER_SECOND = {"3.0": 350, "4.0": 500}


def _d(code, sev, msg, line=None, **kw):
    return Diagnostic(code=code, severity=sev, message=msg, line=line, **kw)


@rule("P701", "Consecutive commands could be merged into one", Severity.INFO)
def mergeable_commands(ctx):
    """ON(A) on one line and ON(B) on the next can usually be ON(A,B)."""
    lines = [ln for ln in ctx.program.lines if ln.is_executable]
    run = []

    def flush(run):
        if len(run) < 2:
            return None
        first = run[0][1]
        cmd = spec.ALL.get(first.name)
        if cmd is None or not cmd.repeat:
            return None
        slots = len(cmd.fixed) + len(cmd.repeat) * cmd.max_repeat
        total = sum(len(c.args) for _, c in run)
        if total > slots:
            return None
        if len(cmd.repeat) != 1:
            return None
        numbers = [n for n, _ in run]
        points = []
        for _, c in run:
            for a in c.args:
                points.append(a.name if isinstance(a, Ref) else getattr(a, "raw", "?"))
        return _d(
            "P701",
            Severity.INFO,
            "lines %s each issue %s and could be a single command"
            % ("-".join(str(n) for n in (numbers[0], numbers[-1])), first.name),
            numbers[0],
            detail="%s accepts up to %d points per statement. Merging these "
            "reclaims %d line(s) of the panel's evaluation budget."
            % (first.name, slots, len(run) - 1),
            suggestion="%s(%s)" % (first.name, ",".join(points)),
        )

    for ln in lines:
        stmt = ln.stmt
        ok = (
            isinstance(stmt, CommandCall)
            and stmt.name in spec.ALL
            and spec.ALL[stmt.name].repeat
            and len(spec.ALL[stmt.name].repeat) == 1
            and spec.ALL[stmt.name].max_repeat > 1
            and stmt.priority is None
            and stmt.name not in ("LOCAL", "ACT", "DEACT", "ENABLE", "DISABL")
            and all(isinstance(a, Ref) for a in stmt.args)
        )
        if ok and (not run or run[-1][1].name == stmt.name):
            run.append((ln.number, stmt))
            continue
        diag = flush(run)
        if diag:
            yield diag
        run = [(ln.number, stmt)] if ok else []
    diag = flush(run)
    if diag:
        yield diag


@rule("P702", "Identical statement repeated", Severity.INFO)
def duplicate_statements(ctx):
    seen = {}
    for ln in ctx.program.lines:
        if not ln.is_executable:
            continue
        key = _normalize(ln.body)
        if not key:
            continue
        seen.setdefault(key, []).append(ln.number)
    for key, numbers in sorted(seen.items(), key=lambda kv: kv[1][0]):
        if len(numbers) < 2:
            continue
        yield _d(
            "P702",
            Severity.INFO,
            "the same statement appears at lines %s"
            % ", ".join(str(n) for n in numbers),
            numbers[0],
            detail="Statement: %s" % key,
            suggestion="If both copies really are needed the logic is probably "
            "better expressed once; otherwise delete the duplicate.",
        )


def _normalize(body: str) -> str:
    return " ".join(body.upper().split())


@rule("P703", "Condition is always true or always false", Severity.WARNING)
def constant_condition(ctx):
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, If):
                continue
            verdict = _constant_value(stmt.cond)
            if verdict is None:
                continue
            taken = "THEN" if verdict else ("ELSE" if stmt.else_stmt else "nothing")
            yield _d(
                "P703",
                Severity.WARNING,
                "this IF condition is always %s" % str(verdict).lower(),
                ln.number,
                detail="Both operands are literals, so the panel evaluates this "
                "test every pass and always runs the %s branch." % taken,
                suggestion="Replace the line with the branch that always runs, or "
                "fix the operand that was meant to be a point name.",
            )


def _constant_value(node):
    """Evaluate a condition built only from literals; None if not constant."""
    if isinstance(node, Num):
        return node.value != 0
    if not isinstance(node, BinOp):
        return None
    left = _literal(node.left)
    right = _literal(node.right)
    if left is None or right is None:
        return None
    ops = {
        ".EQ.": lambda a, b: a == b,
        ".NE.": lambda a, b: a != b,
        ".GT.": lambda a, b: a > b,
        ".GE.": lambda a, b: a >= b,
        ".LT.": lambda a, b: a < b,
        ".LE.": lambda a, b: a <= b,
    }
    fn = ops.get(node.op)
    return fn(left, right) if fn else None


def _literal(node):
    if isinstance(node, Num):
        return node.value
    if isinstance(node, TimeLit):
        return node.as_decimal_hours
    return None


@rule("P704", "Assignment has no effect", Severity.INFO)
def self_assignment(ctx):
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, Assignment):
                continue
            if not isinstance(stmt.target, Ref) or not isinstance(stmt.expr, Ref):
                continue
            if stmt.target.name.upper() == stmt.expr.name.upper():
                yield _d(
                    "P704",
                    Severity.INFO,
                    "%s is assigned to itself" % stmt.target.name,
                    ln.number,
                    suggestion="Delete the line.",
                )


@rule("P705", "Value written but never read", Severity.INFO)
def write_only_point(ctx):
    a = ctx.analysis
    for name in sorted({u.name.upper() for u in a.uses if u.name.startswith("$")}):
        if spec.is_builtin_local(name):
            continue
        writes = [u for u in a.uses if u.name.upper() == name and u.kind == "write"]
        reads = [u for u in a.uses if u.name.upper() == name and u.kind == "read"]
        if writes and not reads:
            yield _d(
                "P705",
                Severity.INFO,
                "local %s is written at line(s) %s but never read"
                % (name, ", ".join(str(w.line) for w in writes)),
                writes[0].line,
                detail="If it is read by another program through the "
                "PROGRAM:name syntax this is fine; otherwise the lines are dead.",
            )


@rule("P706", "Comment-only program regions inflate the evaluation budget", Severity.INFO)
def comment_ratio(ctx):
    total = len(ctx.program.lines)
    if total < 40:
        return
    comments = sum(1 for ln in ctx.program.lines if ln.is_comment)
    if comments / total < 0.6:
        return
    yield _d(
        "P706",
        Severity.INFO,
        "%d of %d lines (%.0f%%) are comments" % (comments, total, 100 * comments / total),
        None,
        detail="Comments are worth keeping, but a panel loaded with very large "
        "licence or history blocks in every program spends memory on them. Check "
        "that the header block is not duplicated across every program in the panel.",
    )


#: Siemens' subroutine benefit table, transcribed exactly from the Desigo CC
#: "PPCL Guidelines" topic. Keyed by (body lines excluding RETURN, calls per
#: pass); the value is "no", "even" or "yes". Body length is capped at 4 and
#: call count at 4, which is where the published table saturates.
#:
#: Note the row that is easy to get wrong: a ONE-LINE subroutine is never
#: worth it, at any number of calls.
SUBROUTINE_BENEFIT = {
    #        1 line  2 lines  3 lines  4+ lines
    1: {1: "no", 2: "no", 3: "no", 4: "no"},
    2: {1: "no", 2: "no", 3: "even", 4: "yes"},
    3: {1: "no", 2: "no", 3: "yes", 4: "yes"},
    4: {1: "no", 2: "yes", 3: "yes", 4: "yes"},
}


def subroutine_benefit(lines: int, calls: int) -> str:
    """Look up Siemens' verdict for a subroutine of ``lines`` called ``calls``."""
    row = SUBROUTINE_BENEFIT.get(min(max(calls, 1), 4), {})
    return row.get(min(max(lines, 1), 4), "yes")


@rule("P708", "Subroutine costs more than it saves", Severity.INFO)
def subroutine_not_worth_it(ctx):
    """A GOSUB has overhead of its own -- the call, the RETURN, and the reader.

    Siemens publishes a benefit table for exactly this decision. It is applied
    verbatim rather than approximated: a one-line subroutine never pays off, a
    two-line one needs four calls, and a three-line one breaks even at two and
    pays off at three.
    """
    a = ctx.analysis
    for entry, sub in sorted(a.subroutines.items()):
        body = [
            n for n in sub.lines
            if (ln := a.line_at(n)) is not None and ln.is_executable
        ]
        # The published table counts body lines excluding the RETURN.
        length = max(0, len(body) - len(sub.returns))
        calls = len(set(sub.callers))
        if length == 0 or calls == 0:
            continue

        verdict = subroutine_benefit(length, calls)
        if verdict == "yes":
            continue

        if verdict == "even":
            message = (
                "the subroutine at line %d (%d lines, %d calls) breaks even "
                "against inlining" % (entry, length, calls)
            )
            detail = ("Siemens' table rates this configuration EVEN: neither "
                      "form is cheaper, so pick on readability.")
        else:
            message = (
                "the subroutine at line %d is %d line(s) called %d time(s), "
                "which costs more than inlining" % (entry, length, calls)
            )
            detail = (
                "Siemens' table rates this NO. A one-line subroutine is never "
                "worth it at any call count; a two-line one needs four calls."
                if length <= 2
                else "Siemens' table rates this NO at %d call(s)." % calls
            )

        yield _d(
            "P708",
            Severity.INFO,
            message,
            entry,
            detail=detail + " Inlining is fewer lines for the panel to "
            "evaluate and one less indirection to follow when reading.",
            manual="Desigo CC engineering help, PPCL Guidelines (Using Subroutines)",
            suggestion="Inline the body at the call site.",
        )


@rule("P707", "Program size and evaluation-rate estimate", Severity.INFO)
def evaluation_budget(ctx):
    executable = len(ctx.program.executable_lines())
    if executable == 0:
        return
    v3 = executable / LINES_PER_SECOND["3.0"]
    v4 = executable / LINES_PER_SECOND["4.0"]
    yield _d(
        "P707",
        Severity.INFO,
        "%d executable lines: about %.1fs per pass on a 3.0 board, %.1fs on a 4.0"
        % (executable, v3, v4),
        None,
        detail="These are the manual's average figures for a panel doing nothing "
        "else. Attached FLN devices are the largest single influence on the real "
        "rate, and every enabled program in the panel shares the budget.",
        manual="Chapter 2, PPCL guidelines",
    )
