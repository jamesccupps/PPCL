"""The block catalog: what you can drop on the canvas, and what it emits.

Each entry declares its pins, its parameters, the help text the UI shows on
hover, and a function that emits PPCL for it. Keeping emission next to the
declaration is deliberate -- a block whose help text and generated code drift
apart is worse than no block at all.

Two properties drive the whole design:

**Pure blocks build expressions, they do not consume lines.** An AND of two
comparisons is one PPCL expression, not three statements and two locals. So a
twelve-block diagram of ordinary logic compiles to a handful of statements
that read like something an engineer wrote, which matters because someone will
eventually maintain the result at a panel with no diagram in front of them.

**Anything that must persist, be timed, or be watched gets a real local.** A
latch, a PID, a delay, a value with more than one consumer, or any block the
engineer has named -- each becomes a named local variable. That is what makes
the diagram debuggable on the panel: the wire you clicked on the drawing has a
name you can look up in the point list.

Every emitted command traces to a signature in :mod:`ppcl.spec`, which is
manual-derived. No block invents syntax.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import spec


class BlockError(Exception):
    """A diagram that cannot be compiled, with the offending block id."""

    def __init__(self, message, block_id=None):
        super().__init__(message)
        self.message = message
        self.block_id = block_id


# --------------------------------------------------------------------------
# Declarations
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Pin:
    """One input or output connector."""

    name: str
    label: str
    kind: str = "analog"      # "analog" | "digital" | "any"
    doc: str = ""
    required: bool = True
    #: An input that may be wired more than once, forming a variadic list.
    repeat: bool = False


@dataclass(frozen=True)
class Param:
    """A setting edited in the block's own form, not wired."""

    name: str
    label: str
    kind: str = "number"      # number | text | point | choice | time | pairs
    default: object = ""
    choices: tuple = ()
    doc: str = ""


@dataclass(frozen=True)
class BlockType:
    """One kind of block."""

    name: str
    label: str
    category: str
    summary: str
    inputs: tuple = ()
    outputs: tuple = ()
    params: tuple = ()
    #: Emitter -- ``fn(ctx, block)``.
    emit: object = None
    #: Holds state between passes, so a wire may legitimately loop back into
    #: it without the diagram being a combinational cycle.
    stateful: bool = False
    #: Emits a command whose behaviour depends on wall-clock time. These are
    #: always placed unconditionally at the top level of the main loop.
    time_based: bool = False
    #: Commands real points rather than producing a value.
    is_output: bool = False
    #: Longer help, shown in the block inspector.
    help: str = ""
    #: PPCL commands this block can emit, for the "what does this become" hint.
    emits: tuple = ()
    manual: str = ""

    def input(self, name):
        for p in self.inputs:
            if p.name == name:
                return p
        return None

    def output(self, name):
        for p in self.outputs:
            if p.name == name:
                return p
        return None

    def param(self, name):
        for p in self.params:
            if p.name == name:
                return p
        return None


CATALOG = {}
CATEGORIES = [
    "Inputs", "Logic", "Math", "Control", "Timing", "Outputs", "Notes",
]


def block_type(name, label, category, summary, **kw):
    """Register a block type. The decorated function is its emitter."""

    def wrap(fn):
        CATALOG[name] = BlockType(
            name=name, label=label, category=category, summary=summary,
            emit=fn, **kw
        )
        return fn

    return wrap


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


@block_type(
    "point_in", "Point", "Inputs",
    "Read a point from the panel database.",
    outputs=(Pin("out", "value", "any", "The point's current value"),),
    params=(
        Param("point", "Point name", "point", "",
              doc="The name exactly as it appears in the panel."),
        Param("kind", "Type", "choice", "analog", ("analog", "digital"),
              doc="Digital points read as 1.0 when ON and 0.0 when OFF."),
    ),
    help="The starting point for most logic. Any reference the panel accepts "
         "works here, including a colon-qualified FLN subpoint such as "
         "Dev201:DAY_CLG_STPT and a BACnet object such as BAC_22222_AI_1.",
    manual="125-1896 Rev.5 ch.2, point referencing",
)
def _emit_point_in(ctx, block):
    name = ctx.text(block, "point")
    if not name:
        raise BlockError("this Point block has no point name", block.id)
    digital = ctx.text(block, "kind", "analog") == "digital"
    ctx.set_out(block, "out", ctx.point_value(name, digital=digital))


