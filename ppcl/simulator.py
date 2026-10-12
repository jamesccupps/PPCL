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

import math

from dataclasses import dataclass, field

from . import spec
from .ast_nodes import Program, Ref

ON = 1.0
OFF = 0.0

#: The arithmetic functions, by PPCL name. Trig works in degrees, as PPCL does.
#:
#: LOG is the COMMON log. spec.FUNCTIONS was corrected to say so on 2026-10-08
#: -- Siemens' shipped library computes a natural log as 2.3026 times LOG,
#: which only holds in base 10 -- and this table was not, so for three days
#: the simulator ran every psychrometric program with LOG as the natural log,
#: off by a factor of ln(10), and returned a silent 0.0 for LN, which it did
#: not implement at all. A test now checks every name in spec.FUNCTIONS
#: resolves here or is handled explicitly, so the next one cannot slip past.
_MATH = {
    "SQRT": lambda v: math.sqrt(v) if v >= 0 else 0.0,
    "LOG": lambda v: math.log10(v) if v > 0 else 0.0,
    "LN": lambda v: math.log(v) if v > 0 else 0.0,
    "EXP": math.exp,
    "SIN": lambda v: math.sin(math.radians(v)),
    "COS": lambda v: math.cos(math.radians(v)),
    "TAN": lambda v: math.tan(math.radians(v)),
    "ATN": lambda v: math.degrees(math.atan(v)),
    "COM": lambda v: OFF if v else ON,
}

#: The value each simple state command writes. Was a dict literal built inside
#: the command handler, so every ON or OFF allocated a fresh one to look up.
_STATE_VALUE = {"ON": ON, "OFF": OFF, "AUTO": ON, "FAST": ON, "SLOW": 0.5}

#: Warnings for functions the simulator cannot compute exactly. Constants,
#: because the compiler (ppcl/simcompile.py) is what raises them.
UNKNOWABLE_FUNCTION_WARNING = (
    "%s cannot be simulated (%s); anything computed from it is only as sound "
    "as that assumption")
UNSIMULATED_FUNCTION_WARNING = "function %s is not simulated; it evaluated to 0"
EXP_WARNING = (
    "EXP is simulated as e**x, but whether PPCL's EXP is base e or base 10 is "
    "not established -- see open question 12 in docs/PPCL-REFERENCE.md. Treat "
    "results built on it as unconfirmed")

#: Functions that read something a simulator does not have. ALMPRI is a
#: point's alarm priority, which lives in the panel's alarm configuration.
_UNKNOWABLE_FUNCTIONS = {"ALMPRI": "a point's alarm priority, 0 here"}

#: Commands the simulator recognises but does not model.
UNMODELLED = frozenset(
    {"PDL", "PDLDAT", "PDLDPG", "PDLMTR", "PDLSET", "SSTO", "SSTOCO",
     "OIP", "DC", "DCR", "DPHONE", "EPHONE", "DISCOV", "ENCOV",
     "DISALM", "ENALM", "ALARM", "NORMAL", "HLIMIT", "LLIMIT", "INITTO",
     "DEFINE", "DAY", "NIGHT",
     # Added 2026-09-24. These seven used to fall past every branch of
     # _exec_command to a bare `return None` -- not modelled AND not warned
     # about, which is the worst of both. A program whose ADAPTM drives a
     # damper simulated cleanly, left cv at whatever it already held, and any
     # IF downstream took the wrong branch with nothing to say why. The fix
     # is not to model them: an invented adaptive output or curve fit would
     # be a confident wrong number, which is what LOOP's standing disclaimer
     # exists to avoid. The fix is to say so.
     "ADAPTM", "ADAPTS", "LSQ2", "LSQDAT", "LSTSQR", "GETVAL", "SETVAL"}
)

#: For an unmodelled command, which argument positions are points it WOULD
#: have written. Naming them in the warning is the difference between "this
#: was skipped" and "these values are stale and everything downstream of them
#: is unsound". Positions are zero-based into the argument list.
UNMODELLED_OUTPUTS = {
    "ADAPTM": (1, 13), "ADAPTS": (1, 13),
    "LSQ2": (1, 2, 3, 4, 5, 6),
    "LSTSQR": (1, 2, 3),
    "GETVAL": (0,),
    "SSTO": (2, 3),
    "TIMAVG": (),
}

