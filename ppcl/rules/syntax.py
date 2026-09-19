"""Syntactic and structural rules: line numbers, naming, argument shapes."""

from __future__ import annotations

import re

from .. import spec
from ..analyzer import LINE_REFERENCING, substatements
from ..ast_nodes import (
    Assignment,
    CommandCall,
    Comment,
    Gosub,
    Goto,
    MacroRef,
    Num,
    ParameterDecl,
    Ref,
    Sampled,
    TimeLit,
    Unparsed,
)
from ..diagnostics import Diagnostic, Severity
from ..linter import rule

_SIMPLE_NAME = re.compile(r"^[A-Z0-9]+$")


def _d(code, sev, msg, line=None, **kw):
    return Diagnostic(code=code, severity=sev, message=msg, line=line, **kw)


# --------------------------------------------------------------------------
# Line numbering
# --------------------------------------------------------------------------


@rule("E101", "Line number outside the legal range 1-32767", Severity.ERROR)
def line_number_range(ctx):
    for ln in ctx.program.lines:
        if not (spec.LINE_MIN <= ln.number <= spec.LINE_MAX):
            yield _d(
                "E101",
                Severity.ERROR,
                "line number %d is outside the legal range %d-%d"
                % (ln.number, spec.LINE_MIN, spec.LINE_MAX),
                ln.number,
                source_line=ln.source_line,
                manual="Chapter 2, PPCL rules",
            )


@rule("E102", "Duplicate line number", Severity.ERROR)
def duplicate_line_numbers(ctx):
    seen = {}
    for ln in ctx.program.lines:
        seen.setdefault(ln.number, []).append(ln)
    for number, group in sorted(seen.items()):
        if len(group) > 1:
            where = ", ".join("source line %d" % g.source_line for g in group)
            yield _d(
                "E102",
                Severity.ERROR,
                "line number %d is used %d times" % (number, len(group)),
                number,
                source_line=group[0].source_line,
                detail="Defined at %s. Only one of these will exist in the panel; "
                "the others are silently lost on load." % where,
                manual="Chapter 2, PPCL rules: each statement must be assigned a "
                "unique line number",
                suggestion="Renumber the duplicates (ppcl renumber) or delete the "
                "dead copy.",
            )


@rule("W103", "Line numbers out of ascending order in the source file", Severity.WARNING)
def line_numbers_out_of_order(ctx):
    prev = None
    for ln in ctx.program.lines:
        if prev is not None and ln.number < prev.number:
            yield _d(
                "W103",
                Severity.WARNING,
                "line %d appears after line %d in the file" % (ln.number, prev.number),
                ln.number,
                source_line=ln.source_line,
                detail="The panel executes by line number, not file order, so this "
                "file does not read in execution order.",
                suggestion="Sort the file by line number (ppcl fmt).",
            )
        prev = ln


@rule("W104", "Program line exceeds the MMI character limit", Severity.WARNING)
def line_too_long(ctx):
    limit = spec.MMI_LINE_LIMIT[ctx.firmware]
    long_comments = []
    for ln in ctx.program.lines:
        if ln.continued:
            continue
        if ln.is_comment:
            # A comment's limit is measured on the comment TEXT: "the maximum
            # number of characters per line is 512 (not including the line
            # number or operator C)" -- A6V10374898, Maximum Number of
            # Characters per Comment Line. Measuring ln.raw instead flags a
            # comment several characters early, which is how this read until
            # that page was found.
            if len(ln.stmt.text) > limit:
                long_comments.append(ln)
            continue
        text = ln.raw.strip()
        if len(text) <= limit:
            continue
        yield _d(
            "W104",
            Severity.WARNING,
            "line is %d characters; the %s MMI port accepts %d"
            % (len(text), ctx.firmware.value, limit),
            ln.number,
            source_line=ln.source_line,
            detail="Entering this through the field panel MMI port will truncate "
            "it. Loading from a workstation is unaffected.",
            manual="Chapter 2, PPCL rules",
            suggestion="Split with a trailing & continuation.",
        )

    if long_comments:
        yield Diagnostic(
            code="W104",
            severity=Severity.STYLE,
            message="%d comment line(s) exceed %d characters of comment text"
            % (len(long_comments), limit),
            line=long_comments[0].number,
            source_line=long_comments[0].source_line,
            detail="Comments are truncated rather than rejected, so this only "
            "matters if the program is re-entered through the MMI port. The "
            "limit counts the comment text alone -- neither the line number "
            "nor the C counts against it.",
            manual="Chapter 2, PPCL rules; A6V10374898, Maximum Number of "
                   "Characters per Comment Line",
        )


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------


