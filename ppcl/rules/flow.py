"""Control-flow rules.

These carry the highest value in PPCL review. The language has no compiler to
catch a GOSUB that never returns, and the panel will happily run a program
whose last line is never reached -- silently disabling every LOOP, WAIT, TOD
and SAMPLE in it.
"""

from __future__ import annotations

from .. import spec
from ..analyzer import LINE_REFERENCING, substatements
from ..ast_nodes import (
    CommandCall,
    Comment,
    Gosub,
    Goto,
    If,
    Num,
    Return,
    Sampled,
)
from ..diagnostics import Diagnostic, Severity
from ..linter import rule


def _d(code, sev, msg, line=None, **kw):
    return Diagnostic(code=code, severity=sev, message=msg, line=line, **kw)


# --------------------------------------------------------------------------
# Branch targets
# --------------------------------------------------------------------------


@rule("E201", "Branch to a line number that does not exist", Severity.ERROR)
def missing_branch_target(ctx):
    a = ctx.analysis
    for src, dst, kind in a.edges:
        if dst in a.index:
            continue
        resolved = a.resolve_target(dst)
        verb = {"goto": "GOTO", "gosub": "GOSUB", "ref": "line reference"}[kind]
        if resolved is None:
            yield _d(
                "E201",
                Severity.ERROR,
                "%s targets line %d, which does not exist and has no line after it"
                % (verb, dst),
                src,
                detail="Execution falls off the end of the program.",
                manual="Chapter 4, GOTO",
            )
        elif kind == "ref":
            yield _d(
                "E201",
                Severity.ERROR,
                "%s targets line %d, which does not exist" % (verb, dst),
                src,
                detail="ACT/DEACT/ENABLE/DISABL name specific lines; a missing "
                "target means the intended line is never enabled or disabled.",
                manual="Chapter 4, ACT",
            )
        else:
            # This was an ERROR on APOGEE on the belief that the Desigo CC
            # compiler "will not save the program". Thirty-six of these turned
            # up across eight programs exported from a live Desigo CC, all of
            # them running, so that belief was wrong. Siemens lists it under
            # "Common Compiler Errors *and Warnings*", and the Program Editor
            # carries "Non-Existent GOTOs" as an opt-in warning toggle, not a
            # save-blocking check. The program compiles and the panel redirects
            # to the next existing line.
            #
            # It stays a warning rather than becoming advice, because the
            # redirect is positional: it is correct only for as long as nobody
            # inserts, deletes or renumbers anything in between.
            yield Diagnostic(
                code="W202",
                severity=Severity.WARNING,
                message="%s targets line %d, which does not exist; control "
                "lands on line %d" % (verb, dst, resolved),
                line=src,
                detail=(
                    "The panel redirects to the next existing line, so this "
                    "runs today. Numbering a block header %d and its first "
                    "statement %d is a common idiom and may be exactly what "
                    "was meant -- but nothing records that intent, and "
                    "inserting a line anywhere in between silently moves where "
                    "control goes. Desigo CC reports it as a warning, not an "
                    "error, and saves the program." % (dst, resolved)
                ),
                manual="Desigo CC PPCL Editor, Common Compiler Errors and "
                "Warnings; Insight Program Editor, Tools > Non-Existent "
                "GOTOs; 125-1896 Chapter 4, GOTO",
                suggestion="Point the %s at line %d explicitly, so the intent "
                "survives the next edit." % (verb, resolved),
            )


@rule("W202", "Branch target does not exist and is silently redirected", Severity.WARNING)
def _w202_placeholder(ctx):
    # Emitted by missing_branch_target; registered so the code appears in the
    # rule catalog and can be suppressed by name.
    return ()