@block_type(
    "constant", "Constant", "Inputs",
    "A fixed number.",
    outputs=(Pin("out", "value", "analog"),),
    params=(Param("value", "Value", "number", 0.0),),
    help="Setpoints, limits and thresholds. A constant costs one operand in "
         "whatever statement uses it and never costs a line of its own.",
)
def _emit_constant(ctx, block):
    ctx.set_out(block, "out", ctx.const(ctx.num(block, "value", 0.0)))


@block_type(
    "resident", "System value", "Inputs",
    "A resident point maintained by the panel itself.",
    outputs=(Pin("out", "value", "any"),),
    params=(
        Param("which", "Value", "choice", "TIME",
              ("TIME", "CRTIME", "DAY", "DAYOFM", "MONTH", "SECNDS",
               "SECND1", "SECND2", "SECND3", "ALMCNT", "LINK", "$BATT"),
              doc="TIME is military time; CRTIME is decimal hours."),
    ),
    help="Resident points live permanently in each field panel and cannot be "
         "commanded. Each panel keeps its own set, so a resident point cannot "
         "be used across the network.",
    manual="Desigo CC engineering help, 'Resident Points'",
)
def _emit_resident(ctx, block):
    which = ctx.text(block, "which", "TIME").upper()
    if which not in spec.RESIDENT_POINTS and not spec.is_resident(which):
        ctx.warn("%s is not a resident point this spec knows about" % which)
    ctx.set_out(block, "out", ctx.expr(which, operands=1, digital=False))


# --------------------------------------------------------------------------
# Logic
# --------------------------------------------------------------------------


def _logic(op_name, op_text, summary, help_text):
    @block_type(
        op_name, op_name.upper(), "Logic", summary,
        inputs=(
            Pin("a", "a", "digital"),
            Pin("b", "b", "digital"),
            Pin("c", "c", "digital", required=False),
            Pin("d", "d", "digital", required=False),
        ),
        outputs=(Pin("out", "out", "digital"),),
        help=help_text,
        manual="125-1896 Rev.5 Table 2-4, logical operators",
    )
    def _emit(ctx, block, _op=op_text):
        parts = [
            ctx.as_boolean(v)
            for v in ctx.inputs_present(block, ("a", "b", "c", "d"))
        ]
        if len(parts) < 2:
            raise BlockError(
                "%s needs at least two inputs wired" % op_name.upper(), block.id
            )
        ctx.set_out(block, "out", ctx.join(_op, parts, digital=True))

    return _emit


_logic("and", ".AND.", "True when every wired input is true.",
       "Unwired inputs are ignored, so a two-input AND and a four-input AND "
       "are the same block.")
_logic("or", ".OR.", "True when any wired input is true.",
       "Unwired inputs are ignored.")
_logic("xor", ".XOR.", "True when exactly one of two inputs is true.",
       "Useful for detecting disagreement between a command and its status.")
_logic("nand", ".NAND.", "False only when every wired input is true.",
       "The inverse of AND.")


@block_type(
    "not", "NOT", "Logic",
    "Inverts a digital value.",
    inputs=(Pin("in", "in", "digital"),),
    outputs=(Pin("out", "out", "digital"),),
    help="Compiles to a comparison against zero, so it costs no line of its "
         "own. PPCL has no NOT operator; this is how you write one.",
)
def _emit_not(ctx, block):
    value = ctx.value_in(block, "in")
    ctx.set_out(block, "out", ctx.compare(value, ".EQ.", ctx.const(0.0)))


@block_type(
    "compare", "Compare", "Logic",
    "Compare two values.",
    inputs=(Pin("a", "a", "any"), Pin("b", "b", "any")),
    outputs=(Pin("out", "out", "digital", "1.0 when the test holds"),),
    params=(
        Param("op", "Test", "choice", ">",
              (">", ">=", "<", "<=", "=", "!="),
              doc="Relational operators sit at precedence level 6, below "
                  "arithmetic and above the logical operators."),
    ),
    help="The workhorse. Wire a sensor to a and a setpoint to b. Remember "
         "that comparing an analog value for exact equality rarely does what "
         "you want -- use a deadband switch instead.",
    manual="125-1896 Rev.5 Table 2-3, relational operators",
)
def _emit_compare(ctx, block):
    op = _RELATIONAL.get(ctx.text(block, "op", ">"))
    if op is None:
        raise BlockError(
            "%r is not a comparison" % ctx.text(block, "op"), block.id
        )
    ctx.set_out(
        block, "out",
        ctx.compare(ctx.value_in(block, "a"), op, ctx.value_in(block, "b")),
    )