@rule("E105", "Point name needs quoting", Severity.ERROR)
def unquoted_long_name(ctx):
    if ctx.firmware is not spec.Firmware.APOGEE:
        return
    reported = set()
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            # DEFINE's own arguments are written unquoted in Siemens'
            # documented example: DEFINE(A01,Bld01.Ahu01). The abbreviation and
            # the string it stands for are not point references.
            if isinstance(stmt, CommandCall) and stmt.name == "DEFINE":
                continue
            for ref in _refs_in(stmt):
                if ref.quoted or ref.name.startswith("@"):
                    continue
                name = ref.name
                if name.startswith("$"):
                    # Locals are referenced bare; only the tail must be simple.
                    name = name[1:]
                upper = name.upper()
                if spec.is_resident(upper) or upper in spec.STATUS_INDICATORS:
                    continue
                if upper in ctx.analysis.parameters:
                    continue
                bad_chars = not _SIMPLE_NAME.match(upper)
                too_long = len(upper) > spec.UNQUOTED_NAME_MAX
                if not (bad_chars or too_long):
                    continue
                key = (ln.number, upper)
                if key in reported:
                    continue
                reported.add(key)
                why = []
                if too_long:
                    why.append("longer than %d characters" % spec.UNQUOTED_NAME_MAX)
                if bad_chars:
                    why.append("contains characters outside A-Z and 0-9")
                yield _d(
                    "E105",
                    Severity.ERROR,
                    "point name %s must be in double quotes (%s)"
                    % (ref.name, " and ".join(why)),
                    ln.number,
                    source_line=ln.source_line,
                    manual="Chapter 2, PPCL rules (APOGEE firmware)",
                    suggestion='Write it as "%s".' % ref.name,
                )


@rule("E120", "Point name contains a parenthesis", Severity.ERROR)
def parenthesis_in_point_name(ctx):
    """The Program Editor refuses to compile or save these.

    "Do not use points with parentheses in their names. Program Editor will
    not compile or save PPCL programs with parentheses in their point names."
    -- Insight Program Editor, Guidelines for Writing PPCL Programs, and again
    under Guidelines for Entering Program Lines.

    Only a quoted name can carry one and still parse, which is exactly the
    case that reaches the compiler and is rejected there instead of here.
    """
    reported = set()
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            for ref in _refs_in(stmt):
                if "(" not in ref.name and ")" not in ref.name:
                    continue
                key = (ln.number, ref.name)
                if key in reported:
                    continue
                reported.add(key)
                yield _d(
                    "E120",
                    Severity.ERROR,
                    'point name "%s" contains a parenthesis' % ref.name,
                    ln.number,
                    source_line=ln.source_line,
                    detail="The Program Editor will neither compile nor save "
                    "a program whose point names contain parentheses, however "
                    "the point is named in the database.",
                    suggestion="Rename the point in the point database.",
                    manual="Insight Program Editor, Guidelines for Writing "
                           "PPCL Programs",
                )


@rule("E106", "Point name starting with a digit needs an @ prefix", Severity.ERROR)
def digit_leading_name(ctx):
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            for ref in _refs_in(stmt):
                if ref.quoted or ref.name.startswith("@"):
                    continue
                if ref.name[:1].isdigit():
                    yield _d(
                        "E106",
                        Severity.ERROR,
                        "point name %s begins with a number and must be prefixed "
                        "with @" % ref.name,
                        ln.number,
                        source_line=ln.source_line,
                        manual="Chapter 2, PPCL rules",
                        suggestion="Write it as @%s." % ref.name,
                    )


