"""Writing PPCL: a line-number-free builder plus sequence templates.

The awkward part of authoring PPCL by hand is that every branch is a raw line
number, so inserting three lines into the middle of a program means fixing
every GOTO after it. :class:`Builder` removes that: you write against symbolic
labels and it assigns line numbers at render time.

The templates produce complete, lintable programs for the sequences that come
up constantly on air handlers and central plant. They are starting points to
edit against a real sequence of operations, not drop-in code -- the point names
are placeholders and the setpoints are conventional defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import spec


class BuildError(Exception):
    """Raised when a program cannot be rendered."""


@dataclass
class _Item:
    kind: str  # "code" | "comment" | "label" | "blank"
    text: str = ""
    label: str = ""
    #: Labels referenced by this line, as (placeholder, label) pairs.
    refs: tuple = ()


class Builder:
    """Assemble a PPCL program using symbolic labels instead of line numbers.

    Example::

        b = Builder()
        b.comment("Main loop")
        b.label("MAIN")
        b.code("IF(TIME.GE.6:00) THEN ON(SFAN) ELSE OFF(SFAN)")
        b.goto("MAIN")
        print(b.render())

    Labels are resolved after all lines are known, so forward and backward
    references both work and inserting a line never breaks a branch.
    """

    def __init__(self, start: int = 10, step: int = 10, pad: int = 5,
                 sep: str = "\t"):
        self.start = start
        self.step = step
        self.pad = pad
        self.sep = sep
        self.items = []

    # -- emitting ----------------------------------------------------------

    def comment(self, text: str = ""):
        for chunk in (text.split("\n") if text else [""]):
            self.items.append(_Item("comment", chunk))
        return self

    def rule(self, title: str = "", width: int = 58):
        """A visual separator comment, optionally carrying a title."""
        if title:
            body = "----- %s " % title
            self.items.append(_Item("comment", body + "-" * max(0, width - len(body))))
        else:
            self.items.append(_Item("comment", "-" * width))
        return self

    def banner(self, title: str, width: int = 58):
        self.items.append(_Item("comment", "=" * width))
        self.items.append(_Item("comment", title))
        self.items.append(_Item("comment", "=" * width))
        return self

    def blank(self):
        self.items.append(_Item("comment", ""))
        return self

    def label(self, name: str):
        """Mark the next emitted line with a symbolic label."""
        self.items.append(_Item("label", label=name))
        return self

    def code(self, text: str, **refs):
        """Emit a statement.

        Any ``{name}`` placeholder in ``text`` is replaced by the line number of
        the label passed as keyword ``name``::

            b.code("GOTO {top}", top="MAIN")
        """
        self.items.append(
            _Item("code", text, refs=tuple(sorted(refs.items())))
        )
        return self

    def goto(self, label: str):
        return self.code("GOTO {target}", target=label)

    def gosub(self, label: str, *args):
        if args:
            return self.code(
                "GOSUB {target} " + ",".join(args), target=label
            )
        return self.code("GOSUB {target}", target=label)

    def returns(self):
        return self.code("RETURN")

    # -- rendering ---------------------------------------------------------

    def _assign_numbers(self):
        """Assign line numbers and bind labels.

        A label binds to the next *executable* line, skipping any comments in
        between. Branching to a comment line works on a panel but the manual
        advises against it, and it makes the program fragile: deleting the
        comment moves where control lands.
        """
        numbers = {}
        pending = []
        n = self.start
        rendered = []
        for item in self.items:
            if item.kind == "label":
                pending.append(item.label)
                continue
            if item.kind == "code":
                for label in pending:
                    if label in numbers:
                        raise BuildError("label %r is defined twice" % label)
                    numbers[label] = n
                pending = []
            rendered.append((n, item))
            n += self.step
            if n > spec.LINE_MAX:
                raise BuildError(
                    "program exceeds the maximum line number %d" % spec.LINE_MAX
                )
        # A label at the very end points one step past the last line.
        for label in pending:
            numbers[label] = n
        return rendered, numbers

    def render(self, firmware: spec.Firmware = spec.Firmware.APOGEE) -> str:
        """Resolve labels and return the finished program text.

        Executable lines that would exceed the MMI character limit are recorded
        on ``self.long_lines`` so a caller can report them; comments are left
        alone because they truncate harmlessly.
        """
        rendered, numbers = self._assign_numbers()
        limit = spec.MMI_LINE_LIMIT[firmware]
        self.long_lines = []
        out = []
        for number, item in rendered:
            if item.kind == "comment":
                body = ("C %s" % item.text) if item.text else "C"
            else:
                body = item.text
                for placeholder, label in item.refs:
                    if label not in numbers:
                        raise BuildError(
                            "line %d references undefined label %r"
                            % (number, label)
                        )
                    body = body.replace("{%s}" % placeholder, str(numbers[label]))
                if "{" in body and "}" in body:
                    raise BuildError(
                        "line %d has an unresolved placeholder: %s" % (number, body)
                    )
            text = "%s%s%s" % (str(number).zfill(self.pad), self.sep, body)
            if item.kind == "code" and len(text) > limit:
                self.long_lines.append((number, len(text)))
            out.append(text)
        return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# Engineering helpers
# --------------------------------------------------------------------------


def proportional_gain(device_range: float, throttling_range: float) -> int:
    """PPCL proportional gain from the manual's formula.

    ``pg = (full range of the controlled device / throttling range) * 1000``
    where the throttling range is the input span that drives the output from
    fully closed to fully open.
    """
    if throttling_range <= 0:
        raise ValueError("throttling range must be positive")
    return int(round((device_range / throttling_range) * 1000))


def integral_gain(pg: int, use_integral: bool = True) -> int:
    """The manual's recommended starting integral gain: 2% of pg."""
    return int(round(pg * 0.02)) if use_integral else 0


