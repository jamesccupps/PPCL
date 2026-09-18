"""Compile a sequence document to PPCL.

The compiler exists to make a class of defects impossible rather than merely
detectable. What it guarantees by construction:

* **Exactly one backward GOTO**, and it is the last statement. That is the one
  the Desigo compiler permits ("backwards GOTO found. With the exception of
  the last GOTO in the program...").
* **Every interlock gets a matching RELEAS at the same priority.** A latch with
  no release is the most common PPCL field defect, and here it cannot be
  written.
* **Time-based commands are never inside an IF and never inside a subroutine**,
  so LOOP, TABLE and SAMPLE are evaluated on every pass as the manual requires.
* **Line references are symbolic** until render time, so nothing can point at
  a stale line number.

Statement order is deliberate and is the part that makes the output correct:

1. Header, LOCAL declarations, ONPWRT
2. Interlock **latches** -- first, so a mode condition can test one
3. Mode determination -- one pass, last matching mode wins
4. Resets (TABLE) -- unconditional
5. Loops (LOOP) -- unconditional, so their timing stays correct
6. Decision table -- issued *after* the loops so a table cell can override a
   loop's output, which is how "closed on shutdown" beats "modulate"
7. Free-form rules
8. Interlock **forces** -- last, so a safety overrides everything above it
9. The single closing GOTO

Two consequences worth understanding:

* A cell saying ``modulate`` emits nothing, leaving the point to its loop.
  Any other cell value overrides the loop, because the table runs after it.
* Because latches are computed before modes, a mode can be written
  ``when Freeze is on`` and drive an entire shutdown column of the table.
  That is better than forcing points one at a time: the table then shows, in
  one place, what every piece of equipment does during the shutdown.
"""

from __future__ import annotations

from .. import spec
from ..generator import Builder, integral_gain, proportional_gain, table_statement
from .model import (
    All,
    Always,
    Any,
    Between,
    CELL_ACTIONS,
    COMPARISONS,
    Compare,
    Not,
    STATUS_WORDS,
    Sequence,
)


class CompileError(Exception):
    """Raised when a document cannot be compiled."""


# --------------------------------------------------------------------------
# Rendering values and conditions
# --------------------------------------------------------------------------


def quote(name: str) -> str:
    """Quote a point name if PPCL requires it."""
    if name.startswith('"'):
        return name
    bare = name.lstrip("$")
    if len(bare) > spec.UNQUOTED_NAME_MAX or not bare.isalnum():
        return '"%s"' % name
    return name


def flag_name(name: str) -> str:
    """The local-variable name holding an interlock's latched state."""
    upper = name.upper()
    return upper[:6] if len(upper) > 6 else upper


def interlock_flag(name, seq: Sequence = None):
    """Return an interlock's latch flag if ``name`` names one, else None.

    This is what lets a mode condition read ``when Freeze is on``, so a safety
    can drive an entire shutdown column of the decision table instead of
    forcing points one at a time.
    """
    if seq is None or not isinstance(name, str):
        return None
    for il in seq.interlocks:
        if il.name.upper() == name.strip().upper():
            return '"$%s"' % flag_name(il.name)
    return None


def resolve(name: str, seq: Sequence = None) -> str:
    """Render a point reference, adding the $ sigil for a local point.

    A point declared with role ``local`` is emitted in a LOCAL statement
    without the sigil but referenced everywhere else with it. Getting this
    wrong produces a program that compiles and silently does nothing, because
    the bare name is a different point.
    """
    flag = interlock_flag(name, seq)
    if flag is not None:
        return flag
    if seq is not None:
        p = seq.point(name)
        if p is not None and p.is_local:
            return '"$%s"' % p.name.upper()
    return quote(name)


def render_value(value, seq: Sequence = None) -> str:
    """Render a document value as PPCL source."""
    if isinstance(value, bool):
        return "1.0" if value else "0.0"
    if isinstance(value, (int, float)):
        return "%.1f" % value if float(value) == int(value) else "%g" % value
    text = str(value).strip()
    if not text:
        return '""'
    lower = text.lower()
    if lower in STATUS_WORDS:
        return lower.upper()
    if ":" in text and text.replace(":", "").isdigit():
        return text  # a time literal such as 6:00
    try:
        number = float(text)
    except ValueError:
        return resolve(text, seq)
    return "%.1f" % number if number == int(number) else "%g" % number