@rule("W107", "Reserved word used as a point name", Severity.WARNING)
def reserved_word_as_point(ctx):
    reported = set()
    for use in ctx.analysis.uses:
        if use.kind != "write":
            continue
        upper = use.name.upper().lstrip("$")
        if upper not in spec.RESERVED_WORDS:
            continue
        # $ARGn and $LOCn are on the reserved list because nothing else may be
        # named that -- but writing to them is their entire purpose. A
        # subroutine receives its arguments in $ARG1..$ARG15, and TABLE,
        # MIN/MAX and friends write their result into one. Siemens' own
        # published MEC100K program does exactly this ten times over.
        if use.name.startswith("$") and spec.is_builtin_local(use.name):
            continue
        if spec.is_resident(upper) and use.context == "assign":
            pass  # still a problem, reported below
        key = (use.line, upper)
        if key in reported:
            continue
        reported.add(key)
        yield _d(
            "W107",
            Severity.WARNING,
            "assigning to %s, which is on the PPCL reserved word list" % use.name,
            use.line,
            detail="Reserved words are used by the PPCL compiler. Using one as a "
            "point name produces behaviour the manual does not define.",
            manual="Chapter 5, PPCL Reserved Word List",
        )


# --------------------------------------------------------------------------
# Command arguments
# --------------------------------------------------------------------------


@rule("E110", "Unknown command", Severity.ERROR)
def unknown_command(ctx):
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, CommandCall) or stmt.name in spec.ALL:
                continue
            if stmt.name in spec.FIRMWARE_STATEMENT_TOKENS:
                continue            # W121 has the honest version
            yield _d(
                "E110",
                Severity.ERROR,
                "%s is not a PPCL command" % stmt.name,
                ln.number,
                source_line=ln.source_line,
                manual="Chapter 4, Command syntax; Chapter 5, PPCL reserved "
                       "word list",
                suggestion=_suggest_command(stmt.name),
            )


@rule("E123", "Compiler wrapped this line in UNKNOWN and ignores it",
      Severity.ERROR)
def unknown_marker(ctx):
    """The panel has already told you this line does nothing.

    "Any unknown PPCL commands will be added with an UNKNOWN (...) marker and
    ignored by the compiler upon saving a program." -- Desigo PXC.A Web
    Interface User Guide (A6V12893115), PPCL Diagnostics.

    So a line in this shape is a statement the engineer wrote, the panel kept,
    and nothing executes. It is the rarest kind of defect and the easiest to
    miss reading a program, because the text of the original command is still
    right there on the line.
    """
    for ln in ctx.program.lines:
        if not getattr(ln, "unknown", False):
            continue
        inner = getattr(ln.stmt, "text", "") or ""
        shown = inner if len(inner) <= 40 else inner[:37] + "..."
        yield _d(
            "E123",
            Severity.ERROR,
            "the compiler did not recognise this command and ignores the "
            "line%s" % (": %s" % shown if shown else ""),
            ln.number,
            source_line=ln.source_line,
            detail="An UNKNOWN (...) wrapper is written by the compiler when "
            "it saves a program containing a command it cannot resolve. The "
            "line stays in the program and never runs, and nothing else "
            "reports it -- the original text is still visible on the line, "
            "which is what makes it easy to read straight past.",
            manual="A6V12893115, PPCL Diagnostics (UNKNOWN Marker)",
            suggestion="Correct the command or delete the line, then save "
            "again. A wrapper left in place is re-wrapped on the next save.",
        )


@rule("E122", "OIP keystroke sequence is too long", Severity.ERROR)
def oip_sequence_length(ctx):
    """Sixty characters including the slashes, and it fails at run time.

    OIP reports FAILED when the sequence is malformed, which happens on the
    panel rather than in the editor -- so nothing tells the engineer until the
    trigger fires and the sequence silently does not run.
    """
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, CommandCall) or stmt.name != "OIP":
                continue
            if len(stmt.args) < 2:
                continue
            seq = stmt.args[1]
            text = getattr(seq, "name", None)
            if text is None or not getattr(seq, "quoted", False):
                continue
            if len(text) <= spec.OIP_SEQUENCE_MAX:
                continue
            yield _d(
                "E122",
                Severity.ERROR,
                "OIP sequence is %d characters; the maximum is %d"
                % (len(text), spec.OIP_SEQUENCE_MAX),
                ln.number,
                source_line=ln.source_line,
                detail="Slashes count toward the limit. OIP validates its "
                "sequence when the trigger fires, not when the line is "
                "entered, so an over-long sequence shows as FAILED on the "
                "panel and nowhere else.",
                manual="Chapter 4, OIP",
                suggestion="Split it across two OIP statements on separate "
                "triggers, staggered in time so one finishes before the next "
                "begins.",
            )