_RELATIONAL = {
    ">": ".GT.", ">=": ".GE.", "<": ".LT.", "<=": ".LE.",
    "=": ".EQ.", "==": ".EQ.", "!=": ".NE.", "<>": ".NE.",
}


@block_type(
    "between", "In range", "Logic",
    "True while a value sits inside a range.",
    inputs=(Pin("in", "in", "analog"),),
    outputs=(Pin("out", "out", "digital"),),
    params=(
        Param("low", "Low", "number", 0.0),
        Param("high", "High", "number", 100.0),
        Param("inclusive", "Include limits", "choice", "low",
              ("low", "both", "neither"),
              doc="'low' means low <= x < high, the usual choice for a "
                  "schedule window."),
    ),
    help="Compiles to a single expression with two comparisons. For an "
         "occupancy window, wire the TIME system value in.",
)
def _emit_between(ctx, block):
    value = ctx.value_in(block, "in")
    low = ctx.const(ctx.num(block, "low", 0.0))
    high = ctx.const(ctx.num(block, "high", 100.0))
    mode = ctx.text(block, "inclusive", "low")
    lo_op = ".GE." if mode in ("low", "both") else ".GT."
    hi_op = ".LE." if mode == "both" else ".LT."
    if ctx.would_repeat(value):
        value = ctx.materialize(value, block, "RANGE")
    ctx.set_out(
        block, "out",
        ctx.join(
            ".AND.",
            [ctx.compare(value, lo_op, low), ctx.compare(value, hi_op, high)],
            digital=True,
        ),
    )


@block_type(
    "select", "Select", "Logic",
    "Pass one of two values through, chosen by a digital input.",
    inputs=(
        Pin("sel", "select", "digital", "Chooses which input passes"),
        Pin("a", "if true", "any"),
        Pin("b", "if false", "any"),
    ),
    outputs=(Pin("out", "out", "any"),),
    help="Two setpoints and an occupancy flag is the classic use. Compiles "
         "to one IF/THEN/ELSE assigning a local.",
)
def _emit_select(ctx, block):
    sel = ctx.as_boolean(ctx.value_in(block, "sel"))
    a = ctx.value_in(block, "a")
    b = ctx.value_in(block, "b")
    out = ctx.new_local(block, "SEL")
    ctx.emit("IF(%s) THEN %s = %s ELSE %s = %s"
             % (sel.text, out.text, a.text, out.text, b.text))
    ctx.set_out(block, "out", out)


@block_type(
    "latch", "Latch", "Logic",
    "Sets on one input, clears on another, and holds in between.",
    inputs=(
        Pin("set", "set", "digital"),
        Pin("reset", "reset", "digital"),
    ),
    outputs=(Pin("out", "latched", "digital"),),
    params=(
        Param("priority", "Reset wins", "choice", "reset", ("reset", "set"),
              doc="Which input wins when both are true at the same instant. "
                  "For a safety, reset should win only if you have thought "
                  "about it -- normally set wins so the trip is not missed."),
    ),
    stateful=True,
    help="This is how you write a safety trip that stays tripped. A latch "
         "with no reset path is the single most common PPCL field defect, so "
         "the reset input is required. Its state survives between passes "
         "because it lives in a local variable.",
)
def _emit_latch(ctx, block):
    set_in = ctx.as_boolean(ctx.value_in(block, "set"))
    reset_in = ctx.as_boolean(ctx.value_in(block, "reset"))
    out = ctx.new_local(block, "LATCH")
    order = [(set_in, "1.0"), (reset_in, "0.0")]
    if ctx.text(block, "priority", "reset") == "set":
        order.reverse()
    for cond, value in order:
        ctx.emit("IF(%s) THEN %s = %s" % (cond.text, out.text, value))
    ctx.set_out(block, "out", out)


@block_type(
    "deadband", "Deadband switch", "Logic",
    "A software thermostat with separate on and off limits.",
    inputs=(Pin("in", "in", "analog"),),
    outputs=(Pin("out", "out", "digital"),),
    params=(
        Param("low", "Low limit", "number", 68.0),
        Param("high", "High limit", "number", 72.0),
        Param("sense", "Sense", "choice", "0", ("0", "1"),
              doc="0 = ON above high, OFF below low. "
                  "1 = ON below low, OFF above high (heating sense)."),
    ),
    stateful=True,
    help="Use this instead of a bare comparison whenever the output starts "
         "and stops equipment. A comparison with no deadband will short-cycle "
         "a compressor on sensor noise.",
    emits=("DBSWIT",),
    manual="125-1896 Rev.5, DBSWIT",
)
def _emit_deadband(ctx, block):
    value = ctx.materialize(ctx.value_in(block, "in"), block, "DBIN")
    out = ctx.new_local(block, "DB")
    ctx.emit(
        "DBSWIT(%s,%s,%s,%s,%s)"
        % (ctx.text(block, "sense", "0"), value.text,
           ctx.fmt(ctx.num(block, "low", 68.0)),
           ctx.fmt(ctx.num(block, "high", 72.0)), out.text)
    )
    ctx.set_out(block, "out", out)


