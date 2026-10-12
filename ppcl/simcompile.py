"""Compile a parsed program into the closures the simulator runs.

The simulator used to walk the syntax tree on every line of every pass:
dispatch on the node's type, then on the operator, then on the command name,
then look each point up by name. None of those decisions changes while a
program runs, so they are made here, once, and what is left at run time is a
chain of direct calls.

This is the only implementation of what a PPCL statement or expression does.
There is deliberately no tree-walking interpreter beside it: two
implementations of the same semantics drift, and this project has found that
kind of drift more than once. ``Simulator._eval`` compiles an expression it
was not built with -- a debugger's condition, say -- on the spot.

Exactness is the contract. Each closure evaluates in the same order the
tree-walker did, warns with the same text, and evaluates both operands of
``.AND.`` and ``.OR.`` as it did -- PPCL does not short-circuit, and an
operand can carry a warning. The simulator's output was held byte-identical
to the tree-walker's across the change, on a golden record of bench and trace
output.

Two rules for anything added here:

* Look ``sim.command``, ``sim.release`` and ``sim.panel`` up when the closure
  runs, never capture them while compiling. The debugger replaces
  ``command`` on the instance to catch writes, after the program is compiled.
* Only a program's own nodes go in the cache. They live as long as the
  program, so their ids cannot be reused while cached. A temporary node --
  the debugger parses a fresh condition on every check -- is compiled without
  caching, because its id can be handed to the next one.
"""

from __future__ import annotations

from . import spec
from .ast_nodes import (
    Assignment,
    BinOp,
    CommandCall,
    FuncCall,
    Gosub,
    Goto,
    If,
    MacroRef,
    Num,
    ParameterDecl,
    PriorityRef,
    Ref,
    Return,
    Sampled,
    TimeLit,
    UnaryOp,
)
from .simulator import (
    EXP_WARNING,
    OFF,
    ON,
    UNKNOWABLE_FUNCTION_WARNING,
    UNMODELLED,
    UNMODELLED_OUTPUTS,
    UNSIMULATED_FUNCTION_WARNING,
    _MATH,
    _STATE_VALUE,
    _UNKNOWABLE_FUNCTIONS,
)

_ARITHMETIC_ERRORS = (ValueError, OverflowError, ZeroDivisionError)


def _zero():
    return 0.0


def _nothing(call_stack):
    return None


def _return(call_stack):
    return "return"


# --------------------------------------------------------------------------
# Expressions: each compiles to a function of no arguments returning a float
# --------------------------------------------------------------------------


def compile_expr(sim, node, cache):
    """A function that returns the value of ``node``, and record it in cache."""
    if node is None:
        return _zero
    kind = node.__class__
    if kind is Num:
        value = node.value
        fn = lambda: value  # noqa: E731
    elif kind is TimeLit:
        value = node.as_decimal_hours
        fn = lambda: value  # noqa: E731
    elif kind is Ref:
        fn = sim._reader(node.name)
    elif kind is PriorityRef:
        value = float(spec.PRIORITY_RANK.get(node.name, 0))
        fn = lambda: value  # noqa: E731
    elif kind is MacroRef:
        fn = _zero
    elif kind is UnaryOp:
        operand = compile_expr(sim, node.operand, cache)
        fn = (lambda: -operand()) if node.op == "-" else operand
    elif kind is FuncCall:
        fn = _compile_func(sim, node, cache)
    elif kind is BinOp:
        fn = _compile_binop(sim, node, cache)
    else:
        fn = _zero
    cache[id(node)] = fn
    return fn


def _compiled(sim, node, cache):
    """The cached function for a program node, compiling it if need be."""
    fn = cache.get(id(node))
    return fn if fn is not None else compile_expr(sim, node, cache)


def _compile_func(sim, node, cache):
    name = node.name
    if name == "TOTAL":
        # Reads the point's runtime; the argument is never evaluated.
        if isinstance(node.arg, Ref):
            point = node.arg.name
            return lambda: sim.panel.get(point).runtime
        return _zero

    if name in _UNKNOWABLE_FUNCTIONS:
        message = UNKNOWABLE_FUNCTION_WARNING % (name, _UNKNOWABLE_FUNCTIONS[name])

        def unknowable():
            sim._warn(message)
            return 0.0
        return unknowable

    math_fn = _MATH.get(name)
    if math_fn is None:
        message = UNSIMULATED_FUNCTION_WARNING % name

        def unsimulated():
            sim._warn(message)
            return 0.0
        return unsimulated

    arg = compile_expr(sim, node.arg, cache)
    if name == "EXP":
        def exp():
            sim._warn(EXP_WARNING)
            try:
                return math_fn(arg())
            except _ARITHMETIC_ERRORS:
                return 0.0
        return exp

    def function():
        try:
            return math_fn(arg())
        except _ARITHMETIC_ERRORS:
            return 0.0
    return function