@rule("W121", "Statement the firmware names but no manual documents",
      Severity.WARNING)
def firmware_only_statement(ctx):
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, CommandCall):
                continue
            entry = spec.FIRMWARE_STATEMENT_TOKENS.get(stmt.name)
            if entry is None:
                continue
            value, note = entry
            yield _d(
                "W121",
                Severity.WARNING,
                "%s was not checked: the panel firmware has a statement token "
                "for it, but no manual documents it" % stmt.name,
                ln.number,
                source_line=ln.source_line,
                detail="Value %d of the controller's own PPCL_statement_type "
                "enum, so the name is real and calling it a typo would be "
                "wrong. But it appears in none of the manuals on hand and in "
                "no program in any corpus, so its arguments and its behaviour "
                "are unknown and this line has been left unvalidated -- the "
                "argument count, the point types and the simulation all skip "
                "it. %s" % (value, note),
                manual="Not documented. Vendor enum PPCL_statement_type.",
                suggestion="If you know what this does, it belongs in "
                "spec.ALL with a signature. Until then, check the line by "
                "hand.",
            )


def _suggest_command(name):
    import difflib

    close = difflib.get_close_matches(name, list(spec.ALL), n=2, cutoff=0.7)
    return ("Did you mean %s?" % " or ".join(close)) if close else ""


@rule("E111", "Wrong number of arguments", Severity.ERROR)
def argument_count(ctx):
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, CommandCall):
                continue
            cmd = spec.ALL.get(stmt.name)
            if cmd is None or not cmd.signature_known:
                continue

            n = len(stmt.args)
            used_priority = 1 if stmt.priority else 0
            slots = len(cmd.fixed) + len(cmd.repeat) * cmd.max_repeat
            low = cmd.min_args()

            if n < low:
                yield _d(
                    "E111",
                    Severity.ERROR,
                    "%s needs at least %d argument%s but has %d"
                    % (stmt.name, low, "" if low == 1 else "s", n),
                    ln.number,
                    source_line=ln.source_line,
                    detail=_signature(cmd),
                    manual="Chapter 4, %s" % cmd.name,
                )
                continue

            if n + used_priority > slots:
                extra = (
                    " (an @priority occupies one of the %d parameter slots)" % slots
                    if used_priority
                    else ""
                )
                yield _d(
                    "E111",
                    Severity.ERROR,
                    "%s accepts at most %d arguments but has %d%s"
                    % (stmt.name, slots - used_priority, n, extra),
                    ln.number,
                    source_line=ln.source_line,
                    detail=_signature(cmd),
                    manual="Chapter 4, %s" % cmd.name,
                )
                continue

            group = len(cmd.repeat)
            if group > 1:
                rest = n - len(cmd.fixed) - len(cmd.trailing)
                if rest % group:
                    names = "/".join(p.name for p in cmd.repeat)
                    yield _d(
                        "E111",
                        Severity.ERROR,
                        "%s takes its trailing arguments in groups of %d (%s); "
                        "%d were given" % (stmt.name, group, names, rest),
                        ln.number,
                        source_line=ln.source_line,
                        detail=_signature(cmd),
                        manual="Chapter 4, %s" % cmd.name,
                    )


def _signature(cmd):
    parts = [p.name for p in cmd.fixed]
    if cmd.repeat:
        names = ",".join(p.name for p in cmd.repeat)
        parts.append("%s1,...,%s%d" % (cmd.repeat[0].name, names.split(",")[-1],
                                       cmd.max_repeat)
                     if len(cmd.repeat) > 1
                     else "%s1,...,%s%d" % (cmd.repeat[0].name, cmd.repeat[0].name,
                                            cmd.max_repeat))
    sig = "%s(%s)" % (cmd.name, ",".join(parts))
    if cmd.priority_arg:
        sig += "   or   %s(@prior,...)" % cmd.name
    return "signature: " + sig