# --------------------------------------------------------------------------
# Math
# --------------------------------------------------------------------------


def _arith(name, label, op, summary, help_text):
    @block_type(
        name, label, "Math", summary,
        inputs=(Pin("a", "a", "analog"), Pin("b", "b", "analog")),
        outputs=(Pin("out", "out", "analog"),),
        help=help_text,
        manual="125-1896 Rev.5 Table 2-2, arithmetic operators",
    )
    def _emit(ctx, block, _op=op):
        a = ctx.value_in(block, "a")
        b = ctx.value_in(block, "b")
        ctx.set_out(block, "out", ctx.arith(a, _op, b))

    return _emit


_arith("add", "Add", "+", "a plus b.", "")
_arith("subtract", "Subtract", "-", "a minus b.",
       "Wire the measured value to a and the setpoint to b to get error.")
_arith("multiply", "Multiply", "*", "a times b.", "")
_arith("divide", "Divide", "/", "a divided by b.",
       "Nothing in PPCL guards against division by zero. If b can reach "
       "zero, gate this block or clamp b first.")


@block_type(
    "function", "Function", "Math",
    "A single-argument arithmetic function.",
    inputs=(Pin("in", "in", "analog"),),
    outputs=(Pin("out", "out", "analog"),),
    params=(
        Param("fn", "Function", "choice", "SQRT",
              ("SQRT", "LOG", "EXP", "SIN", "COS", "TAN", "ATN", "COM",
               "ALMPRI", "TOTAL"),
              doc="Trigonometric arguments and results are in degrees."),
    ),
    help="ALMPRI and TOTAL are special functions and take a point, not an "
         "expression, so wire a Point block straight into them. Note the "
         "arc-tangent is ATN; some Siemens documentation misspells it ARC.",
    manual="125-1896 Rev.5 Table 2-6",
)
def _emit_function(ctx, block):
    fn = ctx.text(block, "fn", "SQRT").upper()
    if fn not in spec.FUNCTIONS:
        raise BlockError(
            "%s is not a PPCL function%s"
            % (fn, "; the arc-tangent is ATN, not ARC" if fn == "ARC" else ""),
            block.id,
        )
    value = ctx.value_in(block, "in")
    if fn in ("ALMPRI", "TOTAL") and not value.is_point:
        raise BlockError(
            "%s takes a point, not a calculated value; wire a Point block "
            "into it" % fn, block.id
        )
    ctx.set_out(
        block, "out",
        ctx.expr("%s(%s)" % (fn, value.text),
                 operands=value.operands,
                 operators=value.operators + 1),
    )


@block_type(
    "extreme", "Min / Max", "Math",
    "The smallest or largest of the wired inputs.",
    inputs=(
        Pin("a", "a", "analog"), Pin("b", "b", "analog"),
        Pin("c", "c", "analog", required=False),
        Pin("d", "d", "analog", required=False),
    ),
    outputs=(Pin("out", "out", "analog"),),
    params=(
        Param("which", "Pick", "choice", "MIN", ("MIN", "MAX")),
    ),
    help="MIN and MAX are commands that write into a point, so this block "
         "always costs one line and one local. Common uses: lowest zone "
         "temperature across several sensors, or highest demand signal.",
    emits=("MIN", "MAX"),
)
def _emit_extreme(ctx, block):
    values = ctx.inputs_present(block, ("a", "b", "c", "d"))
    if len(values) < 2:
        raise BlockError("Min/Max needs at least two inputs wired", block.id)
    out = ctx.new_local(block, ctx.text(block, "which", "MIN"))
    ctx.emit(
        "%s(%s,%s)"
        % (ctx.text(block, "which", "MIN").upper(), out.text,
           ",".join(v.text for v in values))
    )
    ctx.set_out(block, "out", out)


