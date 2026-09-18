"""An interactive debugger for a running PPCL program.

The simulator already executes a program the way a panel does -- a persistent
program counter, a call stack, a fixed line budget shared out over time -- so
this module is exposure rather than new machinery. What it adds is the ability
to stop.

Three ways to stop, which between them cover how these programs actually
misbehave:

* a **line breakpoint**, for "why does this branch never run";
* a **point watchpoint**, for "something is writing this and I cannot find
  what" -- the debugger stops on the write and names the line;
* a **condition**, evaluated in the panel's own expression language against
  live point values, for "stop when the discharge goes below 45 while the fan
  is proved".

Two things you can do at a stop that you cannot do on a panel: override a
point value and carry on, and step one statement at a time while watching the
call stack. Both are the reason to test here before loading anything.

Everything is read-only with respect to real buildings. This drives a
simulated panel; it does not open a socket.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import spec
from . import parser as ppcl_parser
from .ast_nodes import Comment, Unparsed
from .simulator import Clock, Panel, Simulator


class DebugError(Exception):
    """A debugger request that cannot be honoured."""


@dataclass
class Breakpoint:
    """A stop request."""

    kind: str                  # "line" | "write" | "condition"
    #: Line number for a line breakpoint.
    line: int = None
    #: Point name for a write watchpoint.
    point: str = None
    #: PPCL expression for a conditional breakpoint.
    expression: str = None
    enabled: bool = True
    #: Skip this many hits before stopping. Useful in a main loop.
    skip: int = 0
    hits: int = 0
    label: str = ""

    def describe(self):
        if self.kind == "line":
            return "line %s" % self.line
        if self.kind == "write":
            return "write to %s" % self.point
        return "when %s" % self.expression


@dataclass
class Stop:
    """Why execution stopped, and where."""

    reason: str                # "breakpoint" | "step" | "budget" | "end"
    line: int = None
    detail: str = ""
    breakpoint: Breakpoint = None


@dataclass
class Frame:
    """One entry in the GOSUB call stack, as the UI shows it."""

    called_from: int
    entry: int


class DebugSession:
    """A program, a simulated panel, and a place to stop.

    The session owns a :class:`~ppcl.simulator.Simulator` and drives it one
    statement at a time. Statement-at-a-time is slower than the bench's line
    budget, which is why the bench does not use this: a debugging session runs
    at human speed by definition.
    """

    #: Ceiling on statements executed by one ``run`` call, so a program with
    #: no reachable breakpoint returns instead of hanging the UI.
    #:
    #: Sized for a person waiting: about a second of simulated execution, and
    #: several thousand passes of a typical main loop. When it runs out the
    #: session is left exactly where it stopped, so pressing Run again
    #: continues -- which is the right shape for "I do not know how long this
    #: takes to happen".
    RUN_BUDGET = 50_000

    def __init__(self, text, panel=None, clock=None,
                 firmware=spec.Firmware.APOGEE, name="debug"):
        self.program = ppcl_parser.parse(text, name=name)
        if self.program.errors:
            raise DebugError(
                "the program does not parse; fix the %d error(s) first"
                % len(self.program.errors)
            )
        self.sim = Simulator(
            self.program, panel=panel or Panel(), clock=clock or Clock(),
            trace=True, firmware=firmware,
        )
        self.breakpoints = []
        self.stopped = None
        self.started = False
        self.finished = False
        self.watch = []
        #: Statements executed since the session began, for the status line.
        self.executed = 0
        #: Seconds of simulated time to advance per statement. A panel
        #: evaluates roughly 500 lines a second, so this is its reciprocal.
        self.seconds_per_line = 1.0 / 500.0
        self._write_log = []
        self._install_write_hook()

    # -- breakpoints -------------------------------------------------------

    def add_breakpoint(self, bp: Breakpoint) -> Breakpoint:
        if bp.kind == "line":
            if bp.line not in self.sim.index:
                raise DebugError(
                    "there is no line %s in this program" % bp.line
                )
            if self.sim.index[bp.line].is_comment:
                raise DebugError(
                    "line %s is a comment, so execution never stops there"
                    % bp.line
                )
        elif bp.kind == "write":
            if not bp.point:
                raise DebugError("a write breakpoint needs a point name")
            bp.point = bp.point.upper()
        elif bp.kind == "condition":
            if not bp.expression:
                raise DebugError("a conditional breakpoint needs an expression")
            self._compile_condition(bp.expression)  # fail now, not mid-run
        else:
            raise DebugError("unknown breakpoint kind %r" % bp.kind)
        self.breakpoints.append(bp)
        return bp

    def remove_breakpoint(self, index: int) -> None:
        if not 0 <= index < len(self.breakpoints):
            raise DebugError("no such breakpoint")
        del self.breakpoints[index]

    def clear_breakpoints(self) -> None:
        self.breakpoints = []

    def _compile_condition(self, expression):
        """Parse a condition, reusing the panel's own expression grammar."""
        wrapper = ppcl_parser.parse("1 IF(%s) THEN ON(X)" % expression)
        if wrapper.errors:
            raise DebugError(
                "that is not a PPCL expression: %s" % wrapper.errors[0][2]
            )
        line = wrapper.lines[0]
        return line.stmt.cond

    # -- point access ------------------------------------------------------

    def set_point(self, name, value, priority="@NONE"):
        """Override a point mid-run. This is the whole reason to debug here."""
        point = self.sim.panel.get(name)
        self.sim.panel.load({name: value})
        point.priority = priority
        return self.point(name)

    def point(self, name):
        point = self.sim.panel.get(name)
        return {
            "name": point.name,
            "value": round(point.value, 4),
            "priority": point.priority,
            "type": point.ptype,
            "runtime": round(point.runtime, 1),
        }

    def points(self):
        return [self.point(n) for n in sorted(self.sim.panel.points)]

    def add_watch(self, name):
        upper = name.upper()
        if upper not in [w.upper() for w in self.watch]:
            self.watch.append(name)
        return self.watch

    def remove_watch(self, name):
        self.watch = [w for w in self.watch if w.upper() != name.upper()]
        return self.watch

    # -- write tracking ----------------------------------------------------

    def _install_write_hook(self):
        """Record every accepted command so write breakpoints can fire.

        Wrapping ``command`` rather than scanning the trace afterwards means a
        watchpoint stops on the statement that did it, with the call stack
        still intact.
        """
        original = self.sim.command
        log = self._write_log

        def command(name, value, priority, line, what):
            accepted = original(name, value, priority, line, what)
            if accepted:
                log.append((name.upper(), value, line))
            return accepted

        self.sim.command = command

    # -- execution ---------------------------------------------------------

    @property
    def current_line(self):
        if not self.sim.numbers:
            return None
        index = self.sim._pc
        if index >= len(self.sim.numbers):
            index = 0
        return self.sim.numbers[index]

    def call_stack(self):
        frames = []
        for pc in self.sim._call_stack:
            index = min(pc, len(self.sim.numbers) - 1)
            frames.append(Frame(called_from=self.sim.numbers[max(index - 1, 0)],
                                entry=self.sim.numbers[index]))
        return frames

    def step(self, count=1) -> Stop:
        """Execute ``count`` statements, ignoring breakpoints."""
        for _ in range(max(1, count)):
            if not self._execute_one():
                return Stop("end", self.current_line, "the program has no code")
        return Stop("step", self.current_line, "stepped")

    def step_over(self) -> Stop:
        """Execute one statement, running any GOSUB it makes to completion."""
        depth = len(self.sim._call_stack)
        stop = self.step()
        guard = 0
        while len(self.sim._call_stack) > depth and guard < self.RUN_BUDGET:
            guard += 1
            hit = self._check_breakpoints()
            if hit is not None:
                return hit
            self._execute_one()
        return stop

    def step_out(self) -> Stop:
        """Run until the current subroutine returns."""
        if not self.sim._call_stack:
            return self.run()
        depth = len(self.sim._call_stack)
        guard = 0
        while len(self.sim._call_stack) >= depth and guard < self.RUN_BUDGET:
            guard += 1
            hit = self._check_breakpoints()
            if hit is not None:
                return hit
            self._execute_one()
        return Stop("step", self.current_line, "returned from subroutine")

    def run(self, budget=None) -> Stop:
        """Run until a breakpoint fires or the budget runs out."""
        budget = budget or self.RUN_BUDGET
        for _ in range(budget):
            if not self._execute_one():
                return Stop("end", None, "the program has no executable code")
            hit = self._check_breakpoints()
            if hit is not None:
                self.stopped = hit
                return hit
        self.stopped = Stop(
            "budget", self.current_line,
            "ran %d statements without hitting a breakpoint. Press Run again "
            "to carry on from here." % budget,
        )
        return self.stopped

    def run_to(self, line) -> Stop:
        """Run until a specific line is about to execute."""
        temp = Breakpoint(kind="line", line=int(line), label="run to cursor")
        self.add_breakpoint(temp)
        try:
            return self.run()
        finally:
            if temp in self.breakpoints:
                self.breakpoints.remove(temp)

    def _execute_one(self) -> bool:
        """Execute exactly one statement. Returns False if there is none."""
        if not self.sim.numbers:
            self.finished = True
            return False
        self.started = True
        before = len(self._write_log)
        self.sim.execute_lines(1)
        self.executed += 1
        self.sim.clock.advance(self.seconds_per_line)
        self._last_writes = self._write_log[before:]
        return True

    def _check_breakpoints(self):
        """Return a :class:`Stop` if any enabled breakpoint fires."""
        line = self.current_line
        for bp in self.breakpoints:
            if not bp.enabled:
                continue
            fired = False
            detail = ""
            if bp.kind == "line" and line == bp.line:
                fired = True
                detail = "about to execute line %d" % bp.line
            elif bp.kind == "write":
                for name, value, at in getattr(self, "_last_writes", ()):
                    if name == bp.point:
                        fired = True
                        detail = ("line %d wrote %s to %s"
                                  % (at, _fmt(value), name))
                        break
            elif bp.kind == "condition":
                try:
                    node = self._compile_condition(bp.expression)
                    if self.sim._eval(node) >= 0.5:
                        fired = True
                        detail = "%s is true" % bp.expression
                except DebugError:
                    continue
                except Exception as exc:
                    detail = "could not evaluate %s: %s" % (bp.expression, exc)
                    fired = True
            if not fired:
                continue
            bp.hits += 1
            if bp.skip and bp.hits <= bp.skip:
                continue
            return Stop("breakpoint", line, detail, bp)
        return None

    # -- reporting ---------------------------------------------------------

    def state(self, source_lines=6):
        """Everything the UI needs to paint one stopped frame."""
        line = self.current_line
        stmt = self.sim.index.get(line) if line is not None else None
        return {
            "line": line,
            "source": stmt.body if stmt is not None else "",
            "executed": self.executed,
            "clock": {
                "time": round(self.sim.clock.hours, 4),
                "clock_text": _clock_text(self.sim.clock.hours),
                "day_of_week": self.sim.clock.day_of_week,
                "elapsed": round(self.sim.clock.elapsed, 2),
            },
            "stack": [
                {"called_from": f.called_from, "entry": f.entry}
                for f in self.call_stack()
            ],
            "watch": [self.point(name) for name in self.watch],
            "breakpoints": [
                {
                    "index": i, "kind": bp.kind, "line": bp.line,
                    "point": bp.point, "expression": bp.expression,
                    "enabled": bp.enabled, "hits": bp.hits, "skip": bp.skip,
                    "label": bp.label or bp.describe(),
                }
                for i, bp in enumerate(self.breakpoints)
            ],
            "stopped": (
                {
                    "reason": self.stopped.reason,
                    "line": self.stopped.line,
                    "detail": self.stopped.detail,
                }
                if self.stopped
                else None
            ),
            "coverage": sorted(self.sim._executed_lines),
            "starved": self.sim.starved_lines,
            "warnings": list(self.sim.warnings),
            "events": [
                {"time": round(e.time, 4), "line": e.line, "kind": e.kind,
                 "text": e.text}
                for e in self.sim.events[-40:]
            ],
            "blocked": [
                e.text for e in self.sim.events if e.kind == "blocked"
            ][-10:],
        }

    def reset(self, keep_points=False):
        """Restart the program, optionally keeping the current point values."""
        snapshot = self.sim.panel.snapshot() if keep_points else None
        panel = Panel()
        if snapshot:
            panel.points = snapshot
        clock = Clock(hours=self.sim.clock.hours,
                      day_of_week=self.sim.clock.day_of_week)
        breakpoints = self.breakpoints
        watch = self.watch
        self.sim = Simulator(self.program, panel=panel, clock=clock,
                             trace=True, firmware=self.sim.firmware)
        self._write_log = []
        self._install_write_hook()
        self.breakpoints = breakpoints
        for bp in self.breakpoints:
            bp.hits = 0
        self.watch = watch
        self.executed = 0
        self.stopped = None
        self.started = False
        self.finished = False


def _fmt(value):
    return "%.1f" % value if float(value) == int(value) else "%g" % value


def _clock_text(hours):
    hours = hours % 24.0
    h = int(hours)
    m = int(round((hours - h) * 60))
    if m == 60:
        h, m = (h + 1) % 24, 0
    return "%02d:%02d" % (h, m)
