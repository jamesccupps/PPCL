"""Compile a block diagram to PPCL.

The interesting part is not the traversal, it is deciding what becomes a line
and what stays an expression.

A naive function-block compiler gives every block a variable and every block a
statement. On a field panel that is the wrong trade twice over: PPCL allows
only sixteen local variables per program, and a panel evaluates a fixed number
of lines per second shared across every program it runs, so lines are the
scarce resource. A diagram that compiles to sixty statements of single-step
arithmetic is slower and less maintainable than the same logic written by
hand, and no engineer will accept it.

So values flow as **expressions** and only condense into a local when there is
a reason:

* the value feeds more than one block, so recomputing it would cost operands;
* the block must persist state between passes (a latch, a delay);
* the block's own PPCL command writes into a point (LOOP, TABLE, MIN, DBSWIT);
* the expression is about to exceed the statement's operand or operator limit;
* the engineer named the block, which is taken as "I want to watch this".

That last one matters more than it looks. A named block becomes a named local,
so the wire on the drawing has a name that can be looked up at the panel. It
is the only debugging affordance that survives the trip from this canvas to a
technician standing in a mechanical room.

Feedback is allowed, but only through a block that genuinely holds state. A
loop of pure logic has no defined answer and is refused, naming the blocks in
the cycle. A loop through a latch or a delay reads the previous pass's value,
which is what feedback means on a scanning controller, and the compiler says
so in a comment.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import linter, spec
from .. import parser as ppcl_parser
from ..generator import Builder
from .catalog import CATALOG, BlockError
from .model import Diagram

#: A single LOCAL statement declares at most sixteen names. Transcribed from
#: the Insight Program Editor help: "A program can have an unlimited number of
#: local points, however, a statement can only reference up to 16 local points
#: at a time." So the sixteen is a *per statement* limit, not a per program
#: one, and a program that needs more simply emits another LOCAL statement.
LOCALS_PER_STATEMENT = 16

#: INFERRED, not transcribed. Siemens says "unlimited", which is true of the
#: syntax and false of the panel -- every local costs memory and every LOCAL
#: statement costs a line out of the round-robin budget shared by every
#: program in the panel. This ceiling exists so a runaway diagram fails with
#: an explanation instead of emitting a program nobody would accept. Raise it
#: if a real diagram ever justifies it; nothing in the manual forbids that.
MAX_LOCALS = 64


@dataclass
class Value:
    """A value flowing along a wire, as PPCL source.

    ``operands`` and ``operators`` are carried so the compiler can tell when a
    statement is about to exceed the limits in :mod:`ppcl.spec` and condense
    the expression into a local before it does.
    """

    text: str
    operands: int = 1
    operators: int = 0
    is_point: bool = False
    digital: bool = False
    #: Source for the logical inverse, when it can be produced for free.
    inverse: str = None


# --------------------------------------------------------------------------
# Compilation context
# --------------------------------------------------------------------------


class Context:
    """The state a block emitter works against."""

    def __init__(self, diagram: Diagram, firmware=spec.Firmware.APOGEE):
        self.diagram = diagram
        self.firmware = firmware
        self.builder = Builder(start=diagram.start_line, step=diagram.line_step)
        self.warnings = []
        self.locals = []
        self.values = {}          # (block_id, pin) -> Value
        self._local_seq = 0
        self._current = None
        self._predeclared = {}

    # -- diagnostics -------------------------------------------------------

    def warn(self, text):
        if text not in self.warnings:
            self.warnings.append(text)

    # -- emission ----------------------------------------------------------

    def emit(self, text):
        self.builder.code(text)

    def comment(self, text=""):
        self.builder.comment(text)

    # -- parameters --------------------------------------------------------

    def text(self, block, name, default=""):
        value = block.params.get(name, None)
        if value is None or value == "":
            bt = CATALOG.get(block.type)
            param = bt.param(name) if bt else None
            if default != "":
                return str(default)
            return str(param.default) if param and param.default != "" else ""
        return str(value).strip()

    def num(self, block, name, default=0.0):
        raw = self.text(block, name, default)
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise BlockError(
                "%r is not a number for %s" % (raw, name), block.id
            )

    def time(self, block, name, default="0:00"):
        raw = self.text(block, name, default)
        if ":" not in raw:
            raise BlockError(
                "%r is not a time; write it as HH:MM" % raw, block.id
            )
        hh, _, mm = raw.partition(":")
        try:
            hours, minutes = int(hh), int(mm)
        except ValueError:
            raise BlockError("%r is not a time" % raw, block.id)
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            raise BlockError(
                "%s is not a valid time of day" % raw, block.id
            )
        return "%d:%02d" % (hours, minutes)

    def pairs(self, block, name):
        """Parse breakpoint text -- one ``x,y`` pair per line."""
        out = []
        for line in self.text(block, name, "").replace(";", "\n").split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.replace("->", ",").split(",")]
            if len(parts) != 2:
                raise BlockError(
                    "%r is not an x,y breakpoint pair" % line, block.id
                )
            try:
                out.append((float(parts[0]), float(parts[1])))
            except ValueError:
                raise BlockError(
                    "%r is not an x,y breakpoint pair" % line, block.id
                )
        return out

    # -- values ------------------------------------------------------------

    @staticmethod
    def fmt(value):
        number = float(value)
        return "%.1f" % number if number == int(number) else "%g" % number

    def const(self, value):
        return Value(self.fmt(value), operands=1, operators=0)

    def expr(self, text, operands=1, operators=0, digital=False,
             is_point=False, inverse=None):
        return Value(text, operands, operators, is_point, digital, inverse)

    def render_point(self, name):
        """Quote a point reference the way PPCL requires."""
        text = str(name).strip()
        if text.startswith('"'):
            return text
        bare = text.lstrip("$")
        if len(bare) > spec.UNQUOTED_NAME_MAX or not bare.isalnum():
            return '"%s"' % text
        return text

    def point_value(self, name, digital=False):
        text = self.render_point(name)
        return Value(text, operands=1, operators=0, is_point=True,
                     digital=digital,
                     inverse="%s.EQ.0.0" % text if digital else None)

    def set_out(self, block, pin, value):
        """Record a block's output, condensing it into a local if needed."""
        if not value.is_point:
            if block.label:
                value = self.materialize(value, block, "V")
            elif self.diagram.fanout(block.id, pin) > 1:
                value = self.materialize(value, block, "V")
        self.values[(block.id, pin)] = value

    def value_in(self, block, pin, optional=False):
        wire = self.diagram.inputs_of(block.id).get(pin)
        bt = CATALOG[block.type]
        declared = bt.input(pin)
        if wire is None:
            if optional or (declared is not None and not declared.required):
                return None
            raise BlockError(
                "the %r input of this %s is not wired"
                % (declared.label if declared else pin, bt.label),
                block.id,
            )
        value = self.values.get((wire.src, wire.src_pin))
        if value is None:
            raise BlockError(
                "the block feeding %r produced no value" % pin, block.id
            )
        return value

    def inputs_present(self, block, pins):
        out = []
        for pin in pins:
            value = self.value_in(block, pin, optional=True)
            if value is not None:
                out.append(value)
        return out

    def would_repeat(self, value):
        """True when using this value twice would be worth avoiding."""
        return not value.is_point and value.operands > 1

    # -- locals ------------------------------------------------------------

    def predeclare(self, block):
        """Allocate a block's output local ahead of the traversal.

        Used for the block that closes a feedback loop: its consumers are
        emitted first and must reference a local that already exists.
        """
        value = self.new_local(block, CATALOG[block.type].label)
        value.digital = True
        self._predeclared[block.id] = value
        for pin in CATALOG[block.type].outputs:
            self.values[(block.id, pin.name)] = value
        return value

    def new_local(self, block, hint):
        """Allocate a local variable, named after the block when possible."""
        existing = self._predeclared.get(block.id)
        if existing is not None:
            return existing
        base = (block.label or hint or "V").upper()
        base = "".join(ch for ch in base if ch.isalnum()) or "V"
        name = base[:spec.UNQUOTED_NAME_MAX]
        if name in self.locals:
            self._local_seq += 1
            name = (base[:spec.UNQUOTED_NAME_MAX - 2]
                    + str(self._local_seq))[:spec.UNQUOTED_NAME_MAX]
            while name in self.locals:
                self._local_seq += 1
                name = (base[:spec.UNQUOTED_NAME_MAX - 2]
                        + str(self._local_seq))[:spec.UNQUOTED_NAME_MAX]
        if len(self.locals) >= MAX_LOCALS:
            raise BlockError(
                "this diagram needs more than %d local variables. PPCL does "
                "not forbid that, but every local costs panel memory and the "
                "declarations cost lines out of the budget shared by every "
                "program in the panel. Locals are used by latches, delays, "
                "PID loops, reset schedules, min/max, clamps, named blocks "
                "and any value wired to more than one place. Clear the name "
                "off blocks you do not need to watch, or split the diagram "
                "into two programs." % MAX_LOCALS,
                block.id,
            )
        self.locals.append(name)
        text = '"$%s"' % name
        return Value(text, operands=1, operators=0, is_point=True,
                     inverse="%s.EQ.0.0" % text)

    def materialize(self, value, block, hint):
        """Store an expression in a local so it can be reused or watched."""
        if value.is_point:
            return value
        target = self.new_local(block, hint)
        self.emit("%s = %s" % (target.text, value.text))
        target.digital = value.digital
        return target

    # -- expression building ----------------------------------------------

    def _budget(self, values, added_operators):
        """``(operands, operators)`` for a statement combining these values.

        Counted exactly as the Desigo CC help states it: "An expression adds
        one operator for each arithmetic operator, relational operator,
        logical operator, point reference, and value constant. Each additional
        point reference or value constant adds one operand." So every operand
        also counts against the operator ceiling, and parentheses are free.
        """
        operands = sum(v.operands for v in values)
        symbols = sum(v.operators for v in values) + added_operators
        return operands, operands + symbols

    def _fit(self, values, added_operators, block, hint):
        """Condense operands until the combined statement fits the limits.

        Rather than emit a statement the panel compiler will reject, the
        widest sub-expression is stored to a local and the expression rebuilt
        around it.
        """
        limit_operands = spec.MAX_OPERANDS[self.firmware]
        values = list(values)
        while True:
            operands, operators = self._budget(values, added_operators)
            if operands <= limit_operands and operators <= spec.MAX_OPERATORS:
                return values
            widest = max(range(len(values)), key=lambda i: values[i].operands)
            if values[widest].is_point:
                raise BlockError(
                    "this statement needs %d operands and %d operators, over "
                    "the limits of %d and %d, and cannot be split further. "
                    "Break the logic into two blocks."
                    % (operands, operators, limit_operands,
                       spec.MAX_OPERATORS),
                    block.id if block else None,
                )
            values[widest] = self.materialize(values[widest], block, hint)

    def arith(self, a, op, b, block=None):
        a, b = self._fit([a, b], 1, block, "N")
        return Value(
            "%s%s%s" % (self._arith_operand(a, op), op,
                        self._arith_operand(b, op)),
            a.operands + b.operands,
            a.operators + b.operators + 1,
        )

    @staticmethod
    def _arith_operand(value, op):
        """Parenthesise only where precedence would otherwise change meaning.

        Multiplication and division bind tighter than addition, and every
        relational or logical operator binds looser than all of them, so those
        cases need brackets. Parentheses cost nothing against the statement
        limits, but gratuitous ones make the generated program harder to read
        at the panel than the code it replaced.
        """
        if value.operators == 0:
            return value.text
        if value.digital or op in ("*", "/"):
            return "(%s)" % value.text
        return value.text

    def compare(self, a, op, b, block=None):
        a, b = self._fit([a, b], 1, block, "C")
        flipped = {".GT.": ".LE.", ".GE.": ".LT.", ".LT.": ".GE.",
                   ".LE.": ".GT.", ".EQ.": ".NE.", ".NE.": ".EQ."}[op]
        return Value(
            "%s%s%s" % (a.text, op, b.text),
            operands=a.operands + b.operands,
            operators=a.operators + b.operators + 1,
            digital=True,
            inverse="%s%s%s" % (a.text, flipped, b.text),
        )

    def join(self, op, values, digital=True, block=None):
        values = self._fit(values, len(values) - 1, block, "L")
        parts = [self._wrap(v) for v in values]
        inverse = None
        if all(v.inverse for v in values) and op in (".AND.", ".OR."):
            # De Morgan, so an inverted gate still costs no extra line.
            flip = ".OR." if op == ".AND." else ".AND."
            inverse = flip.join("(%s)" % v.inverse for v in values)
        return Value(
            op.join(parts),
            operands=sum(v.operands for v in values),
            operators=sum(v.operators for v in values) + len(values) - 1,
            digital=digital,
            inverse=inverse,
        )

    @staticmethod
    def _wrap(value):
        """Bracket a term of a logical expression.

        Relational operators already bind tighter than the logical ones, so
        this is for the reader rather than the compiler -- and parentheses are
        not counted against the operator limit.
        """
        return "(%s)" % value.text if value.operators else value.text

    def as_boolean(self, value):
        """Coerce a value to something usable as a condition."""
        if value.digital and value.operators > 0:
            return value
        if value.is_point:
            return Value(
                "%s.NE.0.0" % value.text, value.operands, value.operators + 1,
                digital=True, inverse="%s.EQ.0.0" % value.text,
            )
        return self.compare(value, ".NE.", self.const(0.0))

    def invert(self, value):
        """The logical inverse of a condition."""
        if value.inverse:
            return Value(value.inverse, value.operands, value.operators,
                         digital=True, inverse=value.text)
        materialized = self.materialize(value, self._current, "INV")
        return Value("%s.EQ.0.0" % materialized.text, 1, 1, digital=True,
                     inverse="%s.NE.0.0" % materialized.text)