@block_type(
    "limit", "Clamp", "Math",
    "Hold a value between a low and a high limit.",
    inputs=(Pin("in", "in", "analog"),),
    outputs=(Pin("out", "out", "analog"),),
    params=(
        Param("low", "Low limit", "number", 0.0),
        Param("high", "High limit", "number", 100.0),
    ),
    help="Two commands: MAX against the low limit, then MIN against the high. "
         "Clamp anything you are about to send to an actuator.",
    emits=("MIN", "MAX"),
)
def _emit_limit(ctx, block):
    value = ctx.value_in(block, "in")
    low = ctx.num(block, "low", 0.0)
    high = ctx.num(block, "high", 100.0)
    if low >= high:
        raise BlockError(
            "the low limit (%g) must be below the high limit (%g)"
            % (low, high), block.id
        )
    out = ctx.new_local(block, "CLAMP")
    ctx.emit("MAX(%s,%s,%s)" % (out.text, value.text, ctx.fmt(low)))
    ctx.emit("MIN(%s,%s,%s)" % (out.text, out.text, ctx.fmt(high)))
    ctx.set_out(block, "out", out)


@block_type(
    "scale", "Scale", "Math",
    "Map an input range onto an output range, linearly.",
    inputs=(Pin("in", "in", "analog"),),
    outputs=(Pin("out", "out", "analog"),),
    params=(
        Param("in_low", "Input low", "number", 0.0),
        Param("in_high", "Input high", "number", 100.0),
        Param("out_low", "Output low", "number", 0.0),
        Param("out_high", "Output high", "number", 100.0),
    ),
    help="Compiles to arithmetic, not a table, so the mapping stays linear "
         "outside the input range too. Use a Reset schedule if you need it "
         "clamped or need more than two breakpoints.",
)
def _emit_scale(ctx, block):
    in_low = ctx.num(block, "in_low", 0.0)
    in_high = ctx.num(block, "in_high", 100.0)
    out_low = ctx.num(block, "out_low", 0.0)
    out_high = ctx.num(block, "out_high", 100.0)
    if in_low == in_high:
        raise BlockError(
            "the input range is zero wide, so the slope is undefined", block.id
        )
    slope = (out_high - out_low) / (in_high - in_low)
    value = ctx.value_in(block, "in")
    # A falling slope is written as (low - x) * |slope| rather than
    # (x - low) * -|slope|, so the source never contains an operator pair
    # like "*-2.5" that a panel compiler may read differently than intended.
    if slope < 0:
        head = "(%s-%s)" % (ctx.fmt(in_low), value.text)
        slope = -slope
    else:
        head = "(%s-%s)" % (value.text, ctx.fmt(in_low))
    ctx.set_out(
        block, "out",
        ctx.expr(
            "%s*%s+%s" % (head, ctx.fmt(round(slope, 6)), ctx.fmt(out_low)),
            operands=value.operands + 3,
            operators=value.operators + 3,
        ),
    )


@block_type(
    "reset", "Reset schedule", "Math",
    "A piecewise-linear transfer curve, up to seven breakpoints.",
    inputs=(Pin("in", "source", "analog"),),
    outputs=(Pin("out", "out", "analog"),),
    params=(
        Param("points", "Breakpoints", "pairs", "0,0\n100,100",
              doc="One x,y pair per line, x ascending. Outside the first and "
                  "last pair the output holds flat."),
    ),
    help="The standard outside-air reset. TABLE requires the source to be a "
         "point, so a calculated input is stored to a local first.",
    emits=("TABLE",),
    manual="125-1896 Rev.5, TABLE",
)
def _emit_reset(ctx, block):
    pairs = ctx.pairs(block, "points")
    if not pairs:
        raise BlockError("a reset schedule needs at least one breakpoint",
                         block.id)
    if len(pairs) > 7:
        raise BlockError(
            "TABLE accepts at most 7 breakpoints; this one has %d"
            % len(pairs), block.id
        )
    xs = [p[0] for p in pairs]
    if any(b <= a for a, b in zip(xs, xs[1:])):
        raise BlockError(
            "breakpoints must ascend; got %s"
            % ", ".join(ctx.fmt(x) for x in xs), block.id
        )
    source = ctx.materialize(ctx.value_in(block, "in"), block, "RSTIN")
    out = ctx.new_local(block, "RESET")
    body = ",".join("%s,%s" % (ctx.fmt(x), ctx.fmt(y)) for x, y in pairs)
    ctx.emit("TABLE(%s,%s,%s)" % (source.text, out.text, body))
    ctx.set_out(block, "out", out)


# --------------------------------------------------------------------------
# Control
# --------------------------------------------------------------------------