@rule("E112", "@priority given to a command that does not accept one", Severity.ERROR)
def priority_not_allowed(ctx):
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, CommandCall) or not stmt.priority:
                continue
            cmd = spec.ALL.get(stmt.name)
            if cmd is not None and not cmd.priority_arg:
                yield _d(
                    "E112",
                    Severity.ERROR,
                    "%s does not take an @priority argument" % stmt.name,
                    ln.number,
                    source_line=ln.source_line,
                    detail="Commands accepting @priority: "
                    + ", ".join(sorted(c for c, v in spec.ALL.items() if v.priority_arg)),
                    manual="Chapter 3, Command priority; Chapter 4, %s"
                           % stmt.name,
                )


@rule("W113", "Integer literal where the manual writes a decimal", Severity.WARNING)
def integer_where_decimal_required(ctx):
    """Graded down from ERROR after running real engineered programs.

    125-1896 states that these four commands do not accept an integer, and
    spec.py keeps that fact. But the restriction is firmware-historical: the
    manual already records APOGEE relaxing it for ``SET``, the Insight 3.15
    Program Editor help says only "a number, point name, or local variable"
    for all four, and a shipped third-party program compiles and runs
    ``INITTO(0,"...")``. ERROR means "the compiler will reject this", which
    we cannot demonstrate. It is still worth writing the decimal -- it is the
    documented form and it is what every Siemens example uses.
    """
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, CommandCall):
                continue
            cmd = spec.ALL.get(stmt.name)
            if cmd is None:
                continue
            for i, param in enumerate(cmd.fixed):
                if param.kind is not spec.Arg.DECIMAL or i >= len(stmt.args):
                    continue
                arg = stmt.args[i]
                if not isinstance(arg, Num) or not arg.is_integer_literal:
                    continue
                # APOGEE relaxed this for SET only.
                if stmt.name == "SET" and ctx.firmware is spec.Firmware.APOGEE:
                    continue
                yield _d(
                    "W113",
                    Severity.WARNING,
                    "%s parameter '%s' is the integer %s; the manual writes "
                    "this as a decimal" % (stmt.name, param.name, arg.raw),
                    ln.number,
                    source_line=ln.source_line,
                    detail=(
                        "125-1896 says an integer is not accepted here. Later "
                        "documentation drops that restriction and field "
                        "programs do use integers, so this is unlikely to "
                        "stop the program compiling -- but the decimal is the "
                        "documented form and costs nothing."
                    ),
                    manual="Chapter 4, %s; Insight Program Editor, Statement "
                           "Arguments - %s" % (stmt.name, stmt.name),
                    suggestion="Write %s.0 instead of %s." % (arg.raw, arg.raw),
                )


@rule("E114", "Argument outside the documented set of values", Severity.ERROR)
def enum_argument(ctx):
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, CommandCall):
                continue
            cmd = spec.ALL.get(stmt.name)
            if cmd is None:
                continue
            for i, param in enumerate(cmd.fixed):
                if param.kind is not spec.Arg.ENUM or not param.choices:
                    continue
                if i >= len(stmt.args):
                    continue
                arg = stmt.args[i]
                if not isinstance(arg, Num):
                    continue  # a point or local may hold any legal value
                literal = arg.raw
                normalized = str(int(arg.value)) if arg.value.is_integer() else literal
                if literal in param.choices or normalized in param.choices:
                    continue
                yield _d(
                    "E114",
                    Severity.ERROR,
                    "%s parameter '%s' is %s; valid values are %s"
                    % (stmt.name, param.name, literal, ", ".join(param.choices)),
                    ln.number,
                    source_line=ln.source_line,
                    detail=param.doc,
                    manual="Chapter 4, %s" % stmt.name,
                )