def render_condition(cond, seq: Sequence = None) -> str:
    """Render a condition as a PPCL expression."""
    if cond is None or isinstance(cond, Always):
        raise CompileError("this condition cannot be rendered as a test")

    if isinstance(cond, Compare):
        op = COMPARISONS.get(cond.op.strip().lower())
        if op is None:
            raise CompileError(
                "%r is not a comparison; use one of %s"
                % (cond.op, ", ".join(sorted(set(COMPARISONS))))
            )
        right = cond.right
        # An interlock flag is a local holding 1.0 or 0.0, so comparing it to
        # the words on/off means comparing to those numbers, not to the ON and
        # OFF point-status indicators.
        if interlock_flag(cond.left, seq) is not None:
            word = str(right).strip().lower()
            if word in ("on", "true", "latched", "tripped"):
                right = 1.0
            elif word in ("off", "false", "clear", "reset"):
                right = 0.0
        return "%s%s%s" % (
            render_value(cond.left, seq),
            op,
            render_value(right, seq),
        )

    if isinstance(cond, Between):
        left = render_value(cond.left, seq)
        return "%s.GE.%s.AND.%s.LT.%s" % (
            left,
            render_value(cond.low, seq),
            left,
            render_value(cond.high, seq),
        )

    if isinstance(cond, All):
        if not cond.parts:
            raise CompileError("an 'all' condition needs at least one part")
        return ".AND.".join(_wrap(p, seq) for p in cond.parts)

    if isinstance(cond, Any):
        if not cond.parts:
            raise CompileError("an 'any' condition needs at least one part")
        return ".OR.".join(_wrap(p, seq) for p in cond.parts)

    if isinstance(cond, Not):
        if isinstance(cond.part, Compare):
            flipped = {
                "<": ">=", "<=": ">", ">": "<=", ">=": "<",
                "=": "!=", "==": "!=", "!=": "=", "<>": "=",
                "is": "is not", "is not": "is",
            }.get(cond.part.op.strip().lower())
            if flipped:
                return render_condition(
                    Compare(cond.part.left, flipped, cond.part.right), seq
                )
        raise CompileError(
            "PPCL has no NOT operator; write the inverse comparison instead"
        )

    raise CompileError("unsupported condition %r" % type(cond).__name__)


def _wrap(part, seq):
    text = render_condition(part, seq)
    if isinstance(part, (All, Any, Between)):
        return "(%s)" % text
    return text


def count_operands(cond, seq: Sequence = None) -> int:
    """Operands a condition will use, for the 16-operand statement limit."""
    if cond is None or isinstance(cond, Always):
        return 0
    if isinstance(cond, Compare):
        return 2
    if isinstance(cond, Between):
        return 3
    if isinstance(cond, (All, Any)):
        return sum(count_operands(p, seq) for p in cond.parts)
    if isinstance(cond, Not):
        return count_operands(cond.part, seq)
    return 0


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------


def render_action(point: str, action, priority: str = None, seq: Sequence = None):
    """Render one (point, action) pair as a PPCL statement, or None.

    Returns ``None`` for actions that deliberately emit nothing, which is how
    ``modulate`` leaves a point to its control loop.
    """
    if action is None:
        return None
    text = str(action).strip().lower()
    if text in ("modulate", "hold", ""):
        return None

    prefix = ("%s," % priority) if priority else ""

    name = resolve(point, seq)
    if text in ("on", "off", "auto", "fast", "slow"):
        return "%s(%s%s)" % (text.upper(), prefix, name)
    if text == "open":
        return "SET(%s100.0,%s)" % (prefix, name)
    if text == "closed":
        return "SET(%s0.0,%s)" % (prefix, name)

    # Anything else must be a value.
    try:
        number = float(text)
    except ValueError:
        raise CompileError(
            "%r is not an action for %s; use one of %s, or a number"
            % (action, point, ", ".join(sorted(CELL_ACTIONS)))
        )
    return "SET(%s%s,%s)" % (
        prefix,
        "%.1f" % number if number == int(number) else "%g" % number,
        name,
    )


def _action_key(action):
    """Group key so points sharing an action can be commanded together."""
    text = str(action).strip().lower()
    return text


# --------------------------------------------------------------------------
# The compiler
# --------------------------------------------------------------------------


