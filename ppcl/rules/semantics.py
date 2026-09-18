"""Semantic rules: parameter values, point typing, priority hygiene."""

from __future__ import annotations

from .. import spec
from ..analyzer import expr_refs, substatements, walk_expr
from ..ast_nodes import (
    Assignment,
    BinOp,
    CommandCall,
    FuncCall,
    Gosub,
    If,
    Num,
    PriorityRef,
    Ref,
    Sampled,
    TimeLit,
    UnaryOp,
)
from ..diagnostics import Diagnostic, Severity
from ..linter import rule


def _d(code, sev, msg, line=None, **kw):
    return Diagnostic(code=code, severity=sev, message=msg, line=line, **kw)


def _calls(ctx, name):
    """Yield (line, CommandCall) for every call to ``name``."""
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if isinstance(stmt, CommandCall) and stmt.name == name:
                yield ln, stmt


def _num(arg):
    return arg.value if isinstance(arg, Num) else None


def _as_literal(use):
    """Render a point name the way it must be written in PPCL source."""
    bare = use.name.lstrip("$")
    if use.quoted or len(bare) > spec.UNQUOTED_NAME_MAX or not bare.isalnum():
        return '"%s"' % use.name
    return use.name


# --------------------------------------------------------------------------
# Command-specific value checks
# --------------------------------------------------------------------------


@rule("E301", "TABLE x coordinates are not in ascending order", Severity.ERROR)
def table_ascending(ctx):
    for ln, call in _calls(ctx, "TABLE"):
        pairs = call.args[2:]
        xs = []
        for i in range(0, len(pairs) - 1, 2):
            xs.append((_num(pairs[i]), pairs[i]))
        known = [(v, node) for v, node in xs if v is not None]
        for (a, _), (b, node) in zip(known, known[1:]):
            if b <= a:
                yield _d(
                    "E301",
                    Severity.ERROR,
                    "TABLE x values must ascend; %s follows %s"
                    % (node.raw, ("%g" % a)),
                    ln.number,
                    detail="The manual requires x3 > x2 > x1. Out-of-order "
                    "breakpoints make the interpolation undefined.",
                    manual="Chapter 4, TABLE",
                )
                break


@rule("E302", "TODMOD day mode is not 1, 2, 4 or 8", Severity.ERROR)
def todmod_modes(ctx):
    days = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday")
    for ln, call in _calls(ctx, "TODMOD"):
        for i, arg in enumerate(call.args[:7]):
            v = _num(arg)
            if v is None:
                continue
            if int(v) not in (1, 2, 4, 8):
                extra = ""
                if int(v) == 16:
                    extra = (" Mode 16 is the holiday schedule and is set "
                             "automatically by HOLIDA; it must not be entered "
                             "in TODMOD.")
                yield _d(
                    "E302",
                    Severity.ERROR,
                    "TODMOD %s mode is %g; valid values are 1, 2, 4 or 8"
                    % (days[i] if i < 7 else "day %d" % i, v),
                    ln.number,
                    detail="1=normal, 2=extended, 4=shortened, 8=weekend." + extra,
                    manual="Chapter 4, TODMOD",
                )


@rule("W303", "TOD/TODSET/SSTO mode is not a sum of 1, 2, 4, 8, 16", Severity.WARNING)
def schedule_mode_bits(ctx):
    for name in ("TOD", "TODSET", "SSTO"):
        idx = 1 if name == "SSTO" else 0
        for ln, call in _calls(ctx, name):
            if idx >= len(call.args):
                continue
            v = _num(call.args[idx])
            if v is None:
                continue
            mode = int(v)
            if mode <= 0 or mode > 31:
                yield _d(
                    "W303",
                    Severity.WARNING,
                    "%s mode %d is outside the range a sum of 1/2/4/8/16 can "
                    "produce (1-31)" % (name, mode),
                    ln.number,
                    manual="Chapter 4, %s" % name,
                )


@rule("W304", "TOD mode 16 used without any HOLIDA command", Severity.WARNING)
def holiday_mode_without_holida(ctx):
    has_holida = any(True for _ in _calls(ctx, "HOLIDA"))
    if has_holida:
        return
    for name in ("TOD", "TODSET"):
        for ln, call in _calls(ctx, name):
            v = _num(call.args[0]) if call.args else None
            if v is None:
                continue
            if int(v) & 16:
                yield _d(
                    "W304",
                    Severity.WARNING,
                    "%s uses holiday mode 16 but this program defines no "
                    "HOLIDA dates" % name,
                    ln.number,
                    detail="Mode 16 only ever matches on a date listed in a "
                    "HOLIDA command, or in the panel's own TOD calendar.",
                    manual="Chapter 4, HOLIDA and TOD",
                )


@rule("E305", "HOLIDA month or day is out of range", Severity.ERROR)
def holida_dates(ctx):
    for ln, call in _calls(ctx, "HOLIDA"):
        for i in range(0, len(call.args) - 1, 2):
            month = _num(call.args[i])
            day = _num(call.args[i + 1])
            if month is not None and not (1 <= int(month) <= 12):
                yield _d(
                    "E305",
                    Severity.ERROR,
                    "HOLIDA month %g is not in 1-12" % month,
                    ln.number,
                    manual="Chapter 4, HOLIDA",
                )
            if day is not None and not (1 <= int(day) <= 31):
                yield _d(
                    "E305",
                    Severity.ERROR,
                    "HOLIDA day %g is not in 1-31" % day,
                    ln.number,
                    manual="Chapter 4, HOLIDA",
                )


@rule("W306", "HOLIDA or TODMOD appears after a TOD/TODSET command", Severity.WARNING)
def holida_ordering(ctx):
    setup = [ln.number for ln, _ in _calls(ctx, "HOLIDA")]
    setup += [ln.number for ln, _ in _calls(ctx, "TODMOD")]
    users = [ln.number for ln, _ in _calls(ctx, "TOD")]
    users += [ln.number for ln, _ in _calls(ctx, "TODSET")]
    if not setup or not users:
        return
    first_user = min(users)
    late = sorted(n for n in setup if n > first_user)
    for n in late:
        yield _d(
            "W306",
            Severity.WARNING,
            "HOLIDA/TODMOD at line %d comes after the TOD command at line %d"
            % (n, first_user),
            n,
            detail="The manual requires HOLIDA and TODMOD to precede any TOD or "
            "TODSET command for the schedule to operate correctly.",
            manual="Chapter 4, HOLIDA and TODMOD",
            suggestion="Move the HOLIDA/TODMOD definitions above line %d."
            % first_user,
        )