def _compile_binop(sim, node, cache):
    op = node.op

    # A point compared against an @priority tests the point's priority, not
    # its value. Neither side is evaluated as a number.
    if op in (".EQ.", ".NE."):
        for side, other in ((node.left, node.right), (node.right, node.left)):
            if isinstance(other, PriorityRef) and isinstance(side, Ref):
                point, priority = side.name, other.name
                if op == ".EQ.":
                    return lambda: (
                        ON if sim.panel.get(point).priority == priority else OFF)
                return lambda: (
                    OFF if sim.panel.get(point).priority == priority else ON)

    left = compile_expr(sim, node.left, cache)
    right = compile_expr(sim, node.right, cache)

    if op == ".ROOT.":
        def root():
            base, power = left(), right()
            if power == 0:
                return 0.0
            try:
                return base ** (1.0 / power)
            except (ValueError, ZeroDivisionError, OverflowError):
                return 0.0
        return root

    if op == "+":
        return lambda: left() + right()
    if op == "-":
        return lambda: left() - right()
    if op == "*":
        return lambda: left() * right()
    if op == "/":
        def divide():
            a = left()
            b = right()
            return a / b if b else 0.0
        return divide
    if op == ".EQ.":
        return lambda: ON if left() == right() else OFF
    if op == ".NE.":
        return lambda: ON if left() != right() else OFF
    if op == ".GT.":
        return lambda: ON if left() > right() else OFF
    if op == ".GE.":
        return lambda: ON if left() >= right() else OFF
    if op == ".LT.":
        return lambda: ON if left() < right() else OFF
    if op == ".LE.":
        return lambda: ON if left() <= right() else OFF

    # The logical operators evaluate both sides first, as the tree-walker
    # did; short-circuiting would skip a warning the right side carries.
    if op == ".AND.":
        def and_():
            a = left()
            b = right()
            return ON if (a and b) else OFF
        return and_
    if op == ".NAND.":
        def nand():
            a = left()
            b = right()
            return OFF if (a and b) else ON
        return nand
    if op == ".OR.":
        def or_():
            a = left()
            b = right()
            return ON if (a or b) else OFF
        return or_
    if op == ".XOR.":
        def xor():
            a = left()
            b = right()
            return ON if bool(a) != bool(b) else OFF
        return xor

    def unknown_operator():
        left()
        right()
        return 0.0
    return unknown_operator


# --------------------------------------------------------------------------
# Statements: each compiles to a function of the call stack, returning None,
# a target line, or "return"
# --------------------------------------------------------------------------


def compile_stmt(sim, stmt, line, cache):
    if stmt is None:
        return _nothing
    kind = stmt.__class__

    if kind is CommandCall:
        return compile_command(sim, stmt, line, cache)

    if kind is If:
        cond = compile_expr(sim, stmt.cond, cache)
        then = compile_stmt(sim, stmt.then_stmt, line, cache)
        other = compile_stmt(sim, stmt.else_stmt, line, cache)
        return lambda call_stack: (
            then(call_stack) if cond() else other(call_stack))

    if kind is Assignment:
        value = compile_expr(sim, stmt.expr, cache)
        if isinstance(stmt.target, Ref):
            point = stmt.target.name

            def assign(call_stack):
                result = value()
                sim.command(point, result, "@NONE", line, "assign")
                return None
            return assign

        def evaluate(call_stack):
            value()
            return None
        return evaluate

    if kind is Goto:
        target = stmt.target
        text = "GOTO %d" % target

        def goto(call_stack):
            if sim._traces("branch"):
                sim._emit(line, "branch", text)
            return target
        return goto

    if kind is Gosub:
        args = [compile_expr(sim, a, cache) for a in stmt.args[:15]]
        names = ["$ARG%d" % i for i in range(1, len(args) + 1)]
        resume = sim._pc_of[line] + 1
        target = stmt.target
        text = "GOSUB %d" % target

        def gosub(call_stack):
            for name, arg in zip(names, args):
                sim.panel.get(name).value = arg()
            call_stack.append(resume)
            if sim._traces("branch"):
                sim._emit(line, "branch", text)
            return target
        return gosub

    if kind is Return:
        return _return

    if kind is Sampled:
        seconds = compile_expr(sim, stmt.seconds, cache)
        inner = compile_stmt(sim, stmt.statement, line, cache)

        def sampled(call_stack):
            interval = seconds()
            last = sim._sample_last.get(line)
            if last is None or sim.clock.elapsed - last >= interval:
                sim._sample_last[line] = sim.clock.elapsed
                return inner(call_stack)
            return None
        return sampled

    if kind is ParameterDecl:
        key = stmt.name.upper()
        value = compile_expr(sim, stmt.value, cache)

        def parameter(call_stack):
            sim.parameters[key] = value()
            return None
        return parameter

    # Comment, Unparsed, anything else: executes as nothing.
    return _nothing


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def compile_command(sim, call, line, cache):
    # Every argument is compiled up front, so a handler that evaluates its
    # own arguments through sim._eval finds them compiled too.
    for arg in call.args:
        compile_expr(sim, arg, cache)
    binder = COMMANDS.get(call.name)
    if binder is not None:
        return binder(sim, call, line, cache)
    if call.name in UNMODELLED:
        return _unmodelled(sim, call, line)
    return _nothing