# --------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------


def order_blocks(diagram: Diagram):
    """Topologically order the blocks.

    Returns ``(order, feedback_edges, deferred)``, where ``deferred`` names the
    stateful blocks whose output local must be allocated before the traversal
    starts.

    Kahn's algorithm, and when it stalls the remaining subgraph is a cycle. A
    cycle is legal only if it passes through a block that holds state, and the
    cut is made on that block's **outgoing** edges, not its incoming ones. The
    difference matters: the stateful block still runs after the inputs that
    decide its next value, while the consumers ahead of it in the loop read the
    local it wrote on the previous pass. That is what feedback means on a
    scanning controller. Cutting the inputs instead would emit the block before
    the values it needs exist.

    A cycle of pure logic has no fixed point and is refused.
    """
    index = {b.id: b for b in diagram.blocks}
    deps = {b.id: set() for b in diagram.blocks}
    for wire in diagram.wires:
        if wire.dst in deps and wire.src in index:
            deps[wire.dst].add(wire.src)

    order = []
    feedback = []
    deferred = []
    remaining = dict(deps)
    while remaining:
        ready = sorted(i for i, d in remaining.items() if not d)
        if not ready:
            # Only a stateful block that is itself inside a cycle can break
            # one. A stateful block merely waiting behind someone else's cycle
            # is no help, and cutting its edges would reorder the program for
            # no reason.
            cut = None
            for candidate in sorted(remaining):
                block_type = CATALOG.get(index[candidate].type)
                if block_type is None or not block_type.stateful:
                    continue
                if candidate in _upstream(remaining, candidate):
                    cut = candidate
                    break
            if cut is None:
                names = ", ".join(
                    "%s (%s)" % (index[i].label or i,
                                 CATALOG[index[i].type].label
                                 if index[i].type in CATALOG else index[i].type)
                    for i in sorted(remaining)
                )
                raise BlockError(
                    "these blocks feed each other in a loop with nothing to "
                    "hold state, so there is no value they could settle on: "
                    "%s. Insert a Latch or a Delay to break the loop, or "
                    "remove a wire." % names,
                    sorted(remaining)[0],
                )
            deferred.append(cut)
            # Cut only the edges that close the loop. A consumer that is not
            # itself upstream of the cut block keeps its dependency and is
            # ordered after it, so it reads this pass's value, not the last.
            upstream = _upstream(remaining, cut)
            for consumer in sorted(remaining):
                if cut in remaining[consumer] and consumer in upstream:
                    remaining[consumer].discard(cut)
                    feedback.append((cut, consumer))
            continue
        for i in ready:
            order.append(i)
            del remaining[i]
            for d in remaining.values():
                d.discard(i)
    return [index[i] for i in order], feedback, deferred