@rule("W203", "GOTO transfers control backwards", Severity.ERROR)
def backward_goto(ctx):
    """The Desigo CC compiler rejects this outright.

    Its error text is precise about the one exception: "backwards GOTO found.
    With the exception of the last GOTO in the program, there was a GOTO found
    that refers to an earlier line number." So the final GOTO may close the
    main loop; every other backward branch fails to compile.
    """
    a = ctx.analysis

    gotos = []
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if isinstance(stmt, Goto):
                gotos.append((ln.number, stmt))
    if not gotos:
        return
    last_goto_line = max(number for number, _ in gotos)

    for number, stmt in gotos:
        if stmt.target > number:
            continue
        resolved = a.resolve_target(stmt.target)
        if resolved is None:
            continue

        if number == last_goto_line:
            yield Diagnostic(
                code="W203",
                severity=Severity.INFO,
                message="GOTO %d at line %d closes the main program loop"
                % (stmt.target, number),
                line=number,
                detail="This is the last GOTO in the program, which is the one "
                "backward branch the compiler permits. On a PXC.A it is also "
                "what marks the end of the program cycle and restarts the "
                "cycle time calculation.",
                manual="Desigo CC PPCL Editor, Common Compiler Errors; "
                "A6V10374898 Ch.1, PPCL Program Design Guidelines",
            )
            continue

        yield _d(
            "W203",
            Severity.ERROR,
            "GOTO %d at line %d jumps backwards and is not the last GOTO in "
            "the program" % (stmt.target, number),
            number,
            detail="The compiler reports \"backwards GOTO found\" and refuses "
            "to save the program. Only the final GOTO (here, line %d) may "
            "branch to an earlier line. A backward branch anywhere else can "
            "also trap execution in an inner loop, starving every line outside "
            "it. On a PXC.A there is a further consequence: jumping backwards "
            "signals the END OF THE PROGRAM CYCLE and restarts the cycle time "
            "calculation, so an inner backward branch does not merely starve "
            "the rest of the program -- it cuts the pass short every time it "
            "is taken." % last_goto_line,
            manual="Desigo CC PPCL Editor, Common Compiler Errors; "
            "125-1896 Chapter 2, PPCL guidelines",
            suggestion="Restructure so the jump goes to a higher line number, "
            "or move this branch so it becomes the program's last GOTO.",
        )


@rule("W204", "GOTO targets a comment line", Severity.WARNING)
def goto_to_comment(ctx):
    a = ctx.analysis
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, (Goto, Gosub)):
                continue
            target = a.line_at(stmt.target)
            if target is None or not target.is_comment:
                continue
            is_goto = isinstance(stmt, Goto)
            verb = "GOTO" if is_goto else "GOSUB"
            # Pointing a GOSUB at a comment header that labels the subroutine is
            # a widespread and readable convention, so it is noted rather than
            # warned about. The manual's prohibition names GOTO specifically.
            severity = Severity.WARNING if is_goto else Severity.INFO
            yield Diagnostic(
                code="W204",
                severity=severity,
                message="%s %d targets a comment line" % (verb, stmt.target),
                line=ln.number,
                detail="Execution continues at the next executable line, so this "
                "works today. It is fragile: deleting or renumbering the comment "
                "moves where control lands.",
                manual="Chapter 4, GOTO",
            )


@rule("E205", "Time-based command never re-executes", Severity.ERROR)
def time_command_outside_loop(ctx):
    """A LOOP/TOD/WAIT/SAMPLE that runs once at startup and never again.

    This is the defect the manual's "evaluate through every pass" guideline
    exists to prevent, and it is invisible on the panel: the command loads
    fine, runs once, and then quietly stops controlling anything.
    """
    a = ctx.analysis
    if not a.steady_state:
        return  # no loop at all; W207 covers that case
    for ln in ctx.program.lines:
        if ln.number in a.steady_state or ln.number not in a.reachable:
            continue
        if ln.number in a.subroutine_lines:
            continue  # E213 handles time-based commands in subroutines
        for stmt in substatements(ln.stmt):
            name = None
            if isinstance(stmt, Sampled):
                name = "SAMPLE"
            elif isinstance(stmt, CommandCall) and stmt.name in spec.TIME_BASED_COMMANDS:
                name = stmt.name
            if not name:
                continue
            yield _d(
                "E205",
                Severity.ERROR,
                "%s at line %d executes once at startup and is then never "
                "reached again" % (name, ln.number),
                ln.number,
                detail="The repeating loop of this program covers lines %d-%d. "
                "This line sits outside it, so after the first pass the %s stops "
                "being evaluated. The manual requires time-based commands to be "
                "evaluated through every pass."
                % (min(a.steady_state), max(a.steady_state), name),
                manual="Chapter 2, PPCL guidelines",
                suggestion="Move the %s inside the main loop." % name,
            )