def loop_bias(low: float, high: float) -> float:
    """Bias for a proportional-only loop: the midpoint of the output span."""
    return low + (high - low) / 2.0


def duty_cycle_pattern(slots) -> str:
    """Encode a duty-cycle pattern for the DC command.

    ``slots`` is 12 booleans, one per 5-minute interval of the hour, in time
    order. The manual encodes each 15-minute segment as one digit whose bits
    are the three 5-minute slots, and the four digits are read right to left.

    Note on the source: Table 4-1 of the manual is normative here and is what
    this implements (first 5 minutes = 1, second = 2, third = 4). The worked
    DC example a few paragraphs later contradicts that table -- it labels
    OFF/ON/ON as 3 and OFF/OFF/ON as 1, where the table gives 6 and 4. The
    table wins.
    """
    if len(slots) != 12:
        raise ValueError("expected 12 five-minute slots, got %d" % len(slots))
    digits = []
    for seg in range(4):
        a, b, c = slots[seg * 3 : seg * 3 + 3]
        digits.append(str((1 if a else 0) + (2 if b else 0) + (4 if c else 0)))
    return "".join(reversed(digits))


def table_statement(inp: str, out: str, pairs) -> str:
    """Build a TABLE statement, validating the manual's ascending-x rule."""
    if not 1 <= len(pairs) <= 7:
        raise ValueError("TABLE takes 1 to 7 coordinate pairs")
    xs = [p[0] for p in pairs]
    if any(b <= a for a, b in zip(xs, xs[1:])):
        raise ValueError("TABLE x values must be in ascending order: %r" % (xs,))
    body = ",".join("%s,%s" % (_num(x), _num(y)) for x, y in pairs)
    return "TABLE(%s,%s,%s)" % (_q(inp), _q(out), body)


def _num(value) -> str:
    if isinstance(value, int):
        return "%d.0" % value
    return ("%g" % value) if "." in ("%g" % value) else "%g.0" % value