def _upstream(deps, start):
    """Every block ``start`` transitively depends on, ``start`` included when
    it is part of a cycle."""
    seen = set()
    stack = list(deps.get(start, ()))
    while stack:
        node = stack.pop()
        if node in seen or node not in deps:
            continue
        seen.add(node)
        stack.extend(deps[node])
    return seen


# --------------------------------------------------------------------------
# The compiler
# --------------------------------------------------------------------------


def compile_diagram(diagram: Diagram, firmware=spec.Firmware.APOGEE):
    """Compile ``diagram``. Returns ``(ppcl_text, warnings)``."""
    if not diagram.blocks:
        raise BlockError("the diagram is empty, so there is nothing to compile")

    for block in diagram.blocks:
        if block.type not in CATALOG:
            raise BlockError("unknown block type %r" % block.type, block.id)
    for wire in diagram.wires:
        _check_wire(diagram, wire)

    outputs = [b for b in diagram.blocks
               if CATALOG[b.type].is_output]
    if not outputs:
        raise BlockError(
            "nothing in this diagram commands a point, so compiling it would "
            "produce a program that does nothing. Add a Command point or "
            "Write value block."
        )

    ctx = Context(diagram, firmware=firmware)
    order, feedback, deferred = order_blocks(diagram)

    # A block whose output closes a feedback loop needs its local to exist
    # before anything reads it, because the reader is emitted first and gets
    # the previous pass's value.
    for block_id in deferred:
        block = diagram.block(block_id)
        ctx.predeclare(block)

    body = Builder(start=diagram.start_line, step=diagram.line_step)
    ctx.builder = body
    for block in order:
        ctx._current = block
        bt = CATALOG[block.type]
        if block.note:
            for line in block.note.split("\n"):
                ctx.comment(line)
        bt.emit(ctx, block)

    # The LOCAL statement has to precede everything that uses it, but the set
    # of locals is only known once every block has been emitted.
    out = Builder(start=diagram.start_line, step=diagram.line_step)
    out.items.extend(_header_items(ctx, diagram, feedback))
    out.blank()
    out.rule("Declarations and power restart")
    # Sixteen names per LOCAL statement is a compiler limit, so a diagram that
    # needs more declares them across several statements rather than being
    # refused.
    for start in range(0, len(ctx.locals), LOCALS_PER_STATEMENT):
        chunk = ctx.locals[start:start + LOCALS_PER_STATEMENT]
        out.code("LOCAL(%s)" % ",".join('"%s"' % n for n in chunk))
    out.label("INIT")
    out.code("ONPWRT({init})", init="INIT")
    out.blank()
    out.rule("Main loop")
    out.label("MAIN")
    out.items.extend(body.items)
    out.blank()
    out.rule("Close the main loop")
    out.goto("MAIN")

    text = out.render(firmware=firmware)
    for number, length in getattr(out, "long_lines", []):
        ctx.warn(
            "line %d is %d characters, over the %d-character MMI limit; it "
            "will load fine from a workstation but cannot be typed at the "
            "panel" % (number, length, spec.MMI_LINE_LIMIT[firmware])
        )
    return text, ctx.warnings