@rule("W207", "Program never reaches its last line", Severity.WARNING)
def last_line_unreachable(ctx):
    a = ctx.analysis
    if not a.numbers or a.last_line_reachable:
        return

    last = a.index[a.numbers[-1]]
    # A trailing comment the loop skips is cosmetic, not a defect, provided
    # the program does have a steady-state loop.
    if a.steady_state and last.is_comment:
        yield Diagnostic(
            code="W207",
            severity=Severity.STYLE,
            message="the last line (%d) is a comment the main loop never reaches"
            % last.number,
            line=last.number,
            detail="Harmless: the program cycles through lines %d-%d instead of "
            "wrapping past the end. Worth knowing because the automatic wrap "
            "from the last line to the first never happens here."
            % (min(a.steady_state), max(a.steady_state)),
            manual="Chapter 2, PPCL rules",
        )
        return

    detail = (
        "PPCL returns control to the first line once the last line is reached. "
        "Here that wrap never happens."
    )
    if not a.steady_state:
        detail += (
            " No repeating loop was found at all, so this program runs once and "
            "then stops controlling anything."
        )
    yield _d(
        "W207",
        Severity.ERROR if not a.steady_state else Severity.WARNING,
        "the last line (%d) is not reachable on a normal pass" % a.numbers[-1],
        a.numbers[-1],
        detail=detail,
        manual="Chapter 2, PPCL rules and guidelines",
        suggestion="Route the main loop so it falls through the last line.",
    )


@rule("W206", "Unreachable code", Severity.WARNING)
def unreachable_lines(ctx):
    a = ctx.analysis
    dead = []
    for ln in ctx.program.lines:
        if ln.is_comment:
            continue
        if ln.number in a.reachable or ln.number in a.subroutine_lines:
            continue
        dead.append(ln)

    if not dead:
        return

    # Report contiguous runs as one finding to keep the report readable.
    for start, end, lines in _runs(dead):
        span = "line %d" % start if start == end else "lines %d-%d" % (start, end)
        yield _d(
            "W206",
            Severity.WARNING,
            "%s cannot be reached from the top of the program" % span,
            start,
            detail="%d executable line(s). Either an ACT/ENABLE elsewhere brings "
            "them in, or this is dead code left from an earlier revision."
            % len(lines),
            suggestion="Delete it, or confirm an ACT/ENABLE activates it.",
        )


def _runs(lines):
    """Group consecutive Line objects into (first, last, members) runs."""
    if not lines:
        return
    run = [lines[0]]
    for prev, cur in zip(lines, lines[1:]):
        if cur.number - prev.number <= 20:
            run.append(cur)
        else:
            yield run[0].number, run[-1].number, run
            run = [cur]
    yield run[0].number, run[-1].number, run


# --------------------------------------------------------------------------
# Subroutines
# --------------------------------------------------------------------------


@rule("E210", "Subroutine has no RETURN", Severity.ERROR)
def subroutine_without_return(ctx):
    a = ctx.analysis
    for entry, sub in sorted(a.subroutines.items()):
        if sub.returns:
            continue
        callers = ", ".join(str(c) for c in sorted(set(sub.callers)))
        yield _d(
            "E210",
            Severity.ERROR,
            "the subroutine at line %d never reaches a RETURN" % entry,
            entry,
            detail="Called from line(s) %s. Without a RETURN, execution runs on "
            "into whatever follows instead of going back to the caller."
            % callers,
            manual="Chapter 4, GOSUB and RETURN",
            suggestion="End the subroutine with a RETURN.",
        )