#: Resident points whose value no offline simulator can know, paired with the
#: stand-in this one returns. Siemens' own offline tooling declines to model
#: exactly these, on the stated grounds that they "require a firmware
#: environment": NODE0 through NODE99, ADAPTM, ADAPTS, ALMCNT, ALMCT2, OIP,
#: ONPWRT, $BATT and LINK. $PDL is added on the same reasoning: it reports
#: the panel's load-shed state, which exists only in firmware.
#:
#: The stand-in is still returned, so a program that reads one of these runs
#: to completion instead of aborting. What changes is that the run says so.
#: An arithmetic result built on a stand-in looks exactly like a real one.
UNMODELLED_RESIDENTS = {
    "LINK": "BLN link status, ON here",
    "$BATT": "battery condition, ON here",
    "ALMCNT": "panel alarm count, 0 here",
    "ALMCT2": "second alarm counter, 0 here",
    "$PDL": "PDL load-shed state, 0 here",
}


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
        #: Which event kinds to record: True for all, False for none, or a set
        #: of kinds. The bench passes {"blocked"} because that is all it reads,
        #: and recording every write cost a formatted string and an Event on
        #: most lines of every pass -- ~340,000 of each per simulated hour,
        #: built and then thrown away.
        self._trace_kinds = (None if trace is True
                             else frozenset() if not trace
                             else frozenset(trace))
        self.trace_enabled = bool(trace)
        self.events = []
        self.warnings = []

        self.index = program.by_number()
        self.numbers = sorted(self.index)
        # Both fixed for this simulator's lifetime -- the debugger builds a
        # fresh one rather than editing these -- so they are worked out once
        # here instead of on every line of every pass. A branch used to find
        # its target with numbers.index(), a scan of the whole program per GOTO.
        self._pc_of = {number: pc for pc, number in enumerate(self.numbers)}
        # Lines the panel can never evaluate: comments, and statements the
        # compiler saved as UNKNOWN(...). PARAMETER lines keep their existing
        # handling.
        self._skip_at = [
            self.index[n].is_comment or self.index[n].unknown
            for n in self.numbers
        ]
        # Lines disabled right now, which ACT/ENABLE and DEACT/DISABL change
        # as the program runs. A PXC.A line written with "# " starts here: on
        # that generation the "# " IS the line's disabled state, so it is off
        # until something enables it, not off for ever. It used to run
        # regardless, because nothing asked.
        self.disabled_lines = {n for n in self.numbers if self.index[n].disabled}
        self.parameters = {}
        self.locals_declared = set()
        #: How to read each point name, decided on its first read. See
        #: _reader_for(). Filled lazily; must exist before the directives
        #: below are evaluated.
        self._readers = {}

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

        # Every line compiled once, to a closure that does what the statement
        # does -- see ppcl/simcompile.py, which is the only implementation of
        # PPCL execution there is. _expr_code maps a program expression's id
        # to its compiled form, for the handlers that evaluate their own
        # arguments.
        from . import simcompile

        self._expr_code = {}
        self._line_code = [
            None if skip else simcompile.compile_stmt(
                self, self.index[n].stmt, n, self._expr_code)
            for n, skip in zip(self.numbers, self._skip_at)
        ]

        for directive in program.directives:
            self.parameters[directive.name.upper()] = self._eval(directive.value)

    # -- tracing -----------------------------------------------------------

    def _traces(self, kind) -> bool:
        kinds = self._trace_kinds
        return kinds is None or kind in kinds

    def _emit(self, line, kind, text):
        if self._traces(kind):
            self.events.append(Event(self.clock.hours, line, kind, text))

    def _warn(self, text):
        if text not in self.warnings:
            self.warnings.append(text)

    def _unranked_priority(self, point, priority, line, what) -> None:
        """Say that a priority fight was declined, not decided."""
        extra = ""
        if priority in spec.NON_PPCL_PRIORITIES:
            extra = (" PPCL has no %s literal, so this priority can only have "
                     "come from an operator or the panel, and where it sits "
                     "relative to the five PPCL levels is not published."
                     % priority)
        self._warn(
            "%s is at %s, which has no rank here, so %s at line %d was "
            "refused rather than arbitrated.%s"
            % (point, priority, what, line, extra)
        )

    def _warn_resident(self, name: str, why: str = "") -> None:
        """Record that a resident point's value is a stand-in, not a result."""
        why = why or UNMODELLED_RESIDENTS.get(name, "value unknown offline")
        self._warn(
            "%s is a resident point no offline simulator can know (%s); "
            "Siemens' own offline tooling ignores it too, so anything computed "
            "from it is only as sound as that assumption" % (name, why)
        )

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
            self._warn_resident(upper)
            return ON
        if upper == "$BATT":
            self._warn_resident(upper)
            return ON
        if upper in ("ALMCNT", "ALMCT2", "$PDL"):
            self._warn_resident(upper)
            return 0.0
        if upper.startswith("NODE") and upper[4:].isdigit():
            self._warn_resident(upper, "peer panel online status, ON here")
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
        if pt.priority not in spec.PRIORITY_RANK:
            # An unranked priority used to resolve to rank 0, the lowest, so
            # PPCL at @NONE won and overwrote it. That is the optimistic
            # answer in a fight whose outcome is unknown, and it is silent.
            self._unranked_priority(name, pt.priority, line, what)
            self._emit(
                line, "blocked",
                "%s %s blocked: point is at %s, whose rank is unknown"
                % (what, name, pt.priority),
            )
            return False
        cur_rank = spec.PRIORITY_RANK[pt.priority]
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
        if old != value and self._traces("write"):
            self._emit(
                line, "write",
                "%s = %s (%s%s)"
                % (name, _fmt(value), what,
                   "" if priority == "@NONE" else " at " + priority),
            )
        return True

    def release(self, name: str, priority: str, line: int) -> bool:
        pt = self.panel.get(name)
        if pt.priority not in spec.PRIORITY_RANK:
            # Worse here than in command(): a bare RELEAS carries no priority
            # at all, so it skipped the comparison entirely and cleared the
            # point to @NONE. An operator override would have been thrown
            # away by a program that cannot name that priority.
            self._unranked_priority(name, pt.priority, line, "RELEAS")
            self._emit(
                line, "blocked",
                "RELEAS %s refused: point is at %s, whose rank is unknown"
                % (name, pt.priority),
            )
            return False
        cur_rank = spec.PRIORITY_RANK[pt.priority]
        rel_rank = spec.PRIORITY_RANK.get(priority or pt.priority, 0)
        if priority is not None and rel_rank < cur_rank:
            self._emit(
                line, "blocked",
                "RELEAS %s at %s does not clear %s priority"
                % (name, priority, pt.priority),
            )
            return False
        if pt.priority != "@NONE" and self._traces("write"):
            self._emit(line, "write",
                       "%s released from %s to @NONE" % (name, pt.priority))
        pt.priority = "@NONE"
        return True

    # -- expression evaluation --------------------------------------------

    def _eval(self, node) -> float:
        """The value of an expression.

        A program's own expressions were compiled with it. Anything else -- a
        debugger's condition, parsed afresh on every check -- is compiled here
        and not kept, because a temporary node's id can be reused by the next.
        """
        if node is None:
            return 0.0
        fn = self._expr_code.get(id(node))
        if fn is None:
            from . import simcompile

            fn = simcompile.compile_expr(self, node, {})
        return fn()

    def _reader(self, name: str):
        """The function that reads ``name``, made on first use. See below."""
        reader = self._readers.get(name)
        if reader is None:
            reader = self._readers[name] = self._reader_for(name)
        return reader

    def _reader_for(self, name: str):
        """A function that reads ``name`` the way _eval_ref + _read would.

        Every read used to upper-case the name three times and walk a dozen
        comparisons -- status indicator? clock? resident? parameter? -- to an
        answer that never changes for a given name. That decision is made
        once here. The branch order is _read's, exactly, and the golden
        record of bench and trace output is byte-identical across the change.

        Two things are deliberately still decided per read. Resident points
        warn on every read, as before, so a consumer that clears warnings
        sees them come back. And an ordinary name checks the parameters on
        every read, because a PARAMETER statement can run mid-program.
        """
        upper = name.upper()
        clock = self.clock
        if upper in spec.STATUS_INDICATORS:
            constant = _status_value(upper)
            return lambda: constant
        if upper in ("TIME", "CRTIME"):
            return lambda: clock.hours
        if upper == "DAY":
            return lambda: float(clock.day_of_week)
        if upper == "DAYOFM":
            return lambda: float(clock.day_of_month)
        if upper == "MONTH":
            return lambda: float(clock.month)
        if upper == "SECNDS" or (upper.startswith("SECND")
                                 and upper[5:].isdigit()):
            return lambda: float(int(clock.elapsed) % 60)
        if (upper in ("LINK", "$BATT", "ALMCNT", "ALMCT2", "$PDL")
                or (upper.startswith("NODE") and upper[4:].isdigit())):
            return lambda: self._read(name)    # warns on every read
        params = self.parameters
        panel = self.panel

        def ordinary():
            if upper in params:
                return params[upper]
            point = panel.points.get(upper)
            return point.value if point is not None else panel.value(name)
        return ordinary

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

            if self._skip_at[self._pc] or number in self.disabled_lines:
                self._pc += 1
                continue

            executed += 1
            self._executed_lines.add(number)
            jump = self._line_code[self._pc](self._call_stack)

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
            self._pc = self._pc_of[target]
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

    # -- command implementations kept as methods ---------------------------
    #
    # The compiler binds these directly. Everything else a statement can do
    # lives in ppcl/simcompile.py.

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