@rule("E307", "DC duty-cycle pattern is malformed", Severity.ERROR)
def dc_pattern(ctx):
    for ln, call in _calls(ctx, "DC"):
        for i in range(1, len(call.args), 2):
            v = _num(call.args[i])
            if v is None:
                continue
            raw = call.args[i].raw
            if "." in raw:
                yield _d(
                    "E307",
                    Severity.ERROR,
                    "DC pattern %s must be a 4-digit integer" % raw,
                    ln.number,
                    manual="Chapter 4, DC",
                )
                continue
            digits = raw.lstrip("0") or "0"
            if len(digits) > 4:
                yield _d(
                    "E307",
                    Severity.ERROR,
                    "DC pattern %s has %d digits; exactly 4 are used (one per "
                    "15-minute segment)" % (raw, len(digits)),
                    ln.number,
                    manual="Chapter 4, DC",
                )
            bad = [c for c in digits if c not in "01234567"]
            if bad:
                yield _d(
                    "E307",
                    Severity.ERROR,
                    "DC pattern %s contains the digit(s) %s; each digit must be "
                    "0-7" % (raw, ",".join(sorted(set(bad)))),
                    ln.number,
                    detail="Each digit encodes three 5-minute ON/OFF slots, so "
                    "only 0 through 7 are meaningful.",
                    manual="Chapter 4, DC, Table 4-1",
                )


@rule("E308", "PDLDAT timing value out of range", Severity.ERROR)
def pdldat_ranges(ctx):
    for ln, call in _calls(ctx, "PDLDAT"):
        if len(call.args) < 5:
            continue
        minon = _num(call.args[1])
        minoff = _num(call.args[2])
        maxoff = _num(call.args[3])
        if minon is not None and minon >= 546:
            yield _d(
                "E308", Severity.ERROR,
                "PDLDAT minon is %g; it must be less than 546 minutes" % minon,
                ln.number, manual="Chapter 4, PDLDAT",
            )
        if minoff is not None and minoff >= 546:
            yield _d(
                "E308", Severity.ERROR,
                "PDLDAT minoff is %g; it must be less than 546 minutes" % minoff,
                ln.number, manual="Chapter 4, PDLDAT",
            )
        if minoff is not None and maxoff is not None and maxoff > minoff + 546:
            yield _d(
                "E308", Severity.ERROR,
                "PDLDAT maxoff is %g; the maximum allowed is minoff + 546 = %g"
                % (maxoff, minoff + 546),
                ln.number, manual="Chapter 4, PDLDAT",
            )


@rule("E309", "TIMAVG sample count out of range", Severity.ERROR)
def timavg_samples(ctx):
    for ln, call in _calls(ctx, "TIMAVG"):
        if len(call.args) < 3:
            continue
        v = _num(call.args[2])
        if v is None:
            continue
        if not (1 <= int(v) <= 10):
            yield _d(
                "E309",
                Severity.ERROR,
                "TIMAVG samples is %g; it must be an integer from 1 to 10" % v,
                ln.number,
                manual="Chapter 4, TIMAVG",
            )


@rule("W310", "LOOP tuning parameters look inconsistent", Severity.WARNING)
def loop_parameters(ctx):
    for ln, call in _calls(ctx, "LOOP"):
        if len(call.args) < 12:
            continue
        bias, lo, hi = (_num(call.args[8]), _num(call.args[9]), _num(call.args[10]))
        st = _num(call.args[7])
        if lo is not None and hi is not None and lo >= hi:
            yield _d(
                "E310",
                Severity.ERROR,
                "LOOP low limit (%g) is not below the high limit (%g)" % (lo, hi),
                ln.number,
                manual="Chapter 4, LOOP",
            )
        elif bias is not None and lo is not None and hi is not None:
            if not (lo <= bias <= hi):
                yield _d(
                    "W310",
                    Severity.WARNING,
                    "LOOP bias %g lies outside the output range %g to %g"
                    % (bias, lo, hi),
                    ln.number,
                    detail="The manual states the bias value should always be "
                    "between the high and low value.",
                    manual="Chapter 4, LOOP",
                )
        if st is not None and st < 1:
            yield _d(
                "W310",
                Severity.WARNING,
                "LOOP sample time is %g; the minimum is 1 second" % st,
                ln.number,
                manual="Chapter 4, LOOP",
            )


@rule("E310", "LOOP output limits inverted", Severity.ERROR)
def _e310_placeholder(ctx):
    return ()


@rule("E311", "SSTO zone number out of range", Severity.ERROR)
def ssto_zone(ctx):
    for name in ("SSTO", "SSTOCO"):
        for ln, call in _calls(ctx, name):
            if not call.args:
                continue
            v = _num(call.args[0])
            if v is None:
                continue
            if not (1 <= int(v) <= 5):
                yield _d(
                    "E311",
                    Severity.ERROR,
                    "%s zone is %g; valid zones are 1 to 5" % (name, v),
                    ln.number,
                    manual="Chapter 4, %s" % name,
                )


@rule("W312", "PDLSET set-point times are not ascending", Severity.WARNING)
def pdlset_times(ctx):
    for ln, call in _calls(ctx, "PDLSET"):
        times = []
        for i in range(3, len(call.args), 2):
            arg = call.args[i]
            if isinstance(arg, TimeLit):
                times.append((arg.as_decimal_hours, arg.raw))
            elif isinstance(arg, Num):
                times.append((arg.value, arg.raw))
        for (a, _), (b, raw) in zip(times, times[1:]):
            if b <= a:
                yield _d(
                    "W312",
                    Severity.WARNING,
                    "PDLSET times must be in ascending order; %s does not follow "
                    "the previous time" % raw,
                    ln.number,
                    manual="Chapter 4, PDLSET",
                )
                break
        if len(times) < 2:
            yield _d(
                "W312",
                Severity.WARNING,
                "PDLSET has fewer than two set point/time pairs",
                ln.number,
                detail="The manual requires at least two set point/time "
                "definitions per day or reports will not generate.",
                manual="Chapter 4, PDLSET",
            )