@rule("W115", "Literal value passed where a point name is required", Severity.WARNING)
def literal_where_point_required(ctx):
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, CommandCall):
                continue
            cmd = spec.ALL.get(stmt.name)
            if cmd is None or stmt.name == "LOCAL":
                continue
            for i, param in enumerate(cmd.fixed):
                if param.kind is not spec.Arg.POINT or i >= len(stmt.args):
                    continue
                arg = stmt.args[i]
                if isinstance(arg, (Num, TimeLit)):
                    yield _d(
                        "W115",
                        Severity.WARNING,
                        "%s parameter '%s' expects a point name but got the "
                        "literal %s" % (stmt.name, param.name, arg.raw),
                        ln.number,
                        source_line=ln.source_line,
                        detail=param.doc,
                        manual="Chapter 4, %s" % stmt.name,
                    )


@rule("W116", "%macro% used without a matching DEFINE", Severity.WARNING)
def undefined_macro(ctx):
    known = {k.upper() for k in ctx.analysis.defines}
    pattern = re.compile(r"%([A-Za-z0-9_.]+)%")
    for ln in ctx.program.lines:
        if ln.is_comment:
            continue
        for match in pattern.finditer(ln.body):
            name = match.group(1).upper()
            if name not in known:
                yield _d(
                    "W116",
                    Severity.WARNING,
                    "%%%s%% has no matching DEFINE in this program"
                    % match.group(1),
                    ln.number,
                    source_line=ln.source_line,
                    detail="DEFINE abbreviations are per-program. If the DEFINE "
                    "lives in another program in this panel, this is fine.",
                    manual="Chapter 4, DEFINE",
                )


def _refs_in(stmt):
    """Yield the direct Ref nodes of a statement (not nested statements)."""
    from ..analyzer import expr_refs

    if isinstance(stmt, Assignment):
        if isinstance(stmt.target, Ref):
            yield stmt.target
        yield from expr_refs(stmt.expr)
    elif isinstance(stmt, CommandCall):
        if stmt.name in LINE_REFERENCING:
            return
        for arg in stmt.args:
            if isinstance(arg, Ref):
                yield arg
            else:
                yield from expr_refs(arg)
    elif isinstance(stmt, Sampled):
        yield from expr_refs(stmt.seconds)
    elif isinstance(stmt, Gosub):
        for arg in stmt.args:
            if isinstance(arg, Ref):
                yield arg
    else:
        from ..ast_nodes import If

        if isinstance(stmt, If):
            yield from expr_refs(stmt.cond)


@rule("W117", "Malformed BACnet third-party point reference", Severity.WARNING)
def bacnet_reference_form(ctx):
    """The BAC_ form is BAC_<device>_<objecttype>_<instance>, e.g. BAC_10_MO_1.

    Only the nine BACnet object types PPCL supports are valid in the middle
    field, so a typo there is worth catching before download.
    """
    pattern = re.compile(spec.BACNET_REFERENCE)
    loose = re.compile(r"^BAC_", re.IGNORECASE)
    reported = set()
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            for ref in _refs_in(stmt):
                name = ref.name.upper()
                if not loose.match(name):
                    continue
                key = (ln.number, name)
                if key in reported:
                    continue
                reported.add(key)
                m = pattern.match(name)
                if not m:
                    yield _d(
                        "W117",
                        Severity.WARNING,
                        "%s does not match the BACnet reference form "
                        "BAC_<device>_<objecttype>_<instance>" % ref.name,
                        ln.number,
                        source_line=ln.source_line,
                        detail="For example BAC_10_MO_1 is device instance 10, "
                        "Multi-State Output, object instance 1.",
                        manual="Desigo CC engineering help, BACnet Point Referencing",
                    )
                    continue
                objtype = m.group(2)
                if objtype not in spec.BACNET_OBJECT_TYPES:
                    yield _d(
                        "W117",
                        Severity.WARNING,
                        "%s names object type %r, which PPCL does not support"
                        % (ref.name, objtype),
                        ln.number,
                        source_line=ln.source_line,
                        detail="Supported types: %s."
                        % ", ".join(
                            "%s (%s)" % (k, v)
                            for k, v in sorted(spec.BACNET_OBJECT_TYPES.items())
                        ),
                        manual="Desigo CC engineering help, BACnet Point Referencing",
                    )