def _priority(call):
    return call.priority.name if call.priority else "@NONE"


def _points(args):
    return [a.name for a in args if isinstance(a, Ref)]


def _bind_local(sim, call, line, cache):
    bare = [a.bare_name for a in call.args if isinstance(a, Ref)]

    def local(call_stack):
        for name in bare:
            sim.locals_declared.add(name.upper())
            sim.panel.get("$" + name)
        return None
    return local


def _bind_nothing(sim, call, line, cache):
    return _nothing


def _bind_state(sim, call, line, cache):
    name = call.name
    value = _STATE_VALUE[name]
    priority = _priority(call)
    points = _points(call.args)

    def state(call_stack):
        for point in points:
            sim.command(point, value, priority, line, name)
        return None
    return state


def _bind_emergency(sim, call, line, cache):
    name = call.name
    value = OFF if name == "EMOFF" else ON
    points = _points(call.args)

    def emergency(call_stack):
        for point in points:
            sim.command(point, value, "@EMER", line, name)
        return None
    return emergency


def _bind_set(sim, call, line, cache):
    args = call.args
    if not args:
        return _nothing
    name = call.name
    value = _compiled(sim, args[0], cache)
    level = "@EMER" if name == "EMSET" else _priority(call)
    points = _points(args[1:])

    def set_(call_stack):
        result = value()
        for point in points:
            sim.command(point, result, level, line, name)
        return None
    return set_


def _bind_state_command(sim, call, line, cache):
    args = call.args
    if not args:
        return _nothing
    value = _compiled(sim, args[0], cache)
    priority = _priority(call)
    points = _points(args[1:])

    def state_command(call_stack):
        result = value()
        for point in points:
            sim.command(point, result, priority, line, "STATE")
        return None
    return state_command


def _bind_release(sim, call, line, cache):
    priority = call.priority.name if call.priority else None
    points = _points(call.args)

    def release(call_stack):
        for point in points:
            sim.release(point, priority, line)
        return None
    return release


def _bind_extreme(sim, call, line, cache):
    args = call.args
    if len(args) < 2:
        return _nothing
    name = call.name
    values = [_compiled(sim, a, cache) for a in args[1:]]
    target = args[0].name if isinstance(args[0], Ref) else None
    pick = max if name == "MAX" else min

    def extreme(call_stack):
        result = pick([v() for v in values])
        if target is not None:
            sim.command(target, result, "@NONE", line, name)
        return None
    return extreme


def _constant(node):
    """(True, value) for a node whose value can never change, else (False,)."""
    kind = node.__class__
    if kind is Num:
        return True, node.value
    if kind is TimeLit:
        return True, node.as_decimal_hours
    if kind is UnaryOp:
        inner = _constant(node.operand)
        if inner[0]:
            return True, (-inner[1] if node.op == "-" else inner[1])
    return (False,)


def _bind_table(sim, call, line, cache):
    args = call.args
    if len(args) < 4 or not isinstance(args[1], Ref):
        return _nothing
    x_of = _compiled(sim, args[0], cache)
    target = args[1].name
    pair_nodes = [(args[i], args[i + 1]) for i in range(2, len(args) - 1, 2)]

    # A table's breakpoints are nearly always literals. Those are worked out
    # once; anything else is evaluated per run, in the original order.
    folded = [(_constant(a), _constant(b)) for a, b in pair_nodes]
    if all(a[0] and b[0] for a, b in folded):
        constant = [(a[1], b[1]) for a, b in folded]

        def pairs_of():
            return constant
    else:
        pair_fns = [(_compiled(sim, a, cache), _compiled(sim, b, cache))
                    for a, b in pair_nodes]

        def pairs_of():
            return [(a(), b()) for a, b in pair_fns]

    def table(call_stack):
        x = x_of()
        pairs = pairs_of()
        if not pairs:
            return None
        if x <= pairs[0][0]:
            y = pairs[0][1]
        elif x >= pairs[-1][0]:
            y = pairs[-1][1]
        else:
            y = pairs[-1][1]
            for (x0, y0), (x1, y1) in zip(pairs, pairs[1:]):
                if x0 <= x <= x1:
                    span = x1 - x0
                    y = y0 if span == 0 else y0 + (y1 - y0) * (x - x0) / span
                    break
        sim.command(target, y, "@NONE", line, "TABLE")
        return None
    return table