def _q(name: str) -> str:
    """Quote a point name if PPCL requires it."""
    bare = name.lstrip("$")
    needs = len(bare) > spec.UNQUOTED_NAME_MAX or not bare.isalnum()
    return '"%s"' % name if needs and not name.startswith('"') else name


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------


def header(title: str, author: str = "", organization: str = "",
           description: str = "", requires: str = "Nothing",
           version: str = "1.0", equipment: str = "") -> Builder:
    """The documentation block every program should open with."""
    b = Builder()
    b.comment("=" * 58)
    b.comment("Title: %s" % title)
    if organization:
        b.comment("Organization: %s" % organization)
    if author:
        b.comment("Author: %s" % author)
    if equipment:
        b.comment("Equipment: %s" % equipment)
    b.comment("Version: %s" % version)
    b.comment("")
    for line in (description or "Describe WHY this program exists.").split("\n"):
        b.comment("Description: %s" % line if line is description.split("\n")[0]
                  else "  %s" % line)
    b.comment("")
    b.comment("Requires: %s" % requires)
    b.comment("=" * 58)
    return b


def skeleton(title: str, **kw) -> str:
    """A minimal well-formed program: header, init, main loop, subroutines.

    The structure matters: initialisation runs once, the main loop is a
    closed cycle that every time-based command lives inside, and the loop is
    closed by exactly one backward GOTO.
    """
    b = header(title, **kw)
    b.blank()
    b.rule("Declarations and power restart")
    b.comment('LOCAL("FLAG1")')
    b.label("INIT")
    b.code("ONPWRT({init})", init="INIT")
    b.blank()
    b.rule("One-time initialisation")
    b.comment("Runs once at load and after a power failure.")
    b.blank()
    b.rule("Main loop")
    b.label("MAIN")
    b.comment("Everything time-based must live inside this loop.")
    b.gosub("SUB1")
    b.goto("LOOPEND")
    b.blank()
    b.rule("Subroutine: first function")
    b.label("SUB1")
    b.comment("No LOOP / SAMPLE / TOD / WAIT in here.")
    b.returns()
    b.blank()
    b.rule("Close the main loop")
    b.label("LOOPEND")
    b.goto("MAIN")
    return b.render()


