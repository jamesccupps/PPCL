"""The test bench: run a PPCL program against simulated equipment.

Each timestep does four things, in this order:

1. Push the plant's **sensor** values into the panel's point database.
2. Run one PPCL pass.
3. Pull the points the program **commanded** back out to the plant.
4. Advance the physics.

That ordering matters and mirrors a real panel: the program acts on the values
it read at the top of the pass, and its outputs do not take effect until the
equipment responds.

On top of that sit two things that turn a simulation into a test:

* **Faults** you can inject at a given time -- a stuck damper, a failed sensor,
  a valve leaking by -- so you can ask "does my freeze protection actually
  catch this?" instead of hoping.
* **Checks** evaluated over the whole run, so a bench run passes or fails
  instead of producing a wall of numbers to read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .. import spec
from ..simulator import Clock, Panel, Simulator
from .systems import Plant


# --------------------------------------------------------------------------
# Bindings
# --------------------------------------------------------------------------


@dataclass
class Binding:
    """Connects one PPCL point to one plant variable.

    ``direction`` is ``"sensor"`` (plant to panel, what the program reads) or
    ``"command"`` (panel to plant, what the program drives).
    """

    point: str
    path: str
    direction: str = "sensor"
    scale: float = 1.0
    offset: float = 0.0

    def to_plant(self, panel_value: float) -> float:
        return panel_value * self.scale + self.offset

    def to_panel(self, plant_value: float) -> float:
        return plant_value * self.scale + self.offset


def bindings_from_dict(items) -> list:
    out = []
    for item in items:
        out.append(
            Binding(
                point=item["point"],
                path=item["path"],
                direction=item.get("direction", "sensor"),
                scale=float(item.get("scale", 1.0)),
                offset=float(item.get("offset", 0.0)),
            )
        )
    return out


# --------------------------------------------------------------------------
# Faults
# --------------------------------------------------------------------------


@dataclass
class Fault:
    """A fault injected into the plant at a given elapsed time."""

    at_seconds: float
    system: str
    kind: str
    value: float = 0.0
    applied: bool = False

    def describe(self) -> str:
        return "%s on %s (value %g) at t+%gs" % (
            self.kind,
            self.system,
            self.value,
            self.at_seconds,
        )


def inject(system, kind: str, value: float) -> bool:
    """Apply a named fault to a system. Returns False if unsupported."""
    ah = system
    if kind == "oa_damper_stuck":
        ah.mixing_box.outside_damper.actuator.position = value
        ah.mixing_box.outside_damper.actuator.stuck = True
    elif kind == "oa_damper_leak":
        ah.mixing_box.outside_damper.leakage = value
    elif kind == "hw_valve_leak":
        ah.heating_coil.valve.leak_by = value
    elif kind == "cw_valve_leak":
        ah.cooling_coil.valve.leak_by = value
    elif kind == "hw_valve_stuck":
        ah.heating_coil.valve.actuator.position = value
        ah.heating_coil.valve.actuator.stuck = True
    elif kind == "supply_fan_fail":
        ah.supply_fan.failed = True
        ah.supply_fan.running = False
    elif kind == "supply_fan_proof_fail":
        ah.supply_fan.proof_failed = True
    elif kind == "mat_sensor_fail":
        ah.mat_sensor.failed = True
        ah.mat_sensor.failed_value = value
    elif kind == "mat_sensor_stuck":
        ah.mat_sensor.stuck = True
    elif kind == "dat_sensor_fail":
        ah.dat_sensor.failed = True
        ah.dat_sensor.failed_value = value
    elif kind == "dat_sensor_offset":
        ah.dat_sensor.offset = value
    elif kind == "coil_fouling":
        ah.heating_coil.fouling = value
        ah.cooling_coil.fouling = value
    elif kind == "hot_water_loss":
        ah.heating_coil.water_temp = value
    elif kind == "chilled_water_loss":
        ah.cooling_coil.water_temp = value
    else:
        return False
    return True


#: Faults the bench knows how to inject, for the CLI and the UI.
FAULT_KINDS = [
    "oa_damper_stuck", "oa_damper_leak", "hw_valve_leak", "cw_valve_leak",
    "hw_valve_stuck", "supply_fan_fail", "supply_fan_proof_fail",
    "mat_sensor_fail", "mat_sensor_stuck", "dat_sensor_fail",
    "dat_sensor_offset", "coil_fouling", "hot_water_loss",
    "chilled_water_loss",
]


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Check:
    """An acceptance criterion evaluated over the whole run.

    ``kind`` is one of:

    * ``within``      -- ``var`` stays between ``low`` and ``high``
    * ``never_above`` / ``never_below``
    * ``never_true``  -- a digital variable is never 1 (a safety never trips)
    * ``eventually``  -- a digital variable reaches 1 at some point
    * ``settles``     -- ``var`` is within ``tolerance`` of ``target`` by
                         ``after`` seconds and stays there
    * ``max_cycles``  -- ``var`` changes state at most ``limit`` times
    """

    kind: str
    var: str
    name: str = ""
    low: float = None
    high: float = None
    target: float = None
    tolerance: float = 2.0
    after: float = 0.0
    limit: int = 0
    #: Ignore samples before this elapsed time, to skip startup transients.
    warmup: float = 0.0

    def label(self) -> str:
        return self.name or "%s %s" % (self.kind, self.var)

    def evaluate(self, history: list) -> CheckResult:
        samples = [
            s for s in history
            if s["elapsed"] >= self.warmup and self.var in s["values"]
        ]
        if not samples:
            return CheckResult(
                self.label(), False,
                "no samples recorded for %r -- check the variable name" % self.var,
            )

        series = [(s["elapsed"], s["values"][self.var]) for s in samples]

        if self.kind == "within":
            bad = [
                (t, v) for t, v in series
                if (self.low is not None and v < self.low)
                or (self.high is not None and v > self.high)
            ]
            if not bad:
                return CheckResult(self.label(), True,
                                   "stayed within %s to %s" % (self.low, self.high))
            t, v = bad[0]
            return CheckResult(
                self.label(), False,
                "%d of %d samples out of range; first at t+%.0fs with %.1f"
                % (len(bad), len(series), t, v),
            )

        if self.kind in ("never_above", "never_below"):
            limit = self.high if self.kind == "never_above" else self.low
            bad = [
                (t, v) for t, v in series
                if (v > limit if self.kind == "never_above" else v < limit)
            ]
            if not bad:
                return CheckResult(self.label(), True,
                                   "never went %s %s"
                                   % ("above" if self.kind == "never_above"
                                      else "below", limit))
            t, v = bad[0]
            worst = (max if self.kind == "never_above" else min)(v for _, v in bad)
            return CheckResult(
                self.label(), False,
                "breached at t+%.0fs (%.1f); worst was %.1f" % (t, v, worst),
            )

        if self.kind == "never_true":
            bad = [(t, v) for t, v in series if v >= 0.5]
            if not bad:
                return CheckResult(self.label(), True, "never asserted")
            return CheckResult(
                self.label(), False,
                "asserted at t+%.0fs and %d sample(s) total"
                % (bad[0][0], len(bad)),
            )

        if self.kind == "eventually":
            hits = [(t, v) for t, v in series if v >= 0.5]
            if hits:
                return CheckResult(self.label(), True,
                                   "first asserted at t+%.0fs" % hits[0][0])
            return CheckResult(self.label(), False, "never asserted")

        if self.kind == "settles":
            late = [(t, v) for t, v in series if t >= self.after]
            if not late:
                return CheckResult(self.label(), False,
                                   "run ended before t+%.0fs" % self.after)
            bad = [(t, v) for t, v in late if abs(v - self.target) > self.tolerance]
            if not bad:
                return CheckResult(
                    self.label(), True,
                    "held %.1f +/- %.1f after t+%.0fs"
                    % (self.target, self.tolerance, self.after),
                )
            worst = max(abs(v - self.target) for _, v in bad)
            return CheckResult(
                self.label(), False,
                "%d of %d late samples off target; worst error %.1f"
                % (len(bad), len(late), worst),
            )

        if self.kind == "max_cycles":
            transitions = 0
            last = None
            for _t, v in series:
                state = v >= 0.5
                if last is not None and state != last:
                    transitions += 1
                last = state
            starts = transitions // 2
            if starts <= self.limit:
                return CheckResult(self.label(), True,
                                   "%d start(s), limit %d" % (starts, self.limit))
            return CheckResult(
                self.label(), False,
                "%d start(s) exceeds the limit of %d -- short cycling"
                % (starts, self.limit),
            )

        return CheckResult(self.label(), False, "unknown check kind %r" % self.kind)


def checks_from_dict(items) -> list:
    return [Check(**item) for item in items]


# --------------------------------------------------------------------------
# The bench
# --------------------------------------------------------------------------


@dataclass
class BenchResult:
    """Everything a bench run produced."""

    history: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    blocked: list = field(default_factory=list)
    faults: list = field(default_factory=list)
    duration: float = 0.0

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def series(self, var: str):
        """Extract one variable as ``(elapsed, value)`` pairs, for charting."""
        return [
            (s["elapsed"], s["values"][var])
            for s in self.history
            if var in s["values"]
        ]

    def variables(self) -> list:
        seen = set()
        for s in self.history:
            seen.update(s["values"])
        return sorted(seen)

    def to_dict(self) -> dict:
        return {
            "duration": self.duration,
            "passed": self.passed,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
            "faults": self.faults,
            "warnings": self.warnings,
            "blocked": self.blocked,
            "variables": self.variables(),
            "history": self.history,
        }


class TestBench:
    """Couples a PPCL program to a simulated plant."""

    #: Not a pytest test class, despite the name.
    __test__ = False

    def __init__(self, program, plant: Plant, bindings: list,
                 clock: Clock = None, panel: Panel = None):
        self.plant = plant
        self.bindings = list(bindings)
        self.panel = panel or Panel()
        self.clock = clock or Clock(hours=6.0)
        self.simulator = Simulator(program, panel=self.panel, clock=self.clock)
        self.faults = []
        self.checks = []
        #: Sample the history every N seconds of simulated time.
        self.sample_interval = 30.0
        #: Program lines the panel evaluates per second. The manual quotes
        #: ~350 on a Version 3.0 controller board and ~500 on a 4.0, shared
        #: across every enabled program in the panel.
        self.lines_per_second = 500.0

        self._validate_bindings()

    def _validate_bindings(self) -> None:
        """Fail loudly on a mistyped path rather than silently reading zero."""
        for b in self.bindings:
            if b.direction == "sensor":
                self.plant.read(b.path)
            elif b.direction == "command":
                system, name = self.plant._split(b.path)
                probe = system.outputs()
                if not system.apply(name, 0.0):
                    raise KeyError(
                        "binding for point %s targets %r, which is not a "
                        "command on system %s" % (b.point, name, system.name)
                    )
            else:
                raise ValueError(
                    "binding for point %s has direction %r; expected 'sensor' "
                    "or 'command'" % (b.point, b.direction)
                )

    def add_fault(self, fault: Fault) -> None:
        self.faults.append(fault)

    def add_check(self, check: Check) -> None:
        self.checks.append(check)

    # -- the loop ----------------------------------------------------------

    def run(self, seconds: float = 3600.0, dt: float = 5.0) -> BenchResult:
        """Run for ``seconds`` of simulated time in ``dt`` steps."""
        result = BenchResult(duration=seconds)
        elapsed = 0.0
        next_sample = 0.0
        applied_faults = []

        while elapsed < seconds:
            # Faults land before the pass that should react to them.
            for fault in self.faults:
                if not fault.applied and elapsed >= fault.at_seconds:
                    system = self.plant.systems.get(fault.system.upper())
                    if system is None:
                        result.warnings.append(
                            "fault targets unknown system %r" % fault.system
                        )
                    elif not inject(system, fault.kind, fault.value):
                        result.warnings.append(
                            "system %s does not support fault %r"
                            % (system.name, fault.kind)
                        )
                    else:
                        applied_faults.append(
                            "t+%.0fs: %s" % (elapsed, fault.describe())
                        )
                    fault.applied = True

            # 1. Sensors into the panel.
            for b in self.bindings:
                if b.direction == "sensor":
                    self.panel.get(b.point).value = b.to_panel(
                        self.plant.read(b.path)
                    )

            # 2. Run the panel's line budget for this interval.
            self.simulator.execute_lines(
                max(1, int(self.lines_per_second * dt))
            )

            # 3. Commands out to the plant.
            for b in self.bindings:
                if b.direction == "command":
                    self.plant.write(b.path, b.to_plant(self.panel.value(b.point)))

            # 4. Physics.
            self.plant.step(dt, self.clock.hours)
            self.clock.advance(dt)
            self.simulator._accumulate_runtime(dt)
            elapsed += dt

            if elapsed >= next_sample:
                result.history.append(self._sample(elapsed))
                next_sample += self.sample_interval

        result.history.append(self._sample(elapsed))
        result.faults = applied_faults
        result.warnings.extend(self.simulator.warnings)
        starved = self.simulator.starved_lines
        if starved:
            result.warnings.append(
                "%d executable line(s) never ran during the whole run: %s. "
                "On a panel that is a starved branch -- the program keeps "
                "looping but never reaches them."
                % (len(starved), ", ".join(str(n) for n in starved[:12]))
            )
        seen = set()
        for e in self.simulator.events:
            if e.kind == "blocked" and e.text not in seen:
                seen.add(e.text)
                result.blocked.append("line %s: %s" % (e.line, e.text))
        result.checks = [c.evaluate(result.history) for c in self.checks]
        return result

    def _sample(self, elapsed: float) -> dict:
        values = self.plant.snapshot()
        for name, point in self.panel.points.items():
            values["pt.%s" % name] = point.value
            if point.priority != "@NONE":
                values["prio.%s" % name] = float(
                    spec.PRIORITY_RANK.get(point.priority, 0)
                )
        return {
            "elapsed": elapsed,
            "hours": self.clock.hours,
            "values": values,
        }


# --------------------------------------------------------------------------
# Scenario files
# --------------------------------------------------------------------------


def load_scenario(path: str) -> dict:
    """Load a bench scenario from JSON."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def build_from_scenario(program, scenario: dict) -> TestBench:
    """Construct a bench from a scenario dictionary.

    See ``scenarios/`` for worked examples of the format.
    """
    from .library import build_plant

    plant = build_plant(scenario.get("plant", {"preset": "single_zone_ahu"}))
    bindings = bindings_from_dict(scenario.get("bindings", []))

    clock_cfg = scenario.get("clock", {})
    clock = Clock(
        hours=float(clock_cfg.get("hours", 6.0)),
        day_of_week=int(clock_cfg.get("day_of_week", 2)),
        day_of_month=int(clock_cfg.get("day_of_month", 15)),
        month=int(clock_cfg.get("month", 1)),
    )

    bench = TestBench(program, plant, bindings, clock=clock)
    bench.sample_interval = float(scenario.get("sample_interval", 30.0))

    for item in scenario.get("points", []) if isinstance(
        scenario.get("points"), list
    ) else []:
        bench.panel.load({item["point"]: item["value"]})
    if isinstance(scenario.get("points"), dict):
        bench.panel.load(scenario["points"])

    for item in scenario.get("faults", []):
        bench.add_fault(
            Fault(
                at_seconds=float(item.get("at", 0)),
                system=item["system"],
                kind=item["fault"],
                value=float(item.get("value", 0)),
            )
        )
    for item in scenario.get("checks", []):
        bench.add_check(Check(**item))
    return bench