@rule("W211", "Subroutine can fall through past its RETURN", Severity.WARNING)
def subroutine_falls_through(ctx):
    a = ctx.analysis
    for entry, sub in sorted(a.subroutines.items()):
        if not sub.returns or not sub.falls_through:
            continue
        yield _d(
            "W211",
            Severity.WARNING,
            "the subroutine at line %d has a path that runs past its RETURN"
            % entry,
            entry,
            detail="Some route through the body reaches the end of the program "
            "without hitting RETURN at line(s) %s."
            % ", ".join(str(r) for r in sub.returns),
            manual="Chapter 4, GOSUB",
        )


@rule("E212", "RETURN outside any subroutine", Severity.ERROR)
def stray_return(ctx):
    a = ctx.analysis
    for ln in ctx.program.lines:
        has_return = any(isinstance(s, Return) for s in substatements(ln.stmt))
        if not has_return:
            continue
        if ln.number in a.subroutine_lines:
            continue
        # A program with no GOSUB anywhere is a template or a library fragment
        # -- Siemens' own MEC100K program ships exactly this way, with a
        # comment telling the engineer where to add their GOSUB statements. It
        # loads and runs; the subroutine simply lies dormant until called. That
        # is worth flagging but it is not the compiler error that ERROR claims.
        # A program that *does* use GOSUB elsewhere and still has an unreachable
        # RETURN is a real defect, so that keeps its severity.
        uses_gosub = bool(a.subroutines)
        yield _d(
            "E212",
            Severity.ERROR if uses_gosub else Severity.WARNING,
            "RETURN at line %d is not inside any subroutine" % ln.number,
            ln.number,
            detail=(
                "No GOSUB in this program reaches this line. On a normal pass "
                "the panel has no caller to return to."
                if uses_gosub
                else "This program contains no GOSUB at all, so the block ending "
                "here is never entered. That is how a reusable template ships "
                "-- the caller is added later -- but as written the code is "
                "dormant."
            ),
            manual="Chapter 4, RETURN",
            suggestion="Remove the RETURN, or add the GOSUB that should call it.",
        )


@rule("E213", "Time-based command inside a subroutine", Severity.ERROR)
def time_command_in_subroutine(ctx):
    a = ctx.analysis
    for ln in ctx.program.lines:
        if ln.number not in a.subroutine_lines:
            continue
        for stmt in substatements(ln.stmt):
            name = None
            if isinstance(stmt, Sampled):
                name = "SAMPLE"
            elif isinstance(stmt, CommandCall) and stmt.name in spec.SUBROUTINE_UNSAFE:
                name = stmt.name
            if not name:
                continue
            yield _d(
                "E213",
                Severity.ERROR,
                "%s is used inside a subroutine" % name,
                ln.number,
                detail="The manual states time-based commands such as LOOP, "
                "SAMPLE, TOD and WAIT must not be used inside a subroutine. A "
                "subroutine is not guaranteed to be evaluated on every pass, "
                "which is what these commands require.",
                manual="Chapter 4, GOSUB (Notes)",
                suggestion="Move the %s into the main line of the program." % name,
            )


@rule("E214", "GOSUB used as the target of an IF", Severity.ERROR)
def gosub_in_if(ctx):
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, If):
                continue
            for branch, label in ((stmt.then_stmt, "THEN"), (stmt.else_stmt, "ELSE")):
                if isinstance(branch, Gosub):
                    yield _d(
                        "E214",
                        Severity.ERROR,
                        "GOSUB appears in the %s clause of an IF" % label,
                        ln.number,
                        detail="The manual states a GOSUB command cannot be used "
                        "in an IF/THEN/ELSE command.",
                        manual="Chapter 4, GOSUB (Notes) and IF/THEN/ELSE (Notes)",
                        suggestion="Invert the test and branch past the GOSUB "
                        "with a GOTO, then call the subroutine unconditionally.",
                    )