@rule("E313", "SAMPLE wraps another timing command", Severity.ERROR)
def nested_timing(ctx):
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, Sampled) or stmt.statement is None:
                continue
            inner = stmt.statement
            name = None
            if isinstance(inner, Sampled):
                name = "SAMPLE"
            elif isinstance(inner, CommandCall) and inner.name in spec.TIME_BASED_COMMANDS:
                name = inner.name
            if name:
                yield _d(
                    "E313",
                    Severity.ERROR,
                    "SAMPLE contains %s, which has its own timing function"
                    % name,
                    ln.number,
                    detail="The manual states the statement following SAMPLE must "
                    "not include its own timing function (WAIT, PDL, TOD, TIMAVG, "
                    "LOOP, SSTO, or another SAMPLE).",
                    manual="Chapter 4, SAMPLE",
                )


@rule("E314", "Statement uses too many operands", Severity.ERROR)
def operand_limit(ctx):
    limit = spec.MAX_OPERANDS.get(ctx.firmware, spec.MAX_IF_OPERANDS)
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, If):
                continue
            operands = sum(
                1
                for node in walk_expr(stmt.cond)
                if isinstance(node, (Ref, Num, TimeLit))
            )
            if operands > limit:
                yield _d(
                    "E314",
                    Severity.ERROR,
                    "IF condition uses %d operands; the maximum for %s firmware "
                    "is %d" % (operands, ctx.firmware.value, limit),
                    ln.number,
                    detail="The published limits differ: 125-1896 Rev. 5 states "
                    "13 operands, while the Desigo CC PPCL Editor states 16 "
                    "operands and 32 operators per statement.",
                    manual="Chapter 4, IF/THEN/ELSE; Desigo CC PPCL Editor Overview",
                    suggestion="Split the test across two lines, holding the "
                    "intermediate result in a local variable.",
                )


@rule("E315", "Statement uses more than 32 operators", Severity.ERROR)
def operator_limit(ctx):
    """The Desigo CC compiler rejects a statement with more than 32 operators.

    Its counting rule is unusual and is followed exactly here: "An expression
    adds one operator for each arithmetic operator, relational operator,
    logical operator, point reference, and value constant." So point names and
    literals count toward the operator budget as well as the operand budget.
    """
    for ln in ctx.program.lines:
        count = 0
        for stmt in substatements(ln.stmt):
            expressions = []
            if isinstance(stmt, If):
                expressions.append(stmt.cond)
            elif isinstance(stmt, Assignment):
                expressions.append(stmt.expr)
            elif isinstance(stmt, CommandCall):
                expressions.extend(stmt.args)
            for expr in expressions:
                count += sum(
                    1
                    for node in walk_expr(expr)
                    if isinstance(node, (BinOp, Ref, Num, TimeLit))
                )
        if count > spec.MAX_OPERATORS:
            yield _d(
                "E315",
                Severity.ERROR,
                "statement uses %d operators; the maximum is %d"
                % (count, spec.MAX_OPERATORS),
                ln.number,
                manual="Desigo CC engineering help, PPCL Editor Overview",
                suggestion="Break the expression across several lines using a "
                "local variable for the intermediate result.",
            )


# --------------------------------------------------------------------------
# Locals
# --------------------------------------------------------------------------


@rule("E320", "Local variable used without a LOCAL declaration", Severity.ERROR)
def undeclared_local(ctx):
    a = ctx.analysis
    seen = set()
    for use in a.uses:
        if not use.name.startswith("$"):
            continue
        bare = use.name[1:].upper()
        if spec.is_builtin_local(use.name) or spec.is_resident(use.name.upper()):
            continue
        if bare in a.declared_locals:
            continue
        key = (use.line, bare)
        if key in seen:
            continue
        seen.add(key)
        yield _d(
            "E320",
            Severity.ERROR,
            "$%s is used but never declared with LOCAL" % bare,
            use.line,
            detail="Program-local virtual points must be declared, for example "
            'LOCAL("%s").' % bare,
            manual="Chapter 4, LOCAL",
        )


@rule("W321", "LOCAL declared but never used", Severity.WARNING)
def unused_local(ctx):
    a = ctx.analysis
    used = {
        u.name[1:].upper()
        for u in a.uses
        if u.name.startswith("$") and u.kind != "declare"
    }
    for name in sorted(a.declared_locals - used):
        line = next(
            (u.line for u in a.uses if u.kind == "declare"
             and u.name.upper().lstrip("$") == name),
            None,
        )
        yield _d(
            "W321",
            Severity.WARNING,
            "local %s is declared but never referenced" % name,
            line,
            suggestion="Remove it from the LOCAL declaration.",
        )


@rule("W322", "Value read before anything writes it", Severity.WARNING)
def read_before_write(ctx):
    a = ctx.analysis
    for name in sorted({u.name.upper() for u in a.uses if u.name.startswith("$")}):
        if spec.is_builtin_local(name):
            continue
        reads = [u for u in a.uses if u.name.upper() == name and u.kind == "read"]
        writes = [u for u in a.uses if u.name.upper() == name and u.kind == "write"]
        if reads and not writes:
            yield _d(
                "W322",
                Severity.WARNING,
                "%s is read at line %d but never assigned anywhere in this program"
                % (name, reads[0].line),
                reads[0].line,
                detail="It will hold whatever the panel last left in it, or zero "
                "after a database load.",
            )


# --------------------------------------------------------------------------
# Priority hygiene
# --------------------------------------------------------------------------


def _elevated_writes(stmt):
    """{NAME: priority} written above @NONE by this one statement."""
    out = {}
    if isinstance(stmt, CommandCall) and stmt.priority is not None:
        prio = stmt.priority.name
        if spec.PRIORITY_RANK.get(prio, 0) > spec.PRIORITY_RANK["@NONE"]:
            for arg in stmt.args:
                if isinstance(arg, Ref):
                    out[arg.name.upper()] = prio
    return out


