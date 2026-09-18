"""A PPCL interpreter for offline debugging.

The point of this module is to let you run a sequence against made-up sensor
values on a workstation instead of on a live panel, and watch what it does --
including the priority interactions that cause most real PPCL surprises.

Fidelity, stated honestly:

* Exact, because the manual specifies the behaviour completely:
  assignment and arithmetic, IF/THEN/ELSE, GOTO/GOSUB/RETURN, ON/OFF/AUTO/
  FAST/SLOW/SET/STATE/RELEAS and their priority arbitration, MAX, MIN, TABLE,
  DBSWIT, TIMAVG, SAMPLE, WAIT, TOD, TODSET, TODMOD, HOLIDA, ACT/DEACT/
  ENABLE/DISABL, ONPWRT, LOCAL.

* Approximated, and labelled as such at runtime: LOOP. Siemens does not
  publish the internal PID form, so this uses a textbook positional PI(D)
  controller scaled by the documented ``pg = range/throttling * 1000``
  relationship, with the documented anti-windup clamp. Treat loop *output
  values* as indicative, not as a tuning authority. Everything about when the
  loop runs and what it reads and writes is exact.

* Not modelled: PDL shedding, SSTO optimisation, OIP, DC/DCR duty cycling,
  telephone commands. These are recognised and traced as no-ops so a program
  containing them still runs.

Times are handled throughout in decimal hours, so ``TIME``, ``CRTIME`` and a
``17:30`` literal all compare on the same scale.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import spec
from .analyzer import substatements
from .ast_nodes import (
    Assignment,
    BinOp,
    Comment,
    CommandCall,
    FuncCall,
    Gosub,
    Goto,
    If,
    MacroRef,
    Num,
    ParameterDecl,
    PriorityRef,
    Program,
    Ref,
    Return,
    Sampled,
    TimeLit,
    UnaryOp,
    Unparsed,
)

ON = 1.0
OFF = 0.0

#: Commands the simulator recognises but does not model.
UNMODELLED = frozenset(
    {"PDL", "PDLDAT", "PDLDPG", "PDLMTR", "PDLSET", "SSTO", "SSTOCO",
     "OIP", "DC", "DCR", "DPHONE", "EPHONE", "DISCOV", "ENCOV",
     "DISALM", "ENALM", "ALARM", "NORMAL", "HLIMIT", "LLIMIT", "INITTO",
     "DEFINE", "DAY", "NIGHT"}
)


class SimulationError(Exception):
    """Raised when the program cannot continue."""


@dataclass
class Point:
    """One point in the simulated panel database."""

    name: str
    value: float = 0.0
    priority: str = "@NONE"
    ptype: str = None
    #: Total seconds the point has been ON, for totalisation.
    runtime: float = 0.0

    def clone(self):
        return Point(self.name, self.value, self.priority, self.ptype, self.runtime)


@dataclass
class Event:
    """One traced action."""

    time: float  # decimal hours
    line: int
    kind: str  # "write" | "blocked" | "branch" | "note"
    text: str


@dataclass
class Panel:
    """The simulated point database."""

    points: dict = field(default_factory=dict)

    def get(self, name: str) -> Point:
        key = name.upper()
        if key not in self.points:
            self.points[key] = Point(name)
        return self.points[key]

    def value(self, name: str) -> float:
        return self.get(name).value

    def snapshot(self) -> dict:
        return {k: v.clone() for k, v in self.points.items()}

    def load(self, values: dict) -> None:
        """Seed point values, e.g. from a JSON scenario file."""
        for name, val in values.items():
            pt = self.get(name)
            if isinstance(val, dict):
                pt.value = float(val.get("value", 0.0))
                pt.priority = val.get("priority", "@NONE")
                pt.ptype = val.get("type")
            elif isinstance(val, bool):
                pt.value = ON if val else OFF
            elif isinstance(val, str):
                text = val.strip()
                named = {"ON": ON, "OFF": OFF, "AUTO": ON, "FAST": ON,
                         "SLOW": 0.5, "TRUE": ON, "FALSE": OFF}
                if text.upper() in named:
                    pt.value = named[text.upper()]
                elif ":" in text:  # a military-time literal like 17:30
                    hh, _, mm = text.partition(":")
                    pt.value = float(hh) + float(mm) / 60.0
                else:
                    try:
                        pt.value = float(text)
                    except ValueError:
                        raise ValueError(
                            "cannot interpret %r as a value for point %s; use a "
                            "number, ON/OFF, or HH:MM" % (val, name)
                        )
            else:
                pt.value = float(val)


@dataclass
class Clock:
    """Simulated wall clock, in decimal hours since midnight."""

    hours: float = 8.0
    day_of_week: int = 2  # 1 = Sunday, per APOGEE convention
    day_of_month: int = 15
    month: int = 6
    elapsed: float = 0.0  # total simulated seconds

    def advance(self, seconds: float) -> None:
        self.elapsed += seconds
        self.hours += seconds / 3600.0
        while self.hours >= 24.0:
            self.hours -= 24.0
            self.day_of_week = self.day_of_week % 7 + 1
            self.day_of_month += 1


class Simulator:
    """Executes a parsed PPCL program against a simulated panel."""

    #: Guard against a program that branches in a tight cycle forever.
    MAX_LINES_PER_PASS = 20000

    def __init__(self, program: Program, panel: Panel = None,
                 clock: Clock = None, trace: bool = True,
                 firmware: spec.Firmware = spec.Firmware.APOGEE):
        self.program = program
        self.panel = panel or Panel()
        self.clock = clock or Clock()
        self.firmware = firmware
        self.trace_enabled = trace
        self.events = []
        self.warnings = []

        self.index = program.by_number()
        self.numbers = sorted(self.index)
        self.disabled_lines = set()
        self.parameters = {}
        self.locals_declared = set()

        # Persistent execution state. A panel keeps running where it left off
        # rather than restarting the program on every tick, and time-based
        # commands depend on that continuity.
        self._pc = 0
        self._call_stack = []
        self._executed_lines = set()

        # Per-command persistent state, keyed by line number.
        self._sample_last = {}
        self._timavg = {}
        self._wait = {}
        self._loop = {}
        self._tod_mode = None
        self._holidays = set()
        self._pending_power_return = None

        for directive in program.directives:
            self.parameters[directive.name.upper()] = self._eval(directive.value)

    # -- tracing -----------------------------------------------------------

    def _emit(self, line, kind, text):
        if self.trace_enabled:
            self.events.append(Event(self.clock.hours, line, kind, text))

    def _warn(self, text):
        if text not in self.warnings:
            self.warnings.append(text)

    # -- point access ------------------------------------------------------

    def _resolve_name(self, ref: Ref) -> str:
        return ref.name

    def _read(self, name: str) -> float:
        upper = name.upper()

        if upper in ("TIME", "CRTIME"):
            return self.clock.hours
        if upper == "DAY":
            return float(self.clock.day_of_week)
        if upper == "DAYOFM":
            return float(self.clock.day_of_month)
        if upper == "MONTH":
            return float(self.clock.month)
        if upper == "SECNDS":
            return float(int(self.clock.elapsed) % 60)
        if upper.startswith("SECND") and upper[5:].isdigit():
            return float(int(self.clock.elapsed) % 60)
        if upper == "LINK":
            return ON
        if upper == "$BATT":
            return ON
        if upper in ("ALMCNT", "ALMCT2", "$PDL"):
            return 0.0
        if upper.startswith("NODE") and upper[4:].isdigit():
            return ON
        if upper in self.parameters:
            return self.parameters[upper]
        return self.panel.value(name)

    def command(self, name: str, value: float, priority: str, line: int,
                what: str) -> bool:
        """Command a point, honouring priority arbitration.

        Returns True if the command took effect. The manual's rule is that a
        point is only commanded when the operation's priority is at least as
        high as the point's current priority.
        """
        pt = self.panel.get(name)
        cur_rank = spec.PRIORITY_RANK.get(pt.priority, 0)
        new_rank = spec.PRIORITY_RANK.get(priority, 0)
        if new_rank < cur_rank:
            self._emit(
                line, "blocked",
                "%s %s blocked: point is at %s, command is at %s"
                % (what, name, pt.priority, priority),
            )
            return False
        old = pt.value
        pt.value = value
        pt.priority = priority
        if old != value:
            self._emit(
                line, "write",
                "%s = %s (%s%s)"
                % (name, _fmt(value), what,
                   "" if priority == "@NONE" else " at " + priority),
            )
        return True

    def release(self, name: str, priority: str, line: int) -> bool:
        pt = self.panel.get(name)
        cur_rank = spec.PRIORITY_RANK.get(pt.priority, 0)
        rel_rank = spec.PRIORITY_RANK.get(priority or pt.priority, 0)
        if priority is not None and rel_rank < cur_rank:
            self._emit(
                line, "blocked",
                "RELEAS %s at %s does not clear %s priority"
                % (name, priority, pt.priority),
            )
            return False
        if pt.priority != "@NONE":
            self._emit(line, "write",
                       "%s released from %s to @NONE" % (name, pt.priority))
        pt.priority = "@NONE"
        return True

    # -- expression evaluation --------------------------------------------

    def _eval(self, node) -> float:
        if node is None:
            return 0.0
        if isinstance(node, Num):
            return node.value
        if isinstance(node, TimeLit):
            return node.as_decimal_hours
        if isinstance(node, Ref):
            upper = node.name.upper()
            if upper in spec.STATUS_INDICATORS:
                return _status_value(upper)
            return self._read(node.name)
        if isinstance(node, PriorityRef):
            return float(spec.PRIORITY_RANK.get(node.name, 0))
        if isinstance(node, MacroRef):
            return 0.0
        if isinstance(node, UnaryOp):
            v = self._eval(node.operand)
            return -v if node.op == "-" else v
        if isinstance(node, FuncCall):
            return self._eval_func(node)
        if isinstance(node, BinOp):
            return self._eval_binop(node)
        return 0.0

    def _eval_func(self, node: FuncCall) -> float:
        import math

        name = node.name
        if name == "ALMPRI":
            return 0.0
        if name == "TOTAL":
            if isinstance(node.arg, Ref):
                return self.panel.get(node.arg.name).runtime
            return 0.0

        v = self._eval(node.arg)
        try:
            if name == "SQRT":
                return math.sqrt(v) if v >= 0 else 0.0
            if name == "LOG":
                return math.log(v) if v > 0 else 0.0
            if name == "EXP":
                return math.exp(v)
            if name == "SIN":
                return math.sin(math.radians(v))
            if name == "COS":
                return math.cos(math.radians(v))
            if name == "TAN":
                return math.tan(math.radians(v))
            if name == "ATN":
                return math.degrees(math.atan(v))
            if name == "COM":
                return OFF if v else ON
        except (ValueError, OverflowError):
            return 0.0
        return 0.0

    def _eval_binop(self, node: BinOp) -> float:
        op = node.op

        # Comparing a point against an @priority tests the point's priority
        # rather than its value. This is the distinction that makes priority
        # bugs so hard to see by reading.
        if op in (".EQ.", ".NE."):
            for side, other in ((node.left, node.right), (node.right, node.left)):
                if isinstance(other, PriorityRef) and isinstance(side, Ref):
                    actual = self.panel.get(side.name).priority
                    same = actual == other.name
                    return _b(same if op == ".EQ." else not same)

        if op == ".ROOT.":
            base, power = self._eval(node.left), self._eval(node.right)
            if power == 0:
                return 0.0
            try:
                return base ** (1.0 / power)
            except (ValueError, ZeroDivisionError, OverflowError):
                return 0.0

        left = self._eval(node.left)
        right = self._eval(node.right)

        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            return left / right if right else 0.0
        if op == ".EQ.":
            return _b(left == right)
        if op == ".NE.":
            return _b(left != right)
        if op == ".GT.":
            return _b(left > right)
        if op == ".GE.":
            return _b(left >= right)
        if op == ".LT.":
            return _b(left < right)
        if op == ".LE.":
            return _b(left <= right)
        if op == ".AND.":
            return _b(bool(left) and bool(right))
        if op == ".NAND.":
            return _b(not (bool(left) and bool(right)))
        if op == ".OR.":
            return _b(bool(left) or bool(right))
        if op == ".XOR.":
            return _b(bool(left) != bool(right))
        return 0.0

    # -- execution ---------------------------------------------------------

    def run(self, passes: int = 1, seconds_per_pass: float = 1.0) -> list:
        """Run ``passes`` complete program passes, advancing the clock."""
        for _ in range(passes):
            self.run_pass()
            self.clock.advance(seconds_per_pass)
            self._accumulate_runtime(seconds_per_pass)
        return self.events

    def run_for(self, seconds: float, seconds_per_pass: float = 1.0) -> list:
        """Run until ``seconds`` of simulated time have elapsed."""
        passes = max(1, int(seconds / max(seconds_per_pass, 1e-6)))
        return self.run(passes=passes, seconds_per_pass=seconds_per_pass)

    def _accumulate_runtime(self, seconds: float) -> None:
        for pt in self.panel.points.values():
            if pt.value >= 0.5:
                pt.runtime += seconds

    def execute_lines(self, budget: int) -> int:
        """Execute up to ``budget`` program lines from the persistent counter.

        This is the panel-faithful primitive. A field panel does not run
        "passes": it evaluates a fixed number of lines per second, shared
        round-robin across every enabled program, and a program that falls off
        its last line wraps to its first. Execution state persists between
        calls, so a program can be advanced in step with simulated time.

        Returns the number of lines actually executed.
        """
        if not self.numbers:
            return 0

        executed = 0
        while executed < budget:
            if self._pc >= len(self.numbers):
                self._pc = 0  # the documented wrap to the first line

            number = self.numbers[self._pc]
            line = self.index[number]

            if number in self.disabled_lines or line.is_comment:
                self._pc += 1
                continue

            executed += 1
            self._executed_lines.add(number)
            jump = self._exec(line.stmt, number, self._call_stack)

            if jump is None:
                self._pc += 1
                continue
            if jump == "return":
                if not self._call_stack:
                    self._warn(
                        "RETURN at line %d with no matching GOSUB; execution "
                        "continues at the next line" % number
                    )
                    self._pc += 1
                    continue
                self._pc = self._call_stack.pop()
                continue
            target = self._resolve(jump)
            if target is None:
                self._warn(
                    "branch from line %d to line %d ran off the end of the "
                    "program; wrapping to the first line" % (number, jump)
                )
                self._pc = 0
                continue
            self._pc = self.numbers.index(target)
        return executed

    @property
    def starved_lines(self) -> list:
        """Executable lines that have never run.

        On a panel this is what a runaway inner loop actually looks like: the
        program keeps running, but part of it is never reached again.
        """
        return [
            ln.number
            for ln in self.program.lines
            if ln.is_executable and ln.number not in self._executed_lines
        ]

    def run_pass(self) -> None:
        """Execute one nominal pass over the program.

        Kept for simple use and for stepping a program by hand. It executes a
        budget of one line per executable line in the program, which is a full
        traversal for straight-line code and one loop for the usual
        main-loop-with-GOTO structure.
        """
        if not self.numbers:
            return
        budget = min(self.MAX_LINES_PER_PASS, max(len(self.numbers), 1))
        self.execute_lines(budget)

    def _resolve(self, number: int):
        if number in self.index:
            return number
        for n in self.numbers:
            if n > number:
                return n
        return None

    def _exec(self, stmt, line: int, call_stack: list):
        """Execute one statement. Returns None, a target line, or "return"."""
        if stmt is None or isinstance(stmt, (Comment, Unparsed)):
            return None

        if isinstance(stmt, ParameterDecl):
            self.parameters[stmt.name.upper()] = self._eval(stmt.value)
            return None

        if isinstance(stmt, Assignment):
            value = self._eval(stmt.expr)
            if isinstance(stmt.target, Ref):
                self.command(stmt.target.name, value, "@NONE", line, "assign")
            return None

        if isinstance(stmt, If):
            if self._eval(stmt.cond):
                return self._exec(stmt.then_stmt, line, call_stack)
            return self._exec(stmt.else_stmt, line, call_stack)

        if isinstance(stmt, Goto):
            self._emit(line, "branch", "GOTO %d" % stmt.target)
            return stmt.target

        if isinstance(stmt, Gosub):
            for i, arg in enumerate(stmt.args[:15], start=1):
                self.panel.get("$ARG%d" % i).value = self._eval(arg)
            idx = self.numbers.index(line)
            call_stack.append(idx + 1)
            self._emit(line, "branch", "GOSUB %d" % stmt.target)
            return stmt.target

        if isinstance(stmt, Return):
            return "return"

        if isinstance(stmt, Sampled):
            interval = self._eval(stmt.seconds)
            last = self._sample_last.get(line)
            if last is None or self.clock.elapsed - last >= interval:
                self._sample_last[line] = self.clock.elapsed
                return self._exec(stmt.statement, line, call_stack)
            return None

        if isinstance(stmt, CommandCall):
            return self._exec_command(stmt, line)

        return None

    def _exec_command(self, call: CommandCall, line: int):
        name = call.name
        prio = call.priority.name if call.priority else "@NONE"
        args = call.args

        if name == "LOCAL":
            for arg in args:
                if isinstance(arg, Ref):
                    self.locals_declared.add(arg.bare_name.upper())
                    self.panel.get("$" + arg.bare_name)
            return None

        if name == "ONPWRT":
            return None  # only relevant on a simulated power cycle

        if name in ("ON", "OFF", "AUTO", "FAST", "SLOW"):
            value = {"ON": ON, "OFF": OFF, "AUTO": ON, "FAST": ON, "SLOW": 0.5}[name]
            for arg in args:
                if isinstance(arg, Ref):
                    self.command(arg.name, value, prio, line, name)
            return None

        if name in ("EMON", "EMOFF", "EMAUTO", "EMFAST", "EMSLOW"):
            value = OFF if name == "EMOFF" else ON
            for arg in args:
                if isinstance(arg, Ref):
                    self.command(arg.name, value, "@EMER", line, name)
            return None

        if name in ("SET", "EMSET"):
            if not args:
                return None
            value = self._eval(args[0])
            level = "@EMER" if name == "EMSET" else prio
            for arg in args[1:]:
                if isinstance(arg, Ref):
                    self.command(arg.name, value, level, line, name)
            return None

        if name == "STATE":
            if not args:
                return None
            value = self._eval(args[0])
            for arg in args[1:]:
                if isinstance(arg, Ref):
                    self.command(arg.name, value, prio, line, name)
            return None

        if name == "RELEAS":
            for arg in args:
                if isinstance(arg, Ref):
                    self.release(arg.name, call.priority.name if call.priority else None,
                                 line)
            return None

        if name in ("MAX", "MIN"):
            if len(args) < 2:
                return None
            values = [self._eval(a) for a in args[1:]]
            result = max(values) if name == "MAX" else min(values)
            if isinstance(args[0], Ref):
                self.command(args[0].name, result, "@NONE", line, name)
            return None

        if name == "TABLE":
            return self._exec_table(args, line)

        if name == "DBSWIT":
            return self._exec_dbswit(args, line)

        if name == "TIMAVG":
            return self._exec_timavg(args, line)

        if name == "WAIT":
            return self._exec_wait(args, line)

        if name == "LOOP":
            return self._exec_loop(args, line)

        if name in ("ACT", "ENABLE"):
            for arg in args:
                if isinstance(arg, Num):
                    self.disabled_lines.discard(int(arg.value))
            return None

        if name in ("DEACT", "DISABL"):
            for arg in args:
                if isinstance(arg, Num):
                    self.disabled_lines.add(int(arg.value))
            return None

        if name == "TODMOD":
            modes = [int(self._eval(a)) for a in args[:7]]
            if len(modes) == 7:
                # day_of_week 1 = Sunday; TODMOD lists Monday first.
                order = [modes[6]] + modes[:6]
                self._tod_mode = order[(self.clock.day_of_week - 1) % 7]
            return None

        if name == "HOLIDA":
            for i in range(0, len(args) - 1, 2):
                self._holidays.add(
                    (int(self._eval(args[i])), int(self._eval(args[i + 1])))
                )
            if (self.clock.month, self.clock.day_of_month) in self._holidays:
                self._tod_mode = 16
            return None

        if name in ("TOD", "TODSET"):
            return self._exec_tod(name, args, line)

        if name in UNMODELLED:
            self._warn(
                "%s at line %d is recognised but not simulated; it is treated "
                "as a no-op" % (name, line)
            )
            return None

        return None

    # -- individual command implementations --------------------------------

    def _exec_table(self, args, line):
        if len(args) < 4 or not isinstance(args[1], Ref):
            return None
        x = self._eval(args[0])
        pairs = []
        for i in range(2, len(args) - 1, 2):
            pairs.append((self._eval(args[i]), self._eval(args[i + 1])))
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
        self.command(args[1].name, y, "@NONE", line, "TABLE")
        return None

    def _exec_dbswit(self, args, line):
        if len(args) < 5:
            return None
        kind = int(self._eval(args[0]))
        value = self._eval(args[1])
        low = self._eval(args[2])
        high = self._eval(args[3])
        targets = [a for a in args[4:] if isinstance(a, Ref)]

        state = None
        if kind == 0:
            if value > high:
                state = ON
            elif value < low:
                state = OFF
        else:
            if value < low:
                state = ON
            elif value > high:
                state = OFF
        if state is None:
            return None  # inside the dead band: hold
        for t in targets:
            self.command(t.name, state, "@NONE", line, "DBSWIT")
        return None

    def _exec_timavg(self, args, line):
        if len(args) < 4 or not isinstance(args[0], Ref):
            return None
        interval = self._eval(args[1])
        count = max(1, min(10, int(self._eval(args[2]))))
        value = self._eval(args[3])

        state = self._timavg.setdefault(line, {"samples": [], "last": None})
        if state["last"] is None:
            state["samples"] = [value]
            state["last"] = self.clock.elapsed
        elif self.clock.elapsed - state["last"] >= interval:
            state["samples"].append(value)
            state["samples"] = state["samples"][-count:]
            state["last"] = self.clock.elapsed
        avg = sum(state["samples"]) / len(state["samples"])
        self.command(args[0].name, avg, "@NONE", line, "TIMAVG")
        return None

    def _exec_wait(self, args, line):
        if len(args) < 3:
            return None
        delay = self._eval(args[0])
        trigger = args[1]
        target = args[2]
        if not isinstance(trigger, Ref) or not isinstance(target, Ref):
            return None
        mode = "11"
        if len(args) >= 4:
            raw = getattr(args[3], "raw", None)
            mode = raw if raw in ("11", "10", "01", "00") else "11"

        want_edge = ON if mode[0] == "1" else OFF
        result = ON if mode[1] == "1" else OFF

        state = self._wait.setdefault(line, {"last": None, "armed_at": None})
        current = self._read(trigger.name)
        previous = state["last"]
        state["last"] = current

        if previous is not None and current != previous and current == want_edge:
            state["armed_at"] = self.clock.elapsed
            self._emit(line, "note",
                       "WAIT armed by %s; %s in %gs"
                       % (trigger.name, target.name, delay))

        if state["armed_at"] is not None:
            if self.clock.elapsed - state["armed_at"] >= delay:
                self.command(target.name, result, "@NONE", line, "WAIT")
                state["armed_at"] = None
        return None

    def _exec_loop(self, args, line):
        if len(args) < 12:
            return None
        self._warn(
            "LOOP at line %d is simulated with an approximate PID form; treat "
            "the output values as indicative, not as tuning guidance" % line
        )
        kind = int(self._eval(args[0]))
        pv_ref, cv_ref = args[1], args[2]
        if not isinstance(cv_ref, Ref):
            return None
        pv = self._eval(pv_ref)
        sp = self._eval(args[3])
        pg = self._eval(args[4])
        ig = self._eval(args[5])
        st = max(1.0, self._eval(args[7]))
        bias = self._eval(args[8])
        lo = self._eval(args[9])
        hi = self._eval(args[10])

        state = self._loop.setdefault(line, {"integral": 0.0, "last": None})
        if state["last"] is not None and self.clock.elapsed - state["last"] < st:
            return None
        state["last"] = self.clock.elapsed

        error = (pv - sp) if kind == 0 else (sp - pv)

        # The manual defines pg = (full range of the controlled device /
        # throttling range) * 1000. So pg/1000 IS the gain in output units per
        # unit of error -- the output span must NOT be applied a second time.
        # A pg of 10000 on a 0-100% output means 10% per degree, i.e. a 10
        # degree throttling range.
        gain = pg / 1000.0
        proportional = gain * error
        # Integral gain is expressed on the same scale, accumulated per minute
        # of sample time.
        state["integral"] += (ig / 1000.0) * error * (st / 60.0)

        out = bias + proportional + state["integral"]
        clamped = max(lo, min(hi, out))
        if clamped != out:
            # Documented anti-windup: back out the excess so the integral does
            # not keep accumulating against a saturated output.
            state["integral"] += clamped - out
        self.command(cv_ref.name, clamped, "@NONE", line, "LOOP")
        return None

    def _exec_tod(self, name, args, line):
        if len(args) < 4:
            return None
        mode = int(self._eval(args[0]))
        if self._tod_mode is not None and not (mode & self._tod_mode):
            return None

        now = self.clock.hours
        if name == "TOD":
            t_on = self._eval(args[2])
            t_off = self._eval(args[3])
            targets = [a for a in args[4:] if isinstance(a, Ref)]
            if t_on <= t_off:
                state = ON if t_on <= now < t_off else OFF
            else:  # schedule wraps midnight
                state = ON if (now >= t_on or now < t_off) else OFF
            for t in targets:
                self.command(t.name, state, "@NONE", line, "TOD")
            return None

        t1, v1 = self._eval(args[2]), self._eval(args[3])
        t2, v2 = self._eval(args[4]), self._eval(args[5])
        targets = [a for a in args[6:] if isinstance(a, Ref)]
        if t1 <= t2:
            value = v1 if t1 <= now < t2 else v2
        else:
            value = v1 if (now >= t1 or now < t2) else v2
        for t in targets:
            self.command(t.name, value, "@NONE", line, "TODSET")
        return None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _b(flag: bool) -> float:
    return ON if flag else OFF


def _status_value(word: str) -> float:
    if word in ("ON", "FAST", "ALARM", "PRFON", "HAND", "DAYMOD", "OK", "AUTO"):
        return ON
    if word in ("OFF", "FAILED", "DEAD", "NGTMOD", "LOW", "ALMACK"):
        return OFF
    if word == "SLOW":
        return 0.5
    return 0.0


def _fmt(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return "%.4g" % value
