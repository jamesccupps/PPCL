"""The sequence document: a structured description of a control sequence.

This is the intermediate representation the whole authoring side is built on.
A decision table, a block editor and a text file are three renderings of the
same document, and :mod:`ppcl.sequence.compiler` turns it into PPCL.

The shape follows Siemens' own recommended planning method (Desigo CC, "PPCL
Program Plans"): read the sequence of operation, **determine the modes of
operation**, identify the controls, then organise them as a decision table.
So the document is modes plus a table of equipment against those modes, with
the things a table cannot express -- interlocks, resets, loops -- alongside it.

Compilation is one-way by design. A document compiles to PPCL reliably;
arbitrary hand-written PPCL does not lift back into a document, because the
GOTO structure of real programs carries intent a table cannot represent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict


# --------------------------------------------------------------------------
# Conditions
# --------------------------------------------------------------------------

#: Comparison operators, in the document's plain spelling, mapped to PPCL.
COMPARISONS = {
    "<": ".LT.",
    "<=": ".LE.",
    ">": ".GT.",
    ">=": ".GE.",
    "=": ".EQ.",
    "==": ".EQ.",
    "!=": ".NE.",
    "<>": ".NE.",
    "is": ".EQ.",
    "is not": ".NE.",
}

#: Words that may appear on the right of a comparison as a point status.
STATUS_WORDS = {
    "on", "off", "auto", "fast", "slow", "alarm", "failed", "hand",
    "daymod", "ngtmod", "prfon", "ok", "dead",
}


class Condition:
    """Base class for a testable condition."""

    def points(self):
        """Yield every point name the condition reads."""
        return iter(())


@dataclass
class Compare(Condition):
    """``left op right`` -- e.g. ``MAT < 38`` or ``SFAN is on``."""

    left: str
    op: str
    right: object

    def points(self):
        yield self.left
        if isinstance(self.right, str) and self.right.lower() not in STATUS_WORDS:
            yield self.right


@dataclass
class Between(Condition):
    """``left between low and high`` -- the natural way to write a schedule."""

    left: str
    low: object
    high: object

    def points(self):
        yield self.left


@dataclass
class All(Condition):
    """Every sub-condition must hold."""

    parts: list = field(default_factory=list)

    def points(self):
        for part in self.parts:
            yield from part.points()


@dataclass
class Any(Condition):
    """At least one sub-condition must hold."""

    parts: list = field(default_factory=list)

    def points(self):
        for part in self.parts:
            yield from part.points()


@dataclass
class Not(Condition):
    """Inverts a condition."""

    part: Condition = None

    def points(self):
        if self.part is not None:
            yield from self.part.points()


@dataclass
class Always(Condition):
    """The fallback condition of the last mode."""

    def points(self):
        return iter(())


# --------------------------------------------------------------------------
# Document parts
# --------------------------------------------------------------------------

#: What a decision-table cell may say. Taken from Siemens' own example table,
#: which uses Off / On / Closed / Modulate.
CELL_ACTIONS = {
    "on": "command the point ON",
    "off": "command the point OFF",
    "auto": "command the point to AUTO",
    "fast": "command the point to FAST",
    "slow": "command the point to SLOW",
    "open": "drive the point to 100",
    "closed": "drive the point to 0",
    "modulate": "leave the point to its control loop",
    "hold": "issue no command; leave the point wherever it is",
}


@dataclass
class Point:
    """A point the sequence uses."""

    name: str
    kind: str = "analog"  # "analog" | "digital"
    role: str = "output"  # "input" | "output" | "virtual" | "local"
    units: str = ""
    description: str = ""

    @property
    def is_local(self) -> bool:
        return self.role == "local"


@dataclass
class Mode:
    """One operating mode.

    Modes are listed lowest priority first. The last mode whose condition
    holds wins, so a shutdown mode placed after the occupied mode overrides it.
    """

    name: str
    when: Condition = field(default_factory=Always)
    description: str = ""


@dataclass
class Interlock:
    """A latching safety.

    ``trip`` latches it, ``reset`` clears it, and while latched the listed
    actions are forced at ``priority``. The compiler always emits the matching
    RELEAS, because omitting it is the single most common PPCL field defect.
    """

    name: str
    trip: Condition = None
    reset: Condition = None
    priority: str = "@EMER"
    #: (point, action) pairs forced while the interlock is latched.
    forces: list = field(default_factory=list)
    description: str = ""


@dataclass
class Reset:
    """A linear reset schedule, compiled to TABLE."""

    output: str
    source: str
    #: (x, y) breakpoints, x ascending.
    points: list = field(default_factory=list)
    description: str = ""


@dataclass
class Loop:
    """A PID loop, compiled to LOOP."""

    name: str
    process: str  # the measured variable
    output: str
    setpoint: str
    action: str = "reverse"  # "direct" | "reverse"
    #: Input span that drives the output through its full range.
    throttling_range: float = 10.0
    output_low: float = 0.0
    output_high: float = 100.0
    integral: bool = True
    sample_seconds: int = 10
    bias: float = None
    description: str = ""


@dataclass
class Rule:
    """A free-form ``when ... then ...`` rule.

    The escape hatch for logic a decision table cannot express. Actions are
    ``(point, action)`` pairs using the same vocabulary as table cells.
    """

    when: Condition = None
    then: list = field(default_factory=list)
    otherwise: list = field(default_factory=list)
    description: str = ""


@dataclass
class DecisionTable:
    """Equipment against modes.

    ``cells`` maps a point name to a mapping of mode name to action. A missing
    cell means no command is issued for that point in that mode.
    """

    cells: dict = field(default_factory=dict)

    def outputs(self) -> list:
        return list(self.cells)

    def action(self, point: str, mode: str):
        return self.cells.get(point, {}).get(mode)

    def set(self, point: str, mode: str, action: str) -> None:
        self.cells.setdefault(point, {})[mode] = action


@dataclass
class Sequence:
    """A complete sequence document."""

    name: str = "New sequence"
    equipment: str = ""
    author: str = ""
    organization: str = ""
    description: str = ""
    version: str = "1.0"

    points: list = field(default_factory=list)
    modes: list = field(default_factory=list)
    interlocks: list = field(default_factory=list)
    table: DecisionTable = field(default_factory=DecisionTable)
    resets: list = field(default_factory=list)
    loops: list = field(default_factory=list)
    rules: list = field(default_factory=list)

    #: Line numbering for the compiled program.
    start_line: int = 10
    line_step: int = 10

    # -- lookup helpers ----------------------------------------------------

    def point(self, name: str):
        key = name.upper()
        for p in self.points:
            if p.name.upper() == key:
                return p
        return None

    def mode(self, name: str):
        key = name.upper()
        for m in self.modes:
            if m.name.upper() == key:
                return m
        return None

    def mode_names(self) -> list:
        return [m.name for m in self.modes]

    def loop_for(self, output: str):
        key = output.upper()
        for loop in self.loops:
            if loop.output.upper() == key:
                return loop
        return None

    def reset_for(self, output: str):
        key = output.upper()
        for reset in self.resets:
            if reset.output.upper() == key:
                return reset
        return None

    def referenced_points(self):
        """Every point name the document mentions, in no particular order."""
        seen = []

        def add(name):
            if name and name not in seen:
                seen.append(name)

        for p in self.points:
            add(p.name)
        for m in self.modes:
            for name in m.when.points():
                add(name)
        for il in self.interlocks:
            for cond in (il.trip, il.reset):
                if cond is not None:
                    for name in cond.points():
                        add(name)
            for point, _action in il.forces:
                add(point)
        for point in self.table.outputs():
            add(point)
        for r in self.resets:
            add(r.output)
            add(r.source)
        for loop in self.loops:
            add(loop.process)
            add(loop.output)
            add(loop.setpoint)
        for rule in self.rules:
            if rule.when is not None:
                for name in rule.when.points():
                    add(name)
            for point, _action in list(rule.then) + list(rule.otherwise):
                add(point)
        return seen


# --------------------------------------------------------------------------
# JSON round trip
# --------------------------------------------------------------------------

_CONDITION_TYPES = {
    "compare": Compare,
    "between": Between,
    "all": All,
    "any": Any,
    "not": Not,
    "always": Always,
}


def condition_to_dict(cond):
    if cond is None:
        return None
    if isinstance(cond, Compare):
        return {"type": "compare", "left": cond.left, "op": cond.op,
                "right": cond.right}
    if isinstance(cond, Between):
        return {"type": "between", "left": cond.left, "low": cond.low,
                "high": cond.high}
    if isinstance(cond, All):
        return {"type": "all", "parts": [condition_to_dict(p) for p in cond.parts]}
    if isinstance(cond, Any):
        return {"type": "any", "parts": [condition_to_dict(p) for p in cond.parts]}
    if isinstance(cond, Not):
        return {"type": "not", "part": condition_to_dict(cond.part)}
    return {"type": "always"}


def condition_from_dict(data):
    if data is None:
        return None
    kind = data.get("type", "always")
    if kind == "compare":
        return Compare(data["left"], data["op"], data["right"])
    if kind == "between":
        return Between(data["left"], data["low"], data["high"])
    if kind == "all":
        return All([condition_from_dict(p) for p in data.get("parts", [])])
    if kind == "any":
        return Any([condition_from_dict(p) for p in data.get("parts", [])])
    if kind == "not":
        return Not(condition_from_dict(data.get("part")))
    return Always()


def to_dict(seq: Sequence) -> dict:
    return {
        "name": seq.name,
        "equipment": seq.equipment,
        "author": seq.author,
        "organization": seq.organization,
        "description": seq.description,
        "version": seq.version,
        "start_line": seq.start_line,
        "line_step": seq.line_step,
        "points": [asdict(p) for p in seq.points],
        "modes": [
            {"name": m.name, "when": condition_to_dict(m.when),
             "description": m.description}
            for m in seq.modes
        ],
        "interlocks": [
            {
                "name": il.name,
                "trip": condition_to_dict(il.trip),
                "reset": condition_to_dict(il.reset),
                "priority": il.priority,
                "forces": [list(f) for f in il.forces],
                "description": il.description,
            }
            for il in seq.interlocks
        ],
        "table": {"cells": seq.table.cells},
        "resets": [asdict(r) for r in seq.resets],
        "loops": [asdict(loop) for loop in seq.loops],
        "rules": [
            {
                "when": condition_to_dict(r.when),
                "then": [list(a) for a in r.then],
                "otherwise": [list(a) for a in r.otherwise],
                "description": r.description,
            }
            for r in seq.rules
        ],
    }


def from_dict(data: dict) -> Sequence:
    seq = Sequence(
        name=data.get("name", "New sequence"),
        equipment=data.get("equipment", ""),
        author=data.get("author", ""),
        organization=data.get("organization", ""),
        description=data.get("description", ""),
        version=data.get("version", "1.0"),
        start_line=int(data.get("start_line", 10)),
        line_step=int(data.get("line_step", 10)),
    )
    seq.points = [Point(**p) for p in data.get("points", [])]
    seq.modes = [
        Mode(m["name"], condition_from_dict(m.get("when")), m.get("description", ""))
        for m in data.get("modes", [])
    ]
    seq.interlocks = [
        Interlock(
            name=il["name"],
            trip=condition_from_dict(il.get("trip")),
            reset=condition_from_dict(il.get("reset")),
            priority=il.get("priority", "@EMER"),
            forces=[tuple(f) for f in il.get("forces", [])],
            description=il.get("description", ""),
        )
        for il in data.get("interlocks", [])
    ]
    seq.table = DecisionTable(cells=data.get("table", {}).get("cells", {}))
    seq.resets = [Reset(**r) for r in data.get("resets", [])]
    seq.loops = [Loop(**loop) for loop in data.get("loops", [])]
    seq.rules = [
        Rule(
            when=condition_from_dict(r.get("when")),
            then=[tuple(a) for a in r.get("then", [])],
            otherwise=[tuple(a) for a in r.get("otherwise", [])],
            description=r.get("description", ""),
        )
        for r in data.get("rules", [])
    ]
    return seq


def dumps(seq: Sequence) -> str:
    return json.dumps(to_dict(seq), indent=2)


def loads(text: str) -> Sequence:
    return from_dict(json.loads(text))