def _driven_every_pass(ctx):
    """Names written above @NONE on every pass of the main loop.

    Such a point is not *stranded* by the absence of a RELEAS -- something
    writes it again next pass -- so it is a different finding from one
    commanded on a path that ends. Two shapes count:

    * an unconditional command, and
    * a command in **both** the THEN and the ELSE of one IF,

    on a line that lies on a cycle of the control-flow graph and is not part
    of a subroutine body. A SAMPLE-wrapped statement does not count: that is
    the point of SAMPLE.
    """
    a = ctx.analysis
    names = set()
    for ln in ctx.program.lines:
        if ln.number not in a.steady_state or ln.number in a.subroutine_lines:
            continue
        stmt = ln.stmt
        if isinstance(stmt, Sampled):
            continue
        if isinstance(stmt, If):
            then_w = _elevated_writes(stmt.then_stmt)
            else_w = _elevated_writes(stmt.else_stmt) if stmt.else_stmt else {}
            names |= set(then_w) & set(else_w)
        else:
            names |= set(_elevated_writes(stmt))
    return names


@rule("W330", "Point commanded above PPCL priority is never released", Severity.WARNING)
def unreleased_priority(ctx):
    a = ctx.analysis
    elevated = {}
    for use in a.uses:
        if use.kind != "write" or not use.priority:
            continue
        if spec.PRIORITY_RANK.get(use.priority, 0) <= spec.PRIORITY_RANK["@NONE"]:
            continue
        elevated.setdefault(use.name.upper(), []).append(use)

    releases = {}
    for ln, call in _calls(ctx, "RELEAS"):
        prio = call.priority.name if call.priority else None
        for arg in call.args:
            if isinstance(arg, Ref):
                releases.setdefault(arg.name.upper(), []).append((ln.number, prio))

    driven = _driven_every_pass(ctx)

    for name, uses in sorted(elevated.items()):
        rel = releases.get(name, [])
        highest = max(spec.PRIORITY_RANK[u.priority] for u in uses)
        prio_name = spec.PRIORITY_ORDER[highest]
        if not rel and name in driven:
            yield _d(
                "W341",
                Severity.INFO,
                "%s is held at %s every pass, so an operator cannot keep it"
                % (uses[0].name, prio_name),
                uses[0].line,
                detail="Written at %s on every pass of the main loop and never "
                "released, so the point cannot strand -- but a command from an "
                "operator, a schedule or another program is overwritten on the "
                "next pass, within a second or two, with nothing to say why. "
                "Deliberate for a lamp test or a hard interlock; a surprise "
                "otherwise. Written at line(s) %s."
                % (prio_name, ", ".join(str(u.line) for u in uses)),
                manual="Chapter 3, Point priority",
                suggestion="If an operator should be able to take this point, "
                "command it at @NONE and let priority do its job.",
            )
            continue
        if not rel:
            yield _d(
                "W330",
                Severity.WARNING,
                "%s is commanded at %s but this program never releases it"
                % (uses[0].name, prio_name),
                uses[0].line,
                detail="A point left above NONE priority cannot be commanded by "
                "ordinary PPCL, by a schedule, or in most cases by an operator "
                "until something releases it. Commanded at line(s) %s."
                % ", ".join(str(u.line) for u in uses),
                manual="Chapter 3, Point priority; Chapter 4, RELEAS",
                suggestion="Add RELEAS(%s,%s) on the path that ends the "
                "condition." % (prio_name, _as_literal(uses[0])),
            )
            continue

        weak = [
            (line, prio)
            for line, prio in rel
            if prio is not None and spec.PRIORITY_RANK.get(prio, 0) < highest
        ]
        if weak and not any(p is None for _, p in rel):
            yield _d(
                "W331",
                Severity.WARNING,
                "%s is commanded at %s but only released at %s"
                % (uses[0].name, prio_name,
                   ", ".join(p for _, p in weak if p)),
                weak[0][0],
                detail="The manual states you must always release at a priority "
                "at least as high as the one the point is in, otherwise the "
                "release silently does nothing.",
                manual="Chapter 4, RELEAS (Notes)",
                suggestion="Release with RELEAS(%s,%s)."
                % (prio_name, _as_literal(uses[0])),
            )


@rule("W331", "Point released at a lower priority than it was commanded", Severity.WARNING)
def _w331_placeholder(ctx):
    return ()


@rule("W341", "Point held above PPCL priority on every pass", Severity.INFO)
def _w341_placeholder(ctx):
    return ()


@rule("W332", "Same point commanded from several places", Severity.WARNING)
def conflicting_writers(ctx):
    a = ctx.analysis
    # Only commands that actually drive a point's value can conflict. RELEAS,
    # alarm enables and COV enables change a point's handling, not its state.
    value_commands = {
        "ON", "OFF", "AUTO", "FAST", "SLOW", "SET", "STATE",
        "EMON", "EMOFF", "EMSET", "EMAUTO", "EMFAST", "EMSLOW",
        "TOD", "TODSET", "TABLE", "MAX", "MIN", "DBSWIT", "LOOP", "WAIT",
        "DC", "DCR",
    }
    # A statement that reads the point it writes is refining the value that is
    # already there, not competing for it. MAX to a floor followed by MIN to a
    # ceiling is the standard PPCL clamp and must not read as a conflict.
    reads_at = {}
    for use in a.uses:
        if use.kind == "read":
            reads_at.setdefault(use.line, set()).add(use.name.upper())

    by_point = {}
    for use in a.uses:
        if use.kind != "write" or use.context not in value_commands:
            continue
        if use.name.upper() in reads_at.get(use.line, ()):
            continue
        by_point.setdefault(use.name.upper(), []).append(use)

    guarded_lines = {
        ln.number
        for ln in ctx.program.lines
        if any(isinstance(s, If) for s in substatements(ln.stmt))
    }

    for name, uses in sorted(by_point.items()):
        commands = {u.context for u in uses}
        # Complementary pairs are the normal way to write PPCL.
        if commands <= {"ON", "OFF"} or commands <= {"FAST", "SLOW", "OFF"}:
            continue
        if commands <= {"AUTO", "ON", "OFF"}:
            continue
        if len(commands) < 2:
            continue

        lines = sorted({u.line for u in uses})
        # Two writers that both run unconditionally always fight. When at
        # least one sits behind an IF, the author has probably separated the
        # cases -- the override idiom (compute, then conditionally overrule)
        # looks exactly like this -- so it is noted rather than warned about.
        unguarded = [line for line in lines if line not in guarded_lines]
        all_guarded = len(unguarded) < 2
        yield Diagnostic(
            code="W332",
            severity=Severity.INFO if all_guarded else Severity.WARNING,
            message="%s is commanded by %s at lines %s"
            % (uses[0].name, "/".join(sorted(commands)),
               ", ".join(str(n) for n in lines)),
            line=lines[0],
            detail=(
                "At most one of these runs unconditionally, so this is "
                "probably a deliberate override. Confirm the guards really are "
                "exclusive and that the override comes last."
                if all_guarded
                else "Several commands drive this point and %d of them run "
                "unconditionally. Whichever executes last in the pass wins, so "
                "the outcome depends on line order rather than on logic."
                % len(unguarded)
            ),
            suggestion="Decide the point's state in one place, then command it "
            "once.",
        )