def air_handler(name: str = "AHU1", fan: str = "SFAN", ret_fan: str = "",
                mat: str = "MAT", oat: str = "OAT", dat: str = "DAT",
                dasp: str = "DASP", valve: str = "HVLV",
                cool_valve: str = "CVLV", znt: str = "ZNT",
                damper: str = "OADPR",
                occ_start: str = "6:00", occ_stop: str = "18:00",
                freeze_trip: float = 38.0, freeze_reset: float = 45.0,
                zone_heating: float = 68.0, zone_cooling: float = 74.0,
                dat_max: float = 95.0, dat_min: float = 55.0,
                **kw) -> str:
    """A single-zone air handler: schedule, freeze protection, DAT control.

    The freeze interlock latches at emergency priority and, importantly,
    releases itself once the mixed-air temperature recovers. Omitting that
    release is the single most common PPCL field defect.
    """
    b = header(
        "%s supply fan and discharge control" % name,
        description="Occupied/unoccupied fan control with a latching freeze "
                    "interlock and discharge air temperature control.",
        equipment=name,
        **kw
    )
    b.blank()
    b.rule("Declarations and power restart")
    b.code('LOCAL("FRZLAT","OCC","DMD")')
    b.label("INIT")
    b.code("ONPWRT({init})", init="INIT")
    b.blank()

    b.rule("Main loop")
    b.blank()
    b.comment("Occupied schedule")
    b.label("MAIN")
    b.code('"$OCC" = 0.0')
    b.code("IF(TIME.GE.%s.AND.TIME.LT.%s) THEN \"$OCC\" = 1.0"
           % (occ_start, occ_stop))
    fans = _q(fan) + ("," + _q(ret_fan) if ret_fan else "")
    b.code('IF("$OCC".EQ.1.0) THEN ON(%s) ELSE OFF(%s)' % (fans, fans))
    b.blank()

    b.comment("Freeze protection: latch below %g F, reset above %g F"
              % (freeze_trip, freeze_reset))
    b.code('IF(%s.LT.%s) THEN "$FRZLAT" = 1.0' % (_q(mat), _fl(freeze_trip)))
    b.code('IF(%s.GT.%s) THEN "$FRZLAT" = 0.0' % (_q(mat), _fl(freeze_reset)))
    b.comment("Command and release at the SAME priority or the release fails.")
    b.code('IF("$FRZLAT".EQ.1.0) THEN OFF(@EMER,%s)' % _q(fan))
    b.code('IF("$FRZLAT".EQ.0.0) THEN RELEAS(@EMER,%s)' % _q(fan))
    b.blank()

    b.comment("Outside air damper: minimum position when occupied")
    b.code('IF("$OCC".EQ.1.0) THEN SET(20.0,%s)' % _q(damper))
    b.code('IF("$OCC".EQ.0.0) THEN SET(0.0,%s)' % _q(damper))
    b.blank()

    b.comment("Discharge setpoint reset from zone temperature.")
    b.comment("A fixed discharge setpoint cannot heat the space on a cold")
    b.comment("day, so the setpoint has to move with zone demand.")
    b.comment("  zone %.0f F -> discharge %.0f F   (full heating)"
              % (zone_heating, dat_max))
    b.comment("  zone %.0f F -> discharge %.0f F   (full cooling)"
              % (zone_cooling, dat_min))
    b.code(table_statement(znt, dasp,
                           [(zone_heating, dat_max), (zone_cooling, dat_min)]))
    b.blank()

    b.comment("One loop produces a 0-100 heat/cool demand, which is then")
    b.comment("split so the two valves sequence instead of fighting.")
    pg = proportional_gain(100.0, 10.0)
    ig = integral_gain(pg)
    b.code('LOOP(128,%s,"$DMD",%s,%d,%d,0,10,50.0,0.0,100.0,0)'
           % (_q(dat), _q(dasp), pg, ig))
    b.comment("Demand 50-100 opens heating; demand 50-0 opens cooling.")
    b.code(table_statement("$DMD", valve, [(50.0, 0.0), (100.0, 100.0)]))
    b.code(table_statement("$DMD", cool_valve, [(0.0, 100.0), (50.0, 0.0)]))
    b.blank()

    b.comment("Both valves shut with the fan off.")
    b.code("IF(%s.EQ.OFF) THEN SET(0.0,%s)" % (_q(fan), _q(valve)))
    b.code("IF(%s.EQ.OFF) THEN SET(0.0,%s)" % (_q(fan), _q(cool_valve)))
    b.blank()

    b.rule("Close the main loop")
    b.label("LOOPEND")
    b.goto("MAIN")
    return b.render()


def reset_schedule(inp: str = "OAT", out: str = "HWSP",
                   pairs=((0, 180), (60, 100)), title: str = "",
                   **kw) -> str:
    """A TABLE-based reset schedule, e.g. hot water setpoint from outside air."""
    b = header(
        title or "%s reset from %s" % (out, inp),
        description="Linear reset schedule. Below the first x the output holds "
                    "the first y; above the last x it holds the last y.",
        **kw
    )
    b.blank()
    for x, y in pairs:
        b.comment("  %-10s -> %-10s" % ("%g" % x, "%g" % y))
    b.label("MAIN")
    b.code(table_statement(inp, out, pairs))
    b.goto("MAIN")
    return b.render()