def _handler(method):
    """Bind a command whose implementation stays a Simulator method.

    The method is looked up on every call, not captured, as it always was.
    """
    def bind(sim, call, line, cache):
        args = call.args
        return lambda call_stack: getattr(sim, method)(args, line)
    return bind


def _bind_tod(sim, call, line, cache):
    name, args = call.name, call.args
    return lambda call_stack: sim._exec_tod(name, args, line)


def _bind_enable(sim, call, line, cache):
    lines = [int(a.value) for a in call.args if isinstance(a, Num)]

    def enable(call_stack):
        for number in lines:
            sim.disabled_lines.discard(number)
        return None
    return enable


def _bind_disable(sim, call, line, cache):
    lines = [int(a.value) for a in call.args if isinstance(a, Num)]

    def disable(call_stack):
        for number in lines:
            sim.disabled_lines.add(number)
        return None
    return disable


def _bind_todmod(sim, call, line, cache):
    modes_of = [_compiled(sim, a, cache) for a in call.args[:7]]

    def todmod(call_stack):
        modes = [int(m()) for m in modes_of]
        if len(modes) == 7:
            # day_of_week 1 = Sunday; TODMOD lists Monday first.
            order = [modes[6]] + modes[:6]
            sim._tod_mode = order[(sim.clock.day_of_week - 1) % 7]
        return None
    return todmod


def _bind_holida(sim, call, line, cache):
    args = call.args
    pairs = [(_compiled(sim, args[i], cache), _compiled(sim, args[i + 1], cache))
             for i in range(0, len(args) - 1, 2)]

    def holida(call_stack):
        for month, day in pairs:
            sim._holidays.add((int(month()), int(day())))
        if (sim.clock.month, sim.clock.day_of_month) in sim._holidays:
            sim._tod_mode = 16
        return None
    return holida


def _unmodelled(sim, call, line):
    name, args = call.name, call.args
    written = [args[i].name for i in UNMODELLED_OUTPUTS.get(name, ())
               if i < len(args) and isinstance(args[i], Ref)]
    extra = ""
    if written:
        extra = ("; %s %s not written, so %s value%s here %s whatever "
                 "the panel was loaded with"
                 % (", ".join(written),
                    "is" if len(written) == 1 else "are",
                    "its" if len(written) == 1 else "their",
                    "" if len(written) == 1 else "s",
                    "is" if len(written) == 1 else "are"))
    message = ("%s at line %d is recognised but not simulated; it is treated "
               "as a no-op%s" % (name, line, extra))

    def unmodelled(call_stack):
        sim._warn(message)
        return None
    return unmodelled


#: Every command the simulator models, and how each is compiled. A command
#: in spec.ALL must be here, in simulator.UNMODELLED, or be one of the branch
#: statements that have their own syntax node; a test holds that.
COMMANDS = {
    "LOCAL": _bind_local,
    "ONPWRT": _bind_nothing,  # only relevant on a simulated power cycle
    "ON": _bind_state, "OFF": _bind_state, "AUTO": _bind_state,
    "FAST": _bind_state, "SLOW": _bind_state,
    "EMON": _bind_emergency, "EMOFF": _bind_emergency,
    "EMAUTO": _bind_emergency, "EMFAST": _bind_emergency,
    "EMSLOW": _bind_emergency,
    "SET": _bind_set, "EMSET": _bind_set,
    "STATE": _bind_state_command,
    "RELEAS": _bind_release,
    "MAX": _bind_extreme, "MIN": _bind_extreme,
    "TABLE": _bind_table,
    "DBSWIT": _handler("_exec_dbswit"),
    "TIMAVG": _handler("_exec_timavg"),
    "WAIT": _handler("_exec_wait"),
    "LOOP": _handler("_exec_loop"),
    "ACT": _bind_enable, "ENABLE": _bind_enable,
    "DEACT": _bind_disable, "DISABL": _bind_disable,
    "TODMOD": _bind_todmod,
    "HOLIDA": _bind_holida,
    "TOD": _bind_tod, "TODSET": _bind_tod,
}