@rule("W333", "DC or DCR is not guarded by a conditional", Severity.WARNING)
def unguarded_duty_cycle(ctx):
    for name in ("DC", "DCR"):
        for ln in ctx.program.lines:
            for stmt in substatements(ln.stmt):
                if not (isinstance(stmt, CommandCall) and stmt.name == name):
                    continue
                guarded = any(isinstance(s, If) for s in substatements(ln.stmt))
                if guarded:
                    continue
                yield _d(
                    "W333",
                    Severity.WARNING,
                    "%s at line %d runs unconditionally" % (name, ln.number),
                    ln.number,
                    detail="%s commands at NONE priority, the same level ordinary "
                    "PPCL uses. The manual advises structuring the program with "
                    "IF/THEN/ELSE to prevent %s fighting other NONE-priority "
                    "commands on the same point." % (name, name),
                    manual="Chapter 4, %s (Notes)" % name,
                )


@rule("E316", "Command used on a point type it cannot control", Severity.ERROR)
def command_point_type(ctx):
    """Only fires when the point's type is known.

    ``spec`` has carried a ``point_types`` set on the commands that restrict
    their target since the beginning -- AUTO takes only the two On/Off/Auto
    types, FAST and SLOW only the speed types -- and until now nothing read
    it. It was documentation that no one could be wrong about, which is the
    same as documentation nobody checks.

    A point database is required: without a declared type there is nothing to
    compare, and guessing from a name would be worse than silence.
    """
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, CommandCall):
                continue
            cmd = spec.ALL.get(stmt.name)
            if cmd is None or not cmd.point_types:
                continue
            for arg in stmt.args:
                if not isinstance(arg, Ref) or arg.is_local:
                    continue
                ptype = ctx.point_type(arg.name)
                if ptype is None or ptype in cmd.point_types:
                    continue
                if ptype not in spec.POINT_TYPES:
                    continue        # a type this spec does not know
                yield _d(
                    "E316",
                    Severity.ERROR,
                    "%s is a %s point and %s controls only %s"
                    % (arg.name, ptype, stmt.name,
                       ", ".join(sorted(cmd.point_types))),
                    ln.number,
                    detail="%s. The panel rejects the command rather than "
                    "carrying it out on the wrong kind of output."
                    % spec.POINT_TYPES[ptype].description,
                    manual="Chapter 4, %s" % stmt.name,
                )


def _numeric_literal(node):
    """The value of a numeric literal, negated form included, else None."""
    if isinstance(node, Num):
        return node.value
    if isinstance(node, UnaryOp) and isinstance(node.operand, Num):
        return -node.operand.value if node.op == "-" else node.operand.value
    return None


@rule("W343", "SET re-commands the same point every pass on PXC.A",
      Severity.INFO)
def unguarded_set_on_pxc_a(ctx):
    """PXC.A's own guidance, and the analogue of W339 a generation later.

    "The SET command is not always resolved to the actual value, which may
    result in points being continuously commanded. To prevent unnecessary
    MS/TP traffic, use a deadband." -- Desigo PXC.A Web Interface User Guide
    (A6V12893115), PPCL Diagnostics.

    SET does not read the point back, so an unconditional SET issues a command
    on every pass whether or not the point already holds the value. Over an
    MS/TP trunk that is real traffic for no change in state.

    Deliberately narrow. It fires only on a SET with **no** conditional guard
    at all, on a line the main loop reaches every pass. A SET already inside
    an IF is left alone even when the condition is not a deadband, because at
    that point the engineer has thought about when it should run and this rule
    cannot read the thought. INFO, for the same reason W339 is: it is a cost,
    not a defect, and on a small trunk the cost may not matter.
    """
    if ctx.firmware is not spec.Firmware.PXC_A:
        return
    a = ctx.analysis
    for ln in ctx.program.lines:
        stmt = ln.stmt
        if not isinstance(stmt, CommandCall) or stmt.name != "SET":
            continue
        if ln.number not in a.steady_state or ln.number in a.subroutine_lines:
            continue
        target = None
        for arg in reversed(stmt.args):
            if isinstance(arg, Ref):
                target = arg.name
                break
        yield _d(
            "W343",
            Severity.INFO,
            "SET %sruns every pass with no guard, so the point is commanded "
            "again whether or not it has changed"
            % ("on %s " % target if target else ""),
            ln.number,
            detail="SET does not resolve against the point's current value, "
            "so an unguarded one issues a command on every cycle. Siemens' "
            "own remedy is a deadband: compute the difference between the "
            "point and the value you want, and SET only when that difference "
            "leaves the band.",
            manual="A6V12893115, PPCL Diagnostics (Continuously re-commanded "
            "points)",
            suggestion="Guard it: LOCAL a deadband and a difference, then "
            "IF the difference is outside the band THEN SET.",
        )


@rule("W342", "SSTO adjustment looks like a captured value, not an entered one",
      Severity.WARNING)