def _check_wire(diagram, wire):
    src = diagram.block(wire.src)
    dst = diagram.block(wire.dst)
    if src is None or dst is None:
        raise BlockError(
            "a wire refers to a block that is not on the canvas", wire.dst
        )
    src_type = CATALOG.get(src.type)
    dst_type = CATALOG.get(dst.type)
    if src_type is None or src_type.output(wire.src_pin) is None:
        raise BlockError(
            "%s has no output called %r" % (src.type, wire.src_pin), src.id
        )
    if dst_type is None or dst_type.input(wire.dst_pin) is None:
        raise BlockError(
            "%s has no input called %r" % (dst.type, wire.dst_pin), dst.id
        )
    landing = [
        w for w in diagram.wires
        if w.dst == wire.dst and w.dst_pin == wire.dst_pin
    ]
    if len(landing) > 1:
        raise BlockError(
            "the %r input of this block has %d wires on it; an input takes "
            "one" % (wire.dst_pin, len(landing)), wire.dst
        )


def _header_items(ctx, diagram, feedback):
    b = Builder()
    b.comment("=" * 58)
    b.comment("Title: %s" % diagram.name)
    if diagram.equipment:
        b.comment("Equipment: %s" % diagram.equipment)
    if diagram.author:
        b.comment("Author: %s" % diagram.author)
    b.comment("Version: %s" % diagram.version)
    b.comment("")
    b.comment("Generated from a block diagram. Edit the diagram and")
    b.comment("recompile rather than editing this program by hand.")
    if diagram.description:
        b.comment("")
        for line in diagram.description.split("\n"):
            b.comment(line)
    if feedback:
        b.comment("")
        b.comment("Feedback paths, which read the previous pass's value:")
        for source, target in feedback:
            src = diagram.block(source)
            dst = diagram.block(target)
            b.comment(
                "  %s -> %s"
                % (src.label or src.id, dst.label or dst.id)
            )
        ctx.warn(
            "%d feedback path(s) read the previous pass's value. That is how "
            "feedback works on a scanning controller, but check the sequence "
            "expects a one-pass delay." % len(feedback)
        )
    b.comment("=" * 58)
    return b.items


def compile_and_lint(diagram: Diagram, firmware=spec.Firmware.APOGEE):
    """Compile, then lint the result. The UI never shows unchecked code."""
    text, warnings = compile_diagram(diagram, firmware=firmware)
    program = ppcl_parser.parse(text, name=diagram.name)
    diagnostics = linter.lint(program, firmware=firmware)
    return text, warnings, diagnostics