def occupancy_schedule(points=("SFAN",), mode_by_day=(1, 1, 1, 1, 1, 8, 8),
                       on_time: str = "6:00", off_time: str = "18:00",
                       holidays=(), title: str = "", **kw) -> str:
    """A TOD schedule with day modes and holidays, in the required order."""
    b = header(
        title or "Occupancy schedule",
        description="Time-of-day scheduling. HOLIDA and TODMOD must precede "
                    "every TOD command, which is why they come first here.",
        **kw
    )
    b.blank()
    b.label("MAIN")
    b.rule("Holidays and day modes must come first")
    for i in range(0, len(holidays), 8):
        chunk = holidays[i : i + 8]
        b.code("HOLIDA(%s)" % ",".join("%d,%d" % (m, d) for m, d in chunk))
    b.code("TODMOD(%s)" % ",".join(str(m) for m in mode_by_day))
    b.blank()
    b.rule("Schedules")
    b.comment("Mode 1 = normal, 2 = extended, 4 = shortened, 8 = weekend, "
              "16 = holiday")
    names = ",".join(_q(p) for p in points)
    b.code("TOD(1,1,%s,%s,%s)" % (on_time, off_time, names))
    off_mode = 8 + (16 if holidays else 0)
    b.comment("Weekend%s: leave equipment off"
              % (" and holiday" if holidays else ""))
    b.code("TOD(%d,1,0:00,0:01,%s)" % (off_mode, names))
    b.blank()
    b.goto("MAIN")
    return b.render()


def lead_lag(pumps=("PMP1", "PMP2"), enable: str = "PMPENA",
             rotate_hours: int = 168, title: str = "", **kw) -> str:
    """Lead/lag rotation for a pair of pumps on a runtime interval."""
    if len(pumps) != 2:
        raise ValueError("this template handles exactly two pumps")
    lead, lag = pumps
    b = header(
        title or "%s / %s lead-lag rotation" % (lead, lag),
        description="Runs one pump, rotates the lead on a fixed interval, and "
                    "starts the standby if the lead fails to prove.",
        **kw
    )
    b.blank()
    b.rule("Declarations and power restart")
    b.code('LOCAL("LEAD","HRS")')
    b.label("INIT")
    b.code("ONPWRT({init})", init="INIT")
    b.blank()

    b.label("MAIN")
    b.rule("Accumulate lead runtime, one hour at a time")
    b.code('SAMPLE(3600) "$HRS" = "$HRS" + 1.0')
    b.code('IF("$HRS".GE.%s) THEN GOTO {rot}' % _fl(rotate_hours), rot="ROTATE")
    b.goto("RUN")
    b.blank()

    b.rule("Rotate the lead")
    b.label("ROTATE")
    b.code('"$HRS" = 0.0')
    b.code('"$LEAD" = 1.0 - "$LEAD"')
    b.blank()

    b.rule("Run the current lead")
    b.label("RUN")
    b.code('IF(%s.EQ.OFF) THEN GOTO {stop}' % _q(enable), stop="STOP")
    b.code('IF("$LEAD".EQ.0.0) THEN ON(%s) ELSE OFF(%s)' % (_q(lead), _q(lead)))
    b.code('IF("$LEAD".EQ.1.0) THEN ON(%s) ELSE OFF(%s)' % (_q(lag), _q(lag)))
    b.goto("LOOPEND")
    b.blank()

    b.label("STOP")
    b.code("OFF(%s,%s)" % (_q(lead), _q(lag)))
    b.blank()
    b.rule("Close the main loop")
    b.label("LOOPEND")
    b.goto("MAIN")
    return b.render()


def _fl(value) -> str:
    """Render a number as a PPCL decimal literal."""
    return "%.1f" % value if float(value) == int(value) else "%g" % value


#: Template name -> callable, for the CLI.
TEMPLATES = {
    "skeleton": skeleton,
    "ahu": air_handler,
    "reset": reset_schedule,
    "schedule": occupancy_schedule,
    "leadlag": lead_lag,
}