class Compiler:
    """Turns a :class:`Sequence` into PPCL text."""

    #: Local flag holding the current mode index.
    MODE_LOCAL = "MODE"

    def __init__(self, seq: Sequence):
        self.seq = seq
        self.warnings = []
        self._locals = []

    # -- locals ------------------------------------------------------------

    def _local(self, name: str) -> str:
        upper = name.upper()[:6] if len(name) > 6 else name.upper()
        # Keep local names short so statements stay inside the MMI limit.
        candidate = upper
        n = 1
        while candidate in self._locals and candidate != upper:
            n += 1
            candidate = "%s%d" % (upper[:5], n)
        if candidate not in self._locals:
            self._locals.append(candidate)
        return candidate

    def _interlock_local(self, interlock) -> str:
        return self._local(flag_name(interlock.name))

    # -- compilation -------------------------------------------------------

    def compile(self) -> str:
        seq = self.seq
        if not seq.modes and not seq.rules and not seq.interlocks:
            raise CompileError(
                "the sequence has no modes, interlocks or rules, so there is "
                "nothing to compile"
            )

        # Reserve local names before emitting anything.
        mode_local = self._local(self.MODE_LOCAL) if seq.modes else None
        interlock_locals = {
            il.name: self._interlock_local(il) for il in seq.interlocks
        }
        for p in seq.points:
            if p.is_local:
                self._local(p.name)

        b = Builder(start=seq.start_line, step=seq.line_step)
        self._header(b)

        b.blank()
        b.rule("Declarations and power restart")
        if self._locals:
            b.code("LOCAL(%s)" % ",".join('"%s"' % n for n in self._locals))
        b.label("INIT")
        b.code("ONPWRT({init})", init="INIT")

        b.blank()
        b.rule("Main loop")
        b.label("MAIN")

        # Latches come first so a mode condition can test an interlock and
        # drive a whole shutdown column of the decision table. Forces come
        # after, where they override whatever the table just commanded.
        if seq.interlocks:
            self._emit_interlock_latches(b, interlock_locals)
        if seq.modes:
            self._emit_modes(b, mode_local)
        if seq.resets:
            self._emit_resets(b)
        if seq.loops:
            self._emit_loops(b)
        if seq.table.cells and seq.modes:
            self._emit_table(b, mode_local)
        if seq.rules:
            self._emit_rules(b)
        if seq.interlocks:
            self._emit_interlock_forces(b, interlock_locals)

        b.blank()
        b.rule("Close the main loop")
        b.label("LOOPEND")
        b.goto("MAIN")

        text = b.render()
        for number, length in getattr(b, "long_lines", []):
            self.warnings.append(
                "line %d is %d characters, over the %d-character MMI limit; it "
                "will load fine from a workstation but cannot be typed at the "
                "panel" % (number, length, spec.MMI_LINE_LIMIT[spec.Firmware.APOGEE])
            )
        return text

    # -- sections ----------------------------------------------------------

    def _header(self, b: Builder) -> None:
        seq = self.seq
        b.comment("=" * 58)
        b.comment("Title: %s" % seq.name)
        if seq.equipment:
            b.comment("Equipment: %s" % seq.equipment)
        if seq.organization:
            b.comment("Organization: %s" % seq.organization)
        if seq.author:
            b.comment("Author: %s" % seq.author)
        b.comment("Version: %s" % seq.version)
        b.comment("")
        b.comment("Generated from a sequence document. Edit the document and")
        b.comment("recompile rather than editing this program by hand.")
        if seq.description:
            b.comment("")
            for line in seq.description.split("\n"):
                b.comment(line)
        if seq.modes:
            b.comment("")
            b.comment("Modes, lowest priority first:")
            for i, m in enumerate(seq.modes, start=1):
                b.comment("  %d = %s%s"
                          % (i, m.name,
                             (" -- " + m.description) if m.description else ""))
        b.comment("=" * 58)

    def _emit_modes(self, b: Builder, mode_local: str) -> None:
        """Determine the active mode.

        The default is mode 1, then each higher-priority mode overwrites it if
        its condition holds. Because they are tested in order, the last match
        wins, which makes "shutdown beats occupied" a matter of list order.
        """
        seq = self.seq
        b.blank()
        b.rule("Determine the operating mode")
        b.comment("Tested lowest priority first; the last match wins.")
        b.code('"$%s" = 1.0' % mode_local)
        for index, mode in enumerate(seq.modes[1:], start=2):
            if isinstance(mode.when, Always):
                self.warnings.append(
                    "mode %r is marked 'always', so every mode after it can "
                    "never be selected" % mode.name
                )
                b.code('"$%s" = %.1f' % (mode_local, index))
                continue
            operands = count_operands(mode.when, seq) + 1
            limit = spec.MAX_OPERANDS[spec.Firmware.APOGEE]
            if operands > limit:
                raise CompileError(
                    "the condition for mode %r needs %d operands, over the "
                    "limit of %d. Split it using a local flag."
                    % (mode.name, operands, limit)
                )
            b.comment("%s" % mode.name)
            b.code(
                'IF(%s) THEN "$%s" = %.1f'
                % (render_condition(mode.when, seq), mode_local, index)
            )

    def _emit_interlock_latches(self, b: Builder, locals_by_name: dict) -> None:
        """Latch and clear each interlock, before the mode is determined."""
        seq = self.seq
        b.blank()
        b.rule("Safety interlocks - latch")
        b.comment("Evaluated before the mode so a mode can react to a trip.")
        for il in seq.interlocks:
            flag = locals_by_name[il.name]
            b.comment("%s%s" % (il.name,
                                (" -- " + il.description) if il.description else ""))
            if il.trip is None:
                raise CompileError(
                    "interlock %r has no trip condition" % il.name
                )
            b.code('IF(%s) THEN "$%s" = 1.0'
                   % (render_condition(il.trip, seq), flag))
            if il.reset is None:
                raise CompileError(
                    "interlock %r has no reset condition. A latch that never "
                    "clears leaves its points stuck at %s forever."
                    % (il.name, il.priority)
                )
            b.code('IF(%s) THEN "$%s" = 0.0'
                   % (render_condition(il.reset, seq), flag))

    def _emit_interlock_forces(self, b: Builder, locals_by_name: dict) -> None:
        """Force and release the points an interlock owns.

        Emitted last so the force overrides whatever the decision table just
        commanded, and so the release lands after the table has had its say.
        """
        seq = self.seq
        for il in seq.interlocks:
            if not il.forces:
                self.warnings.append(
                    "interlock %r latches but forces no points directly. That "
                    "is fine if a mode tests it, but check something acts on it."
                    % il.name
                )
        forcing = [il for il in seq.interlocks if il.forces]
        if not forcing:
            return

        b.blank()
        b.rule("Safety interlocks - force")
        b.comment("Command and release at the SAME priority, or the release")
        b.comment("silently does nothing.")
        for il in forcing:
            flag = locals_by_name[il.name]
            for point, action in il.forces:
                stmt = render_action(point, action, priority=il.priority,
                                     seq=seq)
                if stmt is None:
                    raise CompileError(
                        "interlock %r cannot force %s to %r"
                        % (il.name, point, action)
                    )
                b.code('IF("$%s".EQ.1.0) THEN %s' % (flag, stmt))
            released = ",".join(resolve(p, seq) for p, _ in il.forces)
            b.code('IF("$%s".EQ.0.0) THEN RELEAS(%s,%s)'
                   % (flag, il.priority, released))

    def _emit_resets(self, b: Builder) -> None:
        b.blank()
        b.rule("Reset schedules")
        for reset in self.seq.resets:
            if len(reset.points) < 1:
                raise CompileError(
                    "reset for %s has no breakpoints" % reset.output
                )
            xs = [p[0] for p in reset.points]
            if any(b1 <= a for a, b1 in zip(xs, xs[1:])):
                raise CompileError(
                    "reset for %s has breakpoints out of order: %s. TABLE "
                    "requires ascending x values."
                    % (reset.output, ", ".join(str(x) for x in xs))
                )
            if reset.description:
                b.comment(reset.description)
            for x, y in reset.points:
                b.comment("  %-10s -> %s" % (x, y))
            b.code(
                table_statement(
                    resolve(reset.source, self.seq),
                    resolve(reset.output, self.seq),
                    reset.points,
                )
            )

    def _emit_loops(self, b: Builder) -> None:
        b.blank()
        b.rule("Control loops")
        b.comment("Unconditional, so their sample timing stays correct.")
        for loop in self.seq.loops:
            span = loop.output_high - loop.output_low
            if span <= 0:
                raise CompileError(
                    "loop %r has an output range of %g to %g"
                    % (loop.name, loop.output_low, loop.output_high)
                )
            pg = proportional_gain(span, loop.throttling_range)
            ig = integral_gain(pg, loop.integral)
            bias = (
                loop.bias
                if loop.bias is not None
                else loop.output_low + span / 2.0
            )
            kind = 0 if loop.action.strip().lower().startswith("direct") else 128
            if loop.description:
                b.comment(loop.description)
            b.comment(
                "%s: %s acting, throttling range %g"
                % (loop.name, "direct" if kind == 0 else "reverse",
                   loop.throttling_range)
            )
            b.code(
                "LOOP(%d,%s,%s,%s,%d,%d,0,%d,%s,%s,%s,0)"
                % (
                    kind,
                    resolve(loop.process, self.seq),
                    resolve(loop.output, self.seq),
                    resolve(loop.setpoint, self.seq),
                    pg,
                    ig,
                    int(loop.sample_seconds),
                    _num(bias),
                    _num(loop.output_low),
                    _num(loop.output_high),
                )
            )

    def _emit_table(self, b: Builder, mode_local: str) -> None:
        """Emit the decision table, grouping points that share an action.

        Grouping matters: PPCL commands take up to 16 points, so a whole row of
        fans going ON in a mode becomes one statement rather than four.
        """
        seq = self.seq
        b.blank()
        b.rule("Decision table")
        b.comment("Issued after the loops so a cell can override a loop.")

        for index, mode in enumerate(seq.modes, start=1):
            grouped = {}
            for point in seq.table.outputs():
                action = seq.table.action(point, mode.name)
                if action is None:
                    continue
                key = _action_key(action)
                if key in ("modulate", "hold", ""):
                    if (
                        key == "modulate"
                        and seq.loop_for(point) is None
                        and seq.reset_for(point) is None
                    ):
                        self.warnings.append(
                            "%s is set to 'modulate' in mode %s but no control "
                            "loop or reset schedule drives it, so nothing will "
                            "command it" % (point, mode.name)
                        )
                    continue
                grouped.setdefault(key, []).append(point)

            if not grouped:
                continue

            b.comment("Mode %d: %s" % (index, mode.name))
            for key, points in grouped.items():
                for chunk in _chunks(points, 16):
                    stmt = self._grouped_action(chunk, key)
                    b.code('IF("$%s".EQ.%.1f) THEN %s'
                           % (mode_local, index, stmt))

    def _grouped_action(self, points, action) -> str:
        names = ",".join(resolve(p, self.seq) for p in points)
        if action in ("on", "off", "auto", "fast", "slow"):
            return "%s(%s)" % (action.upper(), names)
        if action == "open":
            return "SET(100.0,%s)" % names
        if action == "closed":
            return "SET(0.0,%s)" % names
        try:
            number = float(action)
        except ValueError:
            raise CompileError(
                "%r is not an action for %s; use one of %s, or a number"
                % (action, ", ".join(points), ", ".join(sorted(CELL_ACTIONS)))
            )
        return "SET(%s,%s)" % (_num(number), names)

    def _emit_rules(self, b: Builder) -> None:
        b.blank()
        b.rule("Additional rules")
        for rule in self.seq.rules:
            if rule.description:
                b.comment(rule.description)
            if rule.when is None:
                for point, action in rule.then:
                    stmt = render_action(point, action, seq=self.seq)
                    if stmt:
                        b.code(stmt)
                continue

            test = render_condition(rule.when, self.seq)
            then_stmts = [render_action(p, a, seq=self.seq) for p, a in rule.then]
            else_stmts = [
                render_action(p, a, seq=self.seq) for p, a in rule.otherwise
            ]
            then_stmts = [s for s in then_stmts if s]
            else_stmts = [s for s in else_stmts if s]

            if len(then_stmts) == 1 and len(else_stmts) == 1:
                b.code("IF(%s) THEN %s ELSE %s"
                       % (test, then_stmts[0], else_stmts[0]))
                continue
            # PPCL allows only one statement per branch, so multiple actions
            # become separate lines repeating the test.
            for stmt in then_stmts:
                b.code("IF(%s) THEN %s" % (test, stmt))
            if else_stmts:
                inverse = _invert(rule.when, self.seq)
                for stmt in else_stmts:
                    b.code("IF(%s) THEN %s" % (inverse, stmt))


def _invert(cond, seq) -> str:
    """Render the logical inverse of a condition."""
    if isinstance(cond, Compare):
        flipped = {
            "<": ">=", "<=": ">", ">": "<=", ">=": "<",
            "=": "!=", "==": "!=", "!=": "=", "<>": "=",
            "is": "is not", "is not": "is",
        }.get(cond.op.strip().lower())
        if flipped:
            return render_condition(Compare(cond.left, flipped, cond.right), seq)
    raise CompileError(
        "cannot invert this condition for an ELSE with several actions; "
        "split it into separate rules"
    )


def _num(value) -> str:
    return "%.1f" % value if float(value) == int(value) else "%g" % value


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def compile_sequence(seq: Sequence):
    """Compile ``seq``. Returns ``(ppcl_text, warnings)``."""
    c = Compiler(seq)
    return c.compile(), c.warnings