def ssto_adjustment_literal(ctx):
    """The panel writes its learned state into the statement you read back.

    Siemens: "When AST or ASP are entered as zero, the current adjustment
    value is displayed each time the command is displayed." So a program
    *displayed* from a panel shows the adjustment the zone has learned, in the
    place where a zero was entered -- and text exported that way carries a
    number nobody typed.

    Load it back and the zone stops tuning from zero and starts tuning from
    whatever the panel happened to have learned on export day. That is a
    silent behaviour change in a program that looks identical.

    Fires on a numeric literal; a point reference is the documented way to
    seed an adjustment deliberately and is left alone.
    """
    for ln, call in _calls(ctx, "SSTO"):
        for index, name in ((10, "AST"), (11, "ASP")):
            if index >= len(call.args):
                continue
            value = _numeric_literal(call.args[index])
            if value is None or value == 0:
                continue
            computed = value != int(value)
            yield _d(
                "W342",
                Severity.WARNING,
                "SSTO %s is %g, and a displayed SSTO shows the adjustment the "
                "zone has learned" % (name, value),
                ln.number,
                detail="Entered as zero, %s is where the panel prints the "
                "current self-tuning adjustment every time the statement is "
                "displayed. A non-zero literal here is therefore most likely "
                "a captured display rather than anything an engineer chose%s. "
                "Loading this text to a panel writes it back as a fixed entry, "
                "and the zone tunes from that offset instead of from zero."
                % (name, ", and a value carrying several decimal places is "
                   "almost certainly captured" if computed else ""),
                manual="Chapter 4, SSTO (Remarks)",
                suggestion="If you did not deliberately fix the adjustment, "
                "restore 0 -- or a virtual LAO, which is the documented way to "
                "seed one -- before loading this program.",
            )


@rule("W334", "Analog value compared for exact equality", Severity.WARNING)
def analog_equality(ctx):
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, If):
                continue
            for node in walk_expr(stmt.cond):
                if not isinstance(node, BinOp) or node.op not in (".EQ.", ".NE."):
                    continue
                for side, other in ((node.left, node.right), (node.right, node.left)):
                    if not isinstance(side, Ref) or not isinstance(other, Num):
                        continue
                    # A program-local variable holds exactly what this program
                    # put there, so comparing it for equality is safe.
                    if side.is_local:
                        continue
                    # 0 and 1 are flag and digital-state comparisons, not
                    # measurements.
                    if other.value in (0.0, 1.0):
                        continue
                    ptype = ctx.point_type(side.name)
                    analog_known = ptype in spec.ANALOG_TYPES if ptype else None
                    if analog_known is False:
                        continue
                    if analog_known is None and other.is_integer_literal:
                        # Without a point database this is too noisy to flag
                        # against integers; digital and counter points compare
                        # against integers legitimately.
                        continue
                    yield _d(
                        "W334",
                        Severity.WARNING,
                        "%s is compared to %s with %s"
                        % (side.name, other.raw, node.op),
                        ln.number,
                        detail="Analog inputs carry precise values, so an exact "
                        "comparison against a round number is usually false even "
                        "when the reading looks right.",
                        manual="Chapter 2, Equal to (.EQ.) Notes",
                        suggestion="Use a band: .GE./.LE. around the target, or "
                        "compare against a dead band.",
                    )
                    break


@rule("W335", "Qualified point reference used in a command Cross Trunk does not support",
      Severity.INFO)
def cross_trunk_command(ctx):
    """Cross Trunk supports a specific list of commands and no others.

    A colon-qualified name is the syntax for both an FLN subpoint and a
    path-qualified point on another BLN, and only the second is a Cross Trunk
    reference. This cannot be told apart without the panel topology, so it is
    reported at INFO: check it only if the reference really does cross a BLN.
    """
    reported = set()
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, CommandCall):
                continue
            if stmt.name in spec.CROSS_TRUNK_COMMANDS:
                continue
            if stmt.name in ("LOCAL", "DEFINE"):
                continue
            written = set()
            from ..analyzer import written_arg_indexes

            written = written_arg_indexes(stmt)
            for i, arg in enumerate(stmt.args):
                if i not in written or not isinstance(arg, Ref):
                    continue
                if ":" not in arg.name:
                    continue
                key = (ln.number, arg.name, stmt.name)
                if key in reported:
                    continue
                reported.add(key)
                yield _d(
                    "W335",
                    Severity.INFO,
                    "%s commands the qualified point %s, which Cross Trunk "
                    "does not support" % (stmt.name, arg.name),
                    ln.number,
                    detail="Siemens supports Cross Trunk commanding only from "
                    "%s, and states that although PPCL may allow a Cross Trunk "
                    "reference in other commands, it is strongly recommended "
                    "not to. This only applies if the point is on another BLN; "
                    "an FLN subpoint on this panel uses the same colon syntax "
                    "and is fine. Cross Trunk also issues at most one command "
                    "per second per point -- if several arrive in one second, "
                    "only the last is sent."
                    % ", ".join(sorted(spec.CROSS_TRUNK_COMMANDS)),
                    manual="Desigo CC engineering help, PPCL Editor - APOGEE "
                    "Cross Trunk",
                )


@rule("W336", "SETVAL writes a property that changes how a point behaves",
      Severity.WARNING)
def setval_dangerous_property(ctx):
    """SETVAL can reach properties that no ordinary command can.

    Commanding a point at ``@EMER`` is visible: the point shows the priority,
    an operator can see who owns it, and ``RELEAS`` gives it back. Writing
    ``@OoServe`` is not visible in that way at all -- the point simply stops
    tracking its input, still reads a value, and looks like nothing has been
    done to it. Writing ``@PrioArr`` bypasses the priority arbitration that
    every other command goes through.

    Neither is wrong to do; both are worth finding when a system is behaving
    inexplicably, which is exactly when nobody thinks to grep for SETVAL.
    """
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, CommandCall) or stmt.name != "SETVAL":
                continue
            if len(stmt.args) < 2:
                continue
            spec_arg = stmt.args[1]
            if isinstance(spec_arg, Num):
                number = spec.property_number(spec_arg.value)
            elif isinstance(spec_arg, Ref):
                number = spec.property_number(spec_arg.name)
            else:
                continue
            reason = spec.DANGEROUS_PROPERTIES.get(number)
            if reason is None:
                continue
            short = spec.property_name(number)
            targets = [a.name for a in stmt.args[2:] if isinstance(a, Ref)]
            yield _d(
                "W336",
                Severity.WARNING,
                "SETVAL writes @%s (%d) on %s, which %s"
                % (short, number,
                   ", ".join(targets[:4]) or "a point", reason),
                ln.number,
                source_line=ln.source_line,
                detail="Unlike an @-priority command this leaves no visible "
                "owner on the point, so the next person to troubleshoot it has "
                "nothing to find. Confirm the sequence really intends it, and "
                "that something puts the property back.",
                suggestion="If the intent is to override the point, command it "
                "at a priority and RELEAS it at the same priority instead.",
                manual="A6V10374898 PXC.A PPCL User Guide, SETVAL and "
                "Appendix B",
            )


