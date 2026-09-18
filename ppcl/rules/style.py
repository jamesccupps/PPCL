"""Style rules drawn from the manual's "PPCL guidelines" section.

These are guidelines, not errors. They are reported at STYLE severity so a
review can filter them out with ``--min-severity warning``.
"""

from __future__ import annotations

from .. import spec
from ..analyzer import substatements
from ..ast_nodes import CommandCall, Comment
from ..diagnostics import Diagnostic, Severity
from ..linter import rule


def _d(code, msg, line=None, **kw):
    return Diagnostic(code=code, severity=Severity.STYLE, message=msg, line=line, **kw)


@rule("S601", "Line numbers are not spaced in multiples of ten", Severity.STYLE)
def numbering_increment(ctx):
    numbers = ctx.analysis.numbers
    if len(numbers) < 3:
        return
    gaps = [b - a for a, b in zip(numbers, numbers[1:])]
    tight = [g for g in gaps if g < 10]
    if len(tight) <= len(gaps) // 4:
        return
    yield _d(
        "S601",
        "%d of %d line-number gaps are smaller than 10" % (len(tight), len(gaps)),
        numbers[0],
        detail="The manual recommends numbering in multiples of ten so lines can "
        "be inserted later without renumbering the program.",
        manual="Chapter 2, PPCL guidelines",
        suggestion="ppcl renumber --start 10 --step 10 rewrites the program and "
        "fixes every GOTO/GOSUB/ACT reference to match.",
    )


@rule("S602", "Program has no comments", Severity.STYLE)
def missing_comments(ctx):
    comments = [ln for ln in ctx.program.lines if ln.is_comment and ln.stmt.text]
    executable = ctx.program.executable_lines()
    if not executable:
        return
    if comments:
        return
    yield _d(
        "S602",
        "no comment lines in a program of %d executable lines" % len(executable),
        executable[0].number,
        detail="The manual asks that program logic be documented with comment "
        "lines. In practice the next person to touch an AHU sequence at 2am is "
        "the one who pays for this.",
        manual="Chapter 2, PPCL guidelines and Program documentation",
    )


@rule("S603", "Long stretch of code with no comments", Severity.STYLE)
def comment_density(ctx):
    run = []
    worst = []
    for ln in ctx.program.lines:
        if ln.is_comment and ln.stmt.text:
            if len(run) > len(worst):
                worst = run
            run = []
        elif ln.is_executable:
            run.append(ln)
    if len(run) > len(worst):
        worst = run
    if len(worst) >= 25:
        yield _d(
            "S603",
            "%d consecutive executable lines (%d-%d) carry no comment"
            % (len(worst), worst[0].number, worst[-1].number),
            worst[0].number,
            manual="Chapter 2, Internal documentation",
        )


@rule("S604", "Program has no header block", Severity.STYLE)
def missing_header(ctx):
    lines = ctx.program.lines
    if not lines:
        return
    head = [ln for ln in lines[:12] if ln.is_comment and ln.stmt.text]
    if len(head) >= 3:
        return
    first = lines[0]
    yield _d(
        "S604",
        "the program does not open with a comment header",
        first.number,
        detail="A header naming the program, its author, what equipment it "
        "controls and what it depends on makes a panel's programs reviewable "
        "without opening the drawings.",
        manual="Chapter 2, Program documentation",
        suggestion="ppcl new --template header emits a conforming block.",
    )


@rule("S605", "First line of the program is not always executed", Severity.STYLE)
def first_line_guard(ctx):
    lines = ctx.program.lines
    if not lines:
        return
    first_exec = next((ln for ln in lines if ln.is_executable), None)
    if first_exec is None:
        return
    from ..ast_nodes import If

    if isinstance(first_exec.stmt, If):
        yield _d(
            "S605",
            "the first executable line (%d) is conditional" % first_exec.number,
            first_exec.number,
            detail="After a power failure the panel resumes at the program's "
            "first line. The manual recommends that line always execute.",
            manual="Chapter 2, PPCL guidelines",
        )


@rule("S606", "Program is much longer or shorter than its siblings", Severity.STYLE)
def program_balance(ctx):
    # Only meaningful across a panel; the CLI supplies sibling sizes through
    # the context when linting a directory.
    sizes = getattr(ctx, "sibling_sizes", None)
    if not sizes or len(sizes) < 2:
        return
    mine = len(ctx.program.executable_lines())
    if mine == 0:
        return
    longest = max(sizes.values())
    if longest and longest / max(mine, 1) >= 4:
        yield _d(
            "S606",
            "this program has %d executable lines while the largest in the panel "
            "has %d" % (mine, longest),
            None,
            detail="APOGEE panels execute one line of each enabled program per "
            "cycle, so a short program runs its whole loop many times before a "
            "long one completes a single pass. The manual recommends keeping "
            "programs roughly the same length.",
            manual="Chapter 2, PPCL guidelines",
        )