@block_type(
    "pid", "PID loop", "Control",
    "Closed-loop control of one output from one measurement.",
    inputs=(
        Pin("pv", "measured", "analog"),
        Pin("sp", "setpoint", "analog"),
    ),
    outputs=(Pin("out", "output", "analog"),),
    params=(
        Param("action", "Action", "choice", "reverse", ("reverse", "direct"),
              doc="Reverse acting raises the output as the measurement falls "
                  "-- heating. Direct acting is cooling."),
        Param("throttling", "Throttling range", "number", 10.0,
              doc="The change in measurement that drives the output through "
                  "its full range."),
        Param("out_low", "Output low", "number", 0.0),
        Param("out_high", "Output high", "number", 100.0),
        Param("integral", "Integral", "choice", "on", ("on", "off"),
              doc="Integral removes offset. Off gives proportional-only "
                  "control, which will settle away from setpoint."),
        Param("sample", "Sample seconds", "number", 10.0),
        Param("bias", "Bias", "number", "",
              doc="Output when the measurement equals setpoint. Blank means "
                  "the midpoint of the output range."),
    ),
    time_based=True,
    help="Gains are computed from the throttling range using the manual's own "
         "formula: pg = (output range / throttling range) * 1000, and the "
         "recommended integral starting point of 2 percent of pg.\n\n"
         "This block is always emitted unconditionally at the top level. A "
         "LOOP inside an IF or a subroutine loses its sample timing, which is "
         "why the compiler will not place it there.",
    emits=("LOOP",),
    manual="125-1896 Rev.5, LOOP",
)
def _emit_pid(ctx, block):
    from ..generator import integral_gain, proportional_gain

    low = ctx.num(block, "out_low", 0.0)
    high = ctx.num(block, "out_high", 100.0)
    if high <= low:
        raise BlockError(
            "the output range is %g to %g; high must exceed low" % (low, high),
            block.id,
        )
    throttling = ctx.num(block, "throttling", 10.0)
    if throttling <= 0:
        raise BlockError("the throttling range must be greater than zero",
                         block.id)
    pv = ctx.materialize(ctx.value_in(block, "pv"), block, "PV")
    sp = ctx.value_in(block, "sp")
    out = ctx.new_local(block, "CV")
    pg = proportional_gain(high - low, throttling)
    ig = integral_gain(pg, ctx.text(block, "integral", "on") == "on")
    bias_text = ctx.text(block, "bias", "")
    bias = float(bias_text) if bias_text not in ("", None) else low + (high - low) / 2.0
    kind = 0 if ctx.text(block, "action", "reverse") == "direct" else 128
    ctx.comment(
        "%s: %s acting, throttling range %s"
        % (block.label or "PID", ctx.text(block, "action", "reverse"),
           ctx.fmt(throttling))
    )
    ctx.emit(
        "LOOP(%d,%s,%s,%s,%d,%d,0,%d,%s,%s,%s,0)"
        % (kind, pv.text, out.text, sp.text, pg, ig,
           int(ctx.num(block, "sample", 10.0)), ctx.fmt(bias),
           ctx.fmt(low), ctx.fmt(high))
    )
    ctx.set_out(block, "out", out)


@block_type(
    "average", "Rolling average", "Control",
    "Average an analog value over a number of samples.",
    inputs=(Pin("in", "in", "analog"),),
    outputs=(Pin("out", "out", "analog"),),
    params=(
        Param("sample", "Sample seconds", "number", 60.0),
        Param("samples", "Samples", "number", 10.0),
    ),
    time_based=True,
    help="Smooths a noisy sensor before it drives a decision. Emitted "
         "unconditionally, because a rolling average that is only sometimes "
         "evaluated averages the wrong thing.",
    emits=("TIMAVG",),
    manual="125-1896 Rev.5, TIMAVG",
)
def _emit_average(ctx, block):
    source = ctx.materialize(ctx.value_in(block, "in"), block, "AVGIN")
    out = ctx.new_local(block, "AVG")
    ctx.emit(
        "TIMAVG(%s,%d,%d,%s)"
        % (out.text, int(ctx.num(block, "sample", 60.0)),
           int(ctx.num(block, "samples", 10.0)), source.text)
    )
    ctx.set_out(block, "out", out)


# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------