@rule("W215", "Time-based command as the target of an IF", Severity.WARNING)
def time_command_in_if(ctx):
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, If):
                continue
            for branch, label in ((stmt.then_stmt, "THEN"), (stmt.else_stmt, "ELSE")):
                name = None
                if isinstance(branch, Sampled):
                    name = "SAMPLE"
                elif isinstance(branch, CommandCall) and branch.name in spec.IF_TARGET_UNSAFE:
                    name = branch.name
                if not name:
                    continue
                yield _d(
                    "W215",
                    Severity.WARNING,
                    "%s appears in the %s clause of an IF" % (name, label),
                    ln.number,
                    detail="The manual states time-based commands such as WAIT "
                    "and TODMOD should not be used directly as the THEN or ELSE "
                    "action, because they are then not evaluated every pass.",
                    manual="Chapter 4, IF/THEN/ELSE (Notes)",
                )


@rule("E216", "GOSUB passes more than 15 arguments", Severity.ERROR)
def gosub_arg_limit(ctx):
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if isinstance(stmt, Gosub) and len(stmt.args) > 15:
                yield _d(
                    "E216",
                    Severity.ERROR,
                    "GOSUB passes %d arguments; the limit is 15 ($ARG1-$ARG15)"
                    % len(stmt.args),
                    ln.number,
                    manual="Chapter 4, GOSUB",
                )


@rule("W217", "Subroutine is defined but never called", Severity.WARNING)
def uncalled_subroutine(ctx):
    # A subroutine only exists in the analysis if something GOSUBs to it, so
    # this rule instead looks for RETURN-terminated regions no GOSUB reaches.
    # E212 covers the stray-RETURN case; nothing extra to report here.
    return ()


# --------------------------------------------------------------------------
# Power restart
# --------------------------------------------------------------------------


@rule("W220", "ONPWRT is not the first statement", Severity.WARNING)
def onpwrt_placement(ctx):
    a = ctx.analysis
    first_exec = None
    for ln in ctx.program.lines:
        if ln.is_executable:
            first_exec = ln
            break
    if first_exec is None:
        return

    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not (isinstance(stmt, CommandCall) and stmt.name == "ONPWRT"):
                continue
            # LOCAL declarations legitimately precede ONPWRT.
            preceding = [
                p
                for p in ctx.program.lines
                if p.is_executable and p.number < ln.number
            ]
            offenders = [
                p
                for p in preceding
                if not (
                    isinstance(p.stmt, CommandCall)
                    and p.stmt.name in ("LOCAL", "DEFINE")
                )
            ]
            if offenders:
                yield _d(
                    "W220",
                    Severity.WARNING,
                    "ONPWRT at line %d is preceded by %d executable line(s)"
                    % (ln.number, len(offenders)),
                    ln.number,
                    detail="The manual recommends ONPWRT be the first command in "
                    "the program, because execution returns to the program's "
                    "first line after a power failure. First offending line: %d."
                    % offenders[0].number,
                    manual="Chapter 4, ONPWRT",
                )


@rule("W221", "More than one ONPWRT in a program", Severity.WARNING)
def duplicate_onpwrt(ctx):
    found = [
        ln.number
        for ln in ctx.program.lines
        for stmt in substatements(ln.stmt)
        if isinstance(stmt, CommandCall) and stmt.name == "ONPWRT"
    ]
    if len(found) > 1:
        yield _d(
            "W221",
            Severity.WARNING,
            "%d ONPWRT commands in one program (lines %s)"
            % (len(found), ", ".join(str(f) for f in found)),
            found[0],
            detail="Only the one that executes first has any effect.",
            manual="Chapter 4, ONPWRT",
        )