@rule("W337", "SSTO calculates times that nothing reads", Severity.WARNING)
def ssto_output_unused(ctx):
    """SSTO only computes. TOD or TODSET has to act on the result.

    The command writes its calculated start and stop times into two virtual
    LAO points and stops there. If nothing reads those points the optimisation
    runs every pass, produces correct numbers, and changes nothing in the
    building -- with no error anywhere, because every statement involved is
    valid.

    This is the failure mode an SSTO installation actually has. It is worth a
    warning even though the program is legal.
    """
    read_names = {
        use.name.upper().strip('"')
        for use in ctx.analysis.uses
        if use.kind == "read"
    }
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, CommandCall) or stmt.name != "SSTO":
                continue
            # cst and csp are the third and fourth arguments.
            outputs = [
                arg for arg in stmt.args[2:4] if isinstance(arg, Ref)
            ]
            unused = [
                arg.name for arg in outputs
                if arg.name.upper().strip('"') not in read_names
            ]
            if not unused:
                continue
            yield _d(
                "W337",
                Severity.WARNING,
                "SSTO writes %s, which nothing in this program reads"
                % " and ".join(unused),
                ln.number,
                source_line=ln.source_line,
                detail="SSTO only calculates the optimum start and stop "
                "times. A TOD or TODSET command has to use them to command "
                "the equipment. As written the optimisation runs every pass "
                "and changes nothing.",
                suggestion="Feed the calculated time into the TOD or TODSET "
                "that schedules this zone, or remove the SSTO.",
                manual="A6V10374898 PXC.A PPCL User Guide, SSTO",
            )


@rule("W338", "LSQ2 curve fit is not backed by seven LSQDAT rows", Severity.WARNING)
def lsq2_row_count(ctx):
    """An XYZ least-squares fit is a fixed eight-line construct.

    "A total of 8 lines of PPCL is required for each calculation. LSQ2 defines
    the formula on the first line of PPCL, and is followed by 7 lines of
    LSQDAT commands which define the points whose values define the inputs."
    -- Insight Program Editor, LSQ2.

    Getting this wrong is invisible. The statements all compile, the panel
    runs them, and the chiller part-load model is simply built from the wrong
    number of rows. It surfaces months later as a plant that stages badly.
    """
    lsqdat_lines = sorted(
        ln.number
        for ln in ctx.program.lines
        for stmt in substatements(ln.stmt)
        if isinstance(stmt, CommandCall) and stmt.name == "LSQDAT"
    )
    for ln, stmt in _calls(ctx, "LSQ2"):
        if len(stmt.args) < 3:
            continue  # E111 already reports the argument count
        bounds = [a for a in stmt.args[-2:] if isinstance(a, Num)]
        if len(bounds) != 2:
            continue
        start, end = int(bounds[0].value), int(bounds[1].value)
        if start > end:
            yield _d(
                "W338",
                Severity.WARNING,
                "LSQ2 names the range %d-%d, which runs backwards"
                % (start, end),
                ln.number,
                source_line=ln.source_line,
                detail="The start line must come before the end line; as "
                "written the range is empty and no data reaches the fit.",
                manual="Insight Program Editor, LSQ2",
            )
            continue
        found = [n for n in lsqdat_lines if start <= n <= end]
        if len(found) == 7:
            continue
        yield _d(
            "W338",
            Severity.WARNING,
            "LSQ2 names lines %d-%d, which hold %d LSQDAT statement(s); the "
            "curve fit needs exactly 7" % (start, end, len(found)),
            ln.number,
            source_line=ln.source_line,
            detail="An XYZ least-squares curve fit is a fixed eight-line "
            "construct: the LSQ2 statement plus seven LSQDAT rows. Every "
            "statement here is legal on its own, so nothing reports this -- "
            "the chiller part-load model is just built from the wrong data.",
            suggestion=(
                "Add %d more LSQDAT row(s) inside the range."
                % (7 - len(found)) if len(found) < 7 else
                "Narrow the range, or move the extra LSQDAT statement(s) "
                "outside it."
            ),
            manual="Insight Program Editor, LSQ2",
        )


def _tests_a_priority(expr) -> bool:
    """True if this condition compares something against an @priority."""
    return any(
        isinstance(node, PriorityRef)
        or (isinstance(node, Ref) and node.name.startswith("@"))
        for node in walk_expr(expr)
    )


@rule("W339", "RELEAS runs every pass without testing the point's priority",
      Severity.INFO)
def unguarded_releas_in_the_main_loop(ctx):
    """On some firmware a repeated RELEAS floods the network with COVs.

    "Firmware Revision levels SCU 9.1, 10.1 to 12.1, and MBC 1.1 issue
    multiple release commands without regard for the point's current
    priority. This may issue multiple priority change of value commands
    across the network." -- Insight Program Editor, RELEAS.

    Siemens' own remedy is to test the point first, and the shape they give is
    ``IF(PT .NE. @NONE) THEN RELEAS(@EMER,PT)``. A release that is already a
    no-op costs nothing on the panel and a message on the trunk every pass.

    INFO, not a warning. On SCU 9.2/9.3/12.2 and later, and MBC 1.2 and later,
    the firmware suppresses the duplicates and a bare RELEAS is perfectly
    idiomatic. This is a "check your panel revisions" finding, not a defect.
    """
    a = ctx.analysis
    if not a.steady_state:
        return
    for ln in ctx.program.lines:
        if ln.number not in a.steady_state:
            continue  # runs once at startup; no storm to cause
        guarded = False
        releases = []
        for stmt in substatements(ln.stmt):
            if isinstance(stmt, If) and _tests_a_priority(stmt.cond):
                guarded = True
            elif isinstance(stmt, CommandCall) and stmt.name == "RELEAS":
                releases.append(stmt)
        if guarded or not releases:
            continue
        points = [
            arg.name for call in releases for arg in call.args
            if isinstance(arg, Ref) and not arg.name.startswith("@")
        ]
        if not points:
            continue
        shown = ", ".join(points[:3]) + (", ..." if len(points) > 3 else "")
        yield _d(
            "W339",
            Severity.INFO,
            "RELEAS(%s) is reached on every pass with nothing testing the "
            "current priority" % shown,
            ln.number,
            source_line=ln.source_line,
            detail="This matters on SCU firmware 9.1 and 10.1 through 12.1, "
            "and MBC 1.1, which issue the release regardless of whether the "
            "point is already at NONE and put a priority change-of-value on "
            "the network every pass. Later SCU and MBC revisions suppress it, "
            "and the defect is specific to those two families -- a PXC panel "
            "reporting a PME12xx/PME1300 or EPXC/PXME firmware revision is "
            "not affected. Check the panel's Firmware Revision before acting "
            "on this.",
            suggestion="Guard it the way the manual does: "
            "IF(%s .NE. @NONE) THEN RELEAS(...)." % points[0],
            manual="Insight Program Editor, RELEAS (Remarks and Example 3)",
        )