@block_type(
    "schedule", "Time schedule", "Timing",
    "Turn a flag on and off at fixed times of day.",
    outputs=(Pin("out", "occupied", "digital"),),
    params=(
        Param("on", "On at", "time", "6:00"),
        Param("off", "Off at", "time", "18:00"),
        Param("mode", "Day modes", "number", 1,
              doc="Sum of the TODMOD day modes this schedule applies to: "
                  "1, 2, 4, 8, and 16 for holidays."),
        Param("recommand", "Recommand after power loss", "choice", "1",
              ("1", "0")),
    ),
    time_based=True,
    help="Emits a TOD command driving a local flag, which the rest of the "
         "diagram can use like any other digital value. TOD must be reached "
         "on every pass, so it is placed unconditionally.\n\n"
         "If you use day mode 16, a HOLIDA command must define the holiday "
         "dates and must come first in the program.",
    emits=("TOD",),
    manual="125-1896 Rev.5, TOD",
)
def _emit_schedule(ctx, block):
    out = ctx.new_local(block, "OCC")
    ctx.emit(
        "TOD(%d,%s,%s,%s,%s)"
        % (int(ctx.num(block, "mode", 1)),
           ctx.text(block, "recommand", "1"),
           ctx.time(block, "on", "6:00"),
           ctx.time(block, "off", "18:00"), out.text)
    )
    ctx.set_out(block, "out", out)


@block_type(
    "delay", "Delay", "Timing",
    "Command a flag a fixed number of seconds after a trigger changes.",
    inputs=(Pin("in", "trigger", "digital"),),
    outputs=(Pin("out", "out", "digital"),),
    params=(
        Param("seconds", "Delay seconds", "number", 60.0),
        Param("mode", "Edge and result", "choice", "11",
              ("11", "10", "01", "00"),
              doc="11 = trigger ON, wait, output ON. 10 = trigger ON, wait, "
                  "output OFF. 01 = trigger OFF, wait, output ON. "
                  "00 = trigger OFF, wait, output OFF."),
    ),
    stateful=True,
    time_based=True,
    help="Proving a fan before enabling a coil, or holding a pump on after "
         "its load drops.\n\n"
         "After a power failure or an ENABLE, the trigger must change state "
         "before the delayed command runs -- whatever the points read at that "
         "moment. Do not rely on this block to establish state at startup.",
    emits=("WAIT",),
    manual="125-1896 Rev.5, WAIT",
)
def _emit_delay(ctx, block):
    seconds = int(ctx.num(block, "seconds", 60.0))
    if not 1 <= seconds <= 32767:
        raise BlockError(
            "the delay must be between 1 and 32767 seconds; got %d" % seconds,
            block.id,
        )
    trigger = ctx.materialize(ctx.value_in(block, "in"), block, "TRIG")
    out = ctx.new_local(block, "DLY")
    ctx.emit("WAIT(%d,%s,%s,%s)"
             % (seconds, trigger.text, out.text,
                ctx.text(block, "mode", "11")))
    ctx.set_out(block, "out", out)


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------


@block_type(
    "command", "Command point", "Outputs",
    "Command a real point, optionally only while an enable input is true.",
    inputs=(
        Pin("in", "value", "any", "The value to write, for SET", required=False),
        Pin("when", "when", "digital", "Command only while this is true",
            required=False),
    ),
    params=(
        Param("point", "Point name", "point", ""),
        Param("action", "Action", "choice", "set",
              ("set", "on", "off", "auto", "fast", "slow", "release"),
              doc="'set' writes the wired value. The others ignore the value "
                  "input and command the point's state."),
        Param("priority", "Priority", "choice", "",
              ("", "@NONE", "@PDL", "@EMER", "@SMOKE", "@OPER"),
              doc="Blank commands at the program's own priority. A command "
                  "at a priority you never release leaves the point stuck."),
        Param("release_when_false", "Release when 'when' is false", "choice",
              "no", ("no", "yes"),
              doc="Emits the matching RELEAS at the same priority. Turn this "
                  "on for any interlock, or the point never comes back."),
    ),
    is_output=True,
    help="This is where the diagram touches the building.\n\n"
         "If you command at a priority, something has to release it at that "
         "same priority or the point is owned forever -- a release below the "
         "point's current priority silently does nothing. Switching on "
         "'Release when false' writes that release for you.",
    emits=("ON", "OFF", "AUTO", "FAST", "SLOW", "SET", "RELEAS"),
    manual="125-1896 Rev.5 ch.3, point priority",
)
def _emit_command(ctx, block):
    point = ctx.text(block, "point")
    if not point:
        raise BlockError("this Command block has no point name", block.id)
    action = ctx.text(block, "action", "set").lower()
    priority = ctx.text(block, "priority", "")
    prefix = ("%s," % priority) if priority else ""
    target = ctx.render_point(point)

    if action == "set":
        value = ctx.value_in(block, "in")
        statement = "SET(%s%s,%s)" % (prefix, value.text, target)
    elif action == "release":
        statement = "RELEAS(%s%s)" % (prefix, target)
    else:
        statement = "%s(%s%s)" % (action.upper(), prefix, target)

    gate = ctx.value_in(block, "when", optional=True)
    if gate is None:
        ctx.emit(statement)
        return

    cond = ctx.as_boolean(gate)
    ctx.emit("IF(%s) THEN %s" % (cond.text, statement))
    if ctx.text(block, "release_when_false", "no") == "yes":
        if not priority:
            ctx.warn(
                "%s releases at no explicit priority; a bare RELEAS clears "
                "the point to NONE, which is usually what you want but is "
                "worth confirming against the sequence" % point
            )
        inverse = ctx.invert(cond)
        ctx.emit("IF(%s) THEN RELEAS(%s%s)" % (inverse.text, prefix, target))