@rule("E118", "Unknown function in an expression", Severity.ERROR)
def unknown_function(ctx):
    """PPCL has ten functions and no way to define more.

    The common near-miss is ARC for arc-tangent: the Desigo CC precedence
    table prints ``ARC(value1)``, but the PPCL Editor's own Command Assist
    lists ATN, so ARC does not compile.
    """
    import difflib

    from ..analyzer import walk_expr
    from ..ast_nodes import FuncCall, If

    def expressions(stmt):
        if isinstance(stmt, If):
            yield stmt.cond
        elif isinstance(stmt, Assignment):
            yield stmt.expr
        elif isinstance(stmt, CommandCall):
            yield from stmt.args
        elif isinstance(stmt, Sampled):
            yield stmt.seconds

    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            for expr in expressions(stmt):
                for node in walk_expr(expr):
                    if not isinstance(node, FuncCall):
                        continue
                    if node.name in spec.FUNCTIONS:
                        continue
                    hint = ""
                    if node.name == "ARC":
                        hint = (
                            "Arc-tangent is ATN. The Desigo CC precedence "
                            "table prints ARC(value1), but that is a "
                            "documentation error -- the PPCL Editor lists ATN."
                        )
                    else:
                        close = difflib.get_close_matches(
                            node.name, list(spec.FUNCTIONS), n=2, cutoff=0.6
                        )
                        if close:
                            hint = "Did you mean %s?" % " or ".join(close)
                    yield _d(
                        "E118",
                        Severity.ERROR,
                        "%s is not a PPCL function" % node.name,
                        ln.number,
                        source_line=ln.source_line,
                        detail=hint,
                        manual="Order of Precedence for PPCL Operators and "
                        "Special Functions",
                        suggestion="Available functions: %s."
                        % ", ".join(sorted(spec.FUNCTIONS)),
                    )


@rule("E119", "Command not available on the selected firmware", Severity.ERROR)
def command_not_on_firmware(ctx):
    """A command the target panel generation does not have.

    "Valid PPCL" and "runs on my panel" are different questions. The PXC.A
    generation dropped commands the older panels had, and the manual is blunt
    about the consequence for OIP: the runtime "will consider this statement
    invalid, and no replacements have been provided."

    This only fires where the spec records an explicit firmware restriction,
    so it stays quiet on every command whose availability is unqualified.
    """
    for ln in ctx.program.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, CommandCall):
                continue
            command = spec.ALL.get(stmt.name)
            if command is None:
                continue
            if ctx.firmware in command.firmware:
                continue
            available = ", ".join(
                sorted(f.value for f in command.firmware)
            )
            yield _d(
                "E119",
                Severity.ERROR,
                "%s is not available on %s firmware"
                % (stmt.name, ctx.firmware.value),
                ln.number,
                source_line=ln.source_line,
                detail=_why_unavailable(stmt.name, ctx.firmware, available),
                suggestion=(
                    "Re-program it or remove it. Siemens' own conversion tool "
                    "comments these out rather than translating them, because "
                    "no replacement exists."
                    if (ctx.firmware is spec.Firmware.PXC_A
                        and stmt.name in spec.PXC_A_REMOVED)
                    else "Use a command the target panel supports, or change "
                    "the firmware setting if the target is older."
                ),
                manual="A6V10374898 PXC.A PPCL User Guide, Obsolete PPCL "
                       "Statements Removed from the Language"
                       if stmt.name in spec.PXC_A_REMOVED
                       else "A6V10374898 PXC.A PPCL User Guide, %s" % stmt.name,
            )


def _why_unavailable(name, firmware, available):
    """Siemens' own reason, where the manual gives one."""
    base = ("This command exists on %s. Selecting the wrong firmware in "
            "Settings is the other explanation, so check that first."
            % available)
    if firmware is not spec.Firmware.PXC_A:
        return base
    reason = spec.PXC_A_REMOVED.get(name)
    if not reason:
        return base
    return ("%s The PXC.A runtime treats the statement as invalid and no "
            "replacement is provided. %s" % (reason, base))