def _qualified(name: str) -> bool:
    """A colon- or bracket-qualified reference to something off this panel."""
    bare = name.strip('"')
    return ":" in bare or bare.startswith("[")


def _local_part(name: str) -> str:
    """The point name with any panel or node qualifier stripped.

    ``Panel2:TIME`` and ``[Bldg1]TIME`` both name TIME somewhere else.
    """
    bare = name.strip('"')
    if bare.startswith("[") and "]" in bare:
        bare = bare.split("]", 1)[1]
    return bare.split(":")[-1].upper()


def _expressions(stmt):
    """Every expression a statement evaluates."""
    if isinstance(stmt, If):
        return [stmt.cond]
    if isinstance(stmt, Assignment):
        return [stmt.expr]
    if isinstance(stmt, CommandCall):
        return list(stmt.args)
    return []


@rule("W340", "Device-local construct referenced across the network",
      Severity.INFO)
def device_local_across_network(ctx):
    """Resident points, status indicators and special functions are per panel.

    Each is documented the same way. Resident points: "Since each field panel
    maintains its own set of resident points, a resident point cannot be
    directly used across a network." Status indicators: "Since these points
    are related specifically to the functions of the device, you cannot
    directly use these points over the network." Special functions: "Special
    functions cannot be used over the network."

    INFO for the reason W335 is INFO -- a colon-qualified name is also how an
    FLN subpoint on *this* panel is written, and the two cannot be told apart
    without the panel topology.
    """
    reported = set()

    def report(line, what, name, kind):
        key = (line.number, name, kind)
        if key in reported:
            return None
        reported.add(key)
        return _d(
            "W340",
            Severity.INFO,
            "%s %s is reached through the off-panel reference %s"
            % (kind, what, name),
            line.number,
            source_line=line.source_line,
            detail="Resident points, point status indicators and special "
            "functions belong to the field panel that owns them and do not "
            "resolve over the network. If this reference really does cross to "
            "another panel it will not work; if the colon is an FLN subpoint "
            "on this panel, it is fine.",
            suggestion="Have the owning panel copy the value into a shared "
            "point, and read that instead.",
            manual="Insight Program Editor, What Are Resident Points / Point "
                   "Status Indicators / Special Functions",
        )

    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            for expr in _expressions(stmt):
                for node in walk_expr(expr):
                    if (isinstance(node, FuncCall)
                            and node.name in spec.SPECIAL_FUNCTIONS
                            and isinstance(node.arg, Ref)
                            and _qualified(node.arg.name)):
                        found = report(ln, node.name, node.arg.name,
                                       "special function")
                        if found:
                            yield found
                    if isinstance(node, BinOp):
                        for a, b in ((node.left, node.right),
                                     (node.right, node.left)):
                            if (isinstance(a, Ref) and isinstance(b, Ref)
                                    and _qualified(a.name)
                                    and b.name.strip('"').upper()
                                    in spec.STATUS_INDICATORS):
                                found = report(
                                    ln, b.name.strip('"').upper(), a.name,
                                    "status indicator")
                                if found:
                                    yield found
                    elif isinstance(node, Ref) and _qualified(node.name):
                        tail = _local_part(node.name)
                        if spec.is_resident(tail):
                            found = report(ln, tail, node.name,
                                           "resident point")
                            if found:
                                yield found


@rule("W313", "PDL family statements are not in the required order",
      Severity.WARNING)
def pdl_command_order(ctx):
    """Peak Demand Limiting is five commands with a stated order.

    "Distributed PDL uses five PPCL commands that must be defined in the
    following order: PDLMTR ... PDLSET ... PDLDPG ... PDL ... PDLDAT."
    -- Insight Program Editor, Peak Demand Limiting.

    Only the commands actually present are checked, because the five are split
    across panels by design: a predictor panel carries PDLMTR, PDLSET and
    PDLDPG, and each load-handler panel carries PDL and PDLDAT. A panel doing
    both carries all five.

    A warning rather than an error: the manual says "must", but so did the
    integer/decimal rule that field code turned out to break with impunity.
    """
    first = {}
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, CommandCall):
                continue
            if stmt.name in spec.PDL_COMMAND_ORDER:
                first.setdefault(stmt.name, ln)

    present = [n for n in spec.PDL_COMMAND_ORDER if n in first]
    if len(present) < 2:
        return

    for earlier, later in zip(present, present[1:]):
        a, b = first[earlier], first[later]
        if a.number <= b.number:
            continue
        yield _d(
            "W313",
            Severity.WARNING,
            "%s at line %d comes after %s at line %d; the PDL commands are "
            "defined in the order %s"
            % (earlier, a.number, later, b.number,
               " then ".join(spec.PDL_COMMAND_ORDER)),
            a.number,
            source_line=a.source_line,
            detail="Peak Demand Limiting builds on itself: the meter feeds "
            "the setpoints, the setpoints feed the distribution, and only "
            "then do the load-shedding statements have a target to work "
            "against. This program defines them out of that order.",
            suggestion="Move %s above %s." % (earlier, later),
            manual="Insight Program Editor, Peak Demand Limiting",
        )
        return  # one finding is enough; the whole block wants reordering