@block_type(
    "assign", "Write value", "Outputs",
    "Store a calculated value into a point or virtual point.",
    inputs=(Pin("in", "value", "any"),),
    params=(Param("point", "Point name", "point", ""),),
    is_output=True,
    help="An assignment, not a command, so it does not take priority "
         "ownership of the point. Use this for setpoints and calculated "
         "virtual points; use Command point for anything that starts, stops "
         "or positions equipment.",
)
def _emit_assign(ctx, block):
    point = ctx.text(block, "point")
    if not point:
        raise BlockError("this Write block has no point name", block.id)
    value = ctx.value_in(block, "in")
    ctx.emit("%s = %s" % (ctx.render_point(point), value.text))


@block_type(
    "alarm", "Alarm", "Outputs",
    "Raise or clear an alarm on a point.",
    inputs=(Pin("when", "when", "digital"),),
    params=(
        Param("point", "Point name", "point", ""),
        Param("action", "Action", "choice", "alarm",
              ("alarm", "normal", "enable", "disable"),
              doc="alarm/normal force the alarm state; enable/disable turn "
                  "alarm reporting on and off for the point."),
    ),
    is_output=True,
    help="Disabling alarms during a known shutdown is normal practice. "
         "Re-enabling them is the part that gets forgotten -- wire the "
         "inverse condition to a second Alarm block.",
    emits=("ALARM", "NORMAL", "ENALM", "DISALM"),
)
def _emit_alarm(ctx, block):
    point = ctx.text(block, "point")
    if not point:
        raise BlockError("this Alarm block has no point name", block.id)
    command = {
        "alarm": "ALARM", "normal": "NORMAL",
        "enable": "ENALM", "disable": "DISALM",
    }[ctx.text(block, "action", "alarm")]
    cond = ctx.as_boolean(ctx.value_in(block, "when"))
    ctx.emit("IF(%s) THEN %s(%s)"
             % (cond.text, command, ctx.render_point(point)))


# --------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------


@block_type(
    "note", "Note", "Notes",
    "A comment placed in the generated program.",
    params=(Param("text", "Text", "text", ""),),
    help="Emitted as PPCL comment lines where the block sits in the "
         "compilation order. Comments are the only documentation that "
         "travels with the program to the panel.",
)
def _emit_note(ctx, block):
    for line in ctx.text(block, "text", "").split("\n"):
        ctx.comment(line)


# --------------------------------------------------------------------------
# Introspection for the UI
# --------------------------------------------------------------------------


def catalog_payload() -> dict:
    """The whole catalog as JSON, for the palette and the inspector."""
    out = {}
    for name, bt in CATALOG.items():
        out[name] = {
            "name": bt.name,
            "label": bt.label,
            "category": bt.category,
            "summary": bt.summary,
            "help": bt.help,
            "stateful": bt.stateful,
            "time_based": bt.time_based,
            "is_output": bt.is_output,
            "emits": list(bt.emits),
            "manual": bt.manual,
            "inputs": [
                {"name": p.name, "label": p.label, "kind": p.kind,
                 "doc": p.doc, "required": p.required}
                for p in bt.inputs
            ],
            "outputs": [
                {"name": p.name, "label": p.label, "kind": p.kind,
                 "doc": p.doc}
                for p in bt.outputs
            ],
            "params": [
                {"name": p.name, "label": p.label, "kind": p.kind,
                 "default": p.default, "choices": list(p.choices),
                 "doc": p.doc}
                for p in bt.params
            ],
        }
    return {"categories": CATEGORIES, "blocks": out}
