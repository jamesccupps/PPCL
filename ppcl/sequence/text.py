"""A readable text form of a sequence document.

This is the third rendering of the same model, alongside the decision-table
grid and the compiled PPCL. It exists so a sequence can be diffed, reviewed in
a pull request, pasted into an email, and edited by someone who would rather
type than click.

    sequence "AHU-1 single zone"
      equipment AHU1
      author James Cupps

    points
      SFAN  digital output
      MAT   analog input   mixed air temperature

    modes
      Occupied    when TIME between 6:00 and 18:00
      Unoccupied  otherwise

    table
                  Occupied   Unoccupied
      SFAN        on         off
      OADPR       20         closed
      HVLV        modulate   closed

    interlock Freeze
      trip when MAT < 38
      reset when MAT > 45
      force SFAN off at emergency

The parser is indentation-based and deliberately forgiving about whitespace,
because the table is meant to be lined up by hand.
"""

from __future__ import annotations

import re

from .model import (
    All,
    Always,
    Any,
    Between,
    Compare,
    DecisionTable,
    Interlock,
    Loop,
    Mode,
    Point,
    Reset,
    Rule,
    Sequence,
)


class SequenceSyntaxError(Exception):
    """Raised when the text form cannot be parsed."""

    def __init__(self, message: str, line_no: int = 0, line: str = ""):
        super().__init__(message)
        self.message = message
        self.line_no = line_no
        self.line = line

    def __str__(self) -> str:
        if self.line_no:
            return "line %d: %s\n    %s" % (self.line_no, self.message, self.line)
        return self.message


# --------------------------------------------------------------------------
# Conditions
# --------------------------------------------------------------------------

_COMPARE_RE = re.compile(
    r"^(?P<left>.+?)\s*(?P<op><=|>=|!=|<>|==|<|>|=|\bis\s+not\b|\bis\b)\s*"
    r"(?P<right>.+)$",
    re.IGNORECASE,
)
_BETWEEN_RE = re.compile(
    r"^(?P<left>.+?)\s+between\s+(?P<low>.+?)\s+and\s+(?P<high>.+)$",
    re.IGNORECASE,
)


def parse_condition(text: str, line_no: int = 0):
    """Parse a condition such as ``MAT < 38 and SFAN is on``."""
    text = text.strip()
    if not text:
        raise SequenceSyntaxError("empty condition", line_no, text)
    if text.lower() in ("always", "otherwise", "else"):
        return Always()

    # "or" binds loosest, then "and". Split outside parentheses only.
    for word, node in (("or", Any), ("and", All)):
        parts = _split_keyword(text, word)
        if len(parts) > 1:
            return node([parse_condition(p, line_no) for p in parts])

    if text.startswith("(") and text.endswith(")") and _balanced(text[1:-1]):
        return parse_condition(text[1:-1], line_no)

    m = _BETWEEN_RE.match(text)
    if m:
        return Between(
            m.group("left").strip(),
            _literal(m.group("low")),
            _literal(m.group("high")),
        )

    m = _COMPARE_RE.match(text)
    if m:
        op = " ".join(m.group("op").lower().split())
        return Compare(
            m.group("left").strip(), op, _literal(m.group("right"))
        )

    raise SequenceSyntaxError(
        "cannot read this as a condition; expected something like "
        "'MAT < 38' or 'TIME between 6:00 and 18:00'",
        line_no,
        text,
    )


def _split_keyword(text: str, word: str):
    """Split on a bare keyword at paren depth zero.

    ``between LOW and HIGH`` owns its own ``and``, so when splitting on "and"
    each pending ``between`` swallows the next one. Without this,
    ``TIME between 6:00 and 18:00`` would split down the middle.
    """
    parts = []
    depth = 0
    start = 0
    i = 0
    pending_between = 0
    lowered = text.lower()
    target = word.lower()

    def word_at(pos, needle):
        if not lowered.startswith(needle, pos):
            return False
        before_ok = pos == 0 or not (text[pos - 1].isalnum() or text[pos - 1] == "_")
        after = pos + len(needle)
        after_ok = after >= len(text) or not (
            text[after].isalnum() or text[after] == "_"
        )
        return before_ok and after_ok

    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and word_at(i, "between"):
            pending_between += 1
            i += len("between")
            continue
        elif depth == 0 and word_at(i, target):
            if target == "and" and pending_between:
                pending_between -= 1
                i += len(target)
                continue
            parts.append(text[start:i].strip())
            start = i + len(target)
            i = start
            continue
        i += 1
    parts.append(text[start:].strip())
    return [p for p in parts if p]


def _balanced(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _literal(text: str):
    text = text.strip().strip('"')
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        return text


def render_condition(cond) -> str:
    """Render a condition back to the text form."""
    if cond is None or isinstance(cond, Always):
        return "otherwise"
    if isinstance(cond, Compare):
        return "%s %s %s" % (cond.left, cond.op, _render_literal(cond.right))
    if isinstance(cond, Between):
        return "%s between %s and %s" % (
            cond.left, _render_literal(cond.low), _render_literal(cond.high)
        )
    if isinstance(cond, All):
        return " and ".join(_maybe_paren(p) for p in cond.parts)
    if isinstance(cond, Any):
        return " or ".join(_maybe_paren(p) for p in cond.parts)
    return str(cond)


def _maybe_paren(part) -> str:
    text = render_condition(part)
    return "(%s)" % text if isinstance(part, (All, Any)) else text


def _render_literal(value) -> str:
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def parse(text: str) -> Sequence:
    """Parse the text form into a :class:`Sequence`."""
    seq = Sequence()
    lines = [
        (i + 1, raw.rstrip())
        for i, raw in enumerate(text.splitlines())
    ]
    lines = [
        (n, raw) for n, raw in lines
        if raw.strip() and not raw.lstrip().startswith("#")
    ]

    i = 0
    seen_section = False
    while i < len(lines):
        line_no, raw = lines[i]
        stripped = raw.strip()
        head = stripped.split()[0].lower()

        if head == "sequence":
            seq.name = _after(stripped, "sequence").strip().strip('"')
            i += 1
            i = _parse_metadata(seq, lines, i)
            seen_section = True
            continue
        if head == "points":
            i = _parse_points(seq, lines, i + 1)
            seen_section = True
            continue
        if head == "modes":
            i = _parse_modes(seq, lines, i + 1)
            seen_section = True
            continue
        if head == "table":
            i = _parse_table(seq, lines, i + 1)
            seen_section = True
            continue
        if head == "interlock":
            i = _parse_interlock(seq, lines, i)
            seen_section = True
            continue
        if head == "reset":
            i = _parse_reset(seq, lines, i)
            seen_section = True
            continue
        if head == "loop":
            i = _parse_loop(seq, lines, i)
            seen_section = True
            continue
        if head in ("when", "rule"):
            i = _parse_rule(seq, lines, i)
            seen_section = True
            continue

        raise SequenceSyntaxError(
            "unexpected %r; expected one of sequence, points, modes, table, "
            "interlock, reset, loop, when" % stripped.split()[0],
            line_no,
            raw,
        )

    if not seen_section:
        raise SequenceSyntaxError("the document is empty")
    return seq


def _after(text: str, word: str) -> str:
    return text[len(word):].strip()


def _block(lines, start, base_indent):
    """Collect the indented block beginning at ``start``."""
    out = []
    i = start
    while i < len(lines):
        line_no, raw = lines[i]
        if _indent(raw) <= base_indent:
            break
        out.append((line_no, raw))
        i += 1
    return out, i


def _parse_metadata(seq, lines, i):
    if i >= len(lines):
        return i
    base = 0
    block, i = _block(lines, i, base)
    for line_no, raw in block:
        stripped = raw.strip()
        key, _, value = stripped.partition(" ")
        key = key.lower()
        value = value.strip()
        if key == "equipment":
            seq.equipment = value
        elif key == "author":
            seq.author = value
        elif key in ("organization", "organisation", "org"):
            seq.organization = value
        elif key == "version":
            seq.version = value
        elif key == "description":
            seq.description = value.strip('"')
        else:
            raise SequenceSyntaxError(
                "unknown sequence property %r" % key, line_no, raw
            )
    return i


def _parse_points(seq, lines, i):
    block, i = _block(lines, i, _indent(lines[i - 1][1]))
    for line_no, raw in block:
        parts = raw.split()
        if len(parts) < 2:
            raise SequenceSyntaxError(
                "a point needs at least a name and a kind, e.g. "
                "'SFAN digital output'",
                line_no,
                raw,
            )
        name = parts[0]
        kind = parts[1].lower()
        if kind not in ("analog", "digital"):
            raise SequenceSyntaxError(
                "point kind must be 'analog' or 'digital', not %r" % parts[1],
                line_no,
                raw,
            )
        role = "output"
        rest = parts[2:]
        if rest and rest[0].lower() in ("input", "output", "virtual", "local"):
            role = rest[0].lower()
            rest = rest[1:]
        seq.points.append(
            Point(name=name, kind=kind, role=role, description=" ".join(rest))
        )
    return i


def _parse_modes(seq, lines, i):
    block, i = _block(lines, i, _indent(lines[i - 1][1]))
    for line_no, raw in block:
        stripped = raw.strip()
        m = re.match(r"^(?P<name>\S+)\s+(?:when\s+)?(?P<cond>.+)$", stripped)
        if not m:
            raise SequenceSyntaxError(
                "a mode needs a name and a condition, e.g. "
                "'Occupied when TIME between 6:00 and 18:00'",
                line_no,
                raw,
            )
        seq.modes.append(
            Mode(m.group("name"), parse_condition(m.group("cond"), line_no))
        )
    return i


def _parse_table(seq, lines, i):
    block, i = _block(lines, i, _indent(lines[i - 1][1]))
    if not block:
        raise SequenceSyntaxError("the table has no rows")
    header_no, header = block[0]
    mode_names = header.split()
    unknown = [m for m in mode_names if seq.mode(m) is None]
    if unknown and seq.modes:
        raise SequenceSyntaxError(
            "the table header names %s, which %s not declared in 'modes'"
            % (", ".join(unknown), "is" if len(unknown) == 1 else "are"),
            header_no,
            header,
        )
    for line_no, raw in block[1:]:
        cells = raw.split()
        point = cells[0]
        actions = cells[1:]
        if len(actions) != len(mode_names):
            raise SequenceSyntaxError(
                "row %r has %d cells but the table has %d modes (%s)"
                % (point, len(actions), len(mode_names), ", ".join(mode_names)),
                line_no,
                raw,
            )
        for mode_name, action in zip(mode_names, actions):
            if action == "-":
                continue
            seq.table.set(point, mode_name, action)
    return i


def _parse_interlock(seq, lines, i):
    line_no, raw = lines[i]
    name = _after(raw.strip(), "interlock").strip()
    if not name:
        raise SequenceSyntaxError("interlock needs a name", line_no, raw)
    il = Interlock(name=name)
    block, i = _block(lines, i + 1, _indent(raw))
    for bl_no, bl_raw in block:
        stripped = bl_raw.strip()
        low = stripped.lower()
        if low.startswith("trip when "):
            il.trip = parse_condition(stripped[len("trip when "):], bl_no)
        elif low.startswith("reset when "):
            il.reset = parse_condition(stripped[len("reset when "):], bl_no)
        elif low.startswith("force ") or low.startswith("then force "):
            body = stripped[stripped.lower().index("force ") + 6:]
            priority = "@EMER"
            m = re.search(r"\s+at\s+(\S+)\s*$", body, re.IGNORECASE)
            if m:
                priority = "@" + m.group(1).upper().lstrip("@")
                body = body[: m.start()]
            parts = body.split()
            if len(parts) < 2:
                raise SequenceSyntaxError(
                    "force needs a point and an action, e.g. 'force SFAN off'",
                    bl_no,
                    bl_raw,
                )
            il.priority = priority
            il.forces.append((parts[0], " ".join(parts[1:])))
        elif low.startswith("description "):
            il.description = stripped[len("description "):].strip('"')
        else:
            raise SequenceSyntaxError(
                "expected 'trip when', 'reset when', 'force' or 'description'",
                bl_no,
                bl_raw,
            )
    seq.interlocks.append(il)
    return i


def _parse_reset(seq, lines, i):
    line_no, raw = lines[i]
    m = re.match(
        r"^reset\s+(?P<out>\S+)\s+from\s+(?P<src>\S+)\s*$",
        raw.strip(),
        re.IGNORECASE,
    )
    if not m:
        raise SequenceSyntaxError(
            "expected 'reset OUTPUT from SOURCE'", line_no, raw
        )
    reset = Reset(output=m.group("out"), source=m.group("src"))
    block, i = _block(lines, i + 1, _indent(raw))
    for bl_no, bl_raw in block:
        stripped = bl_raw.strip()
        if "->" not in stripped:
            raise SequenceSyntaxError(
                "expected a breakpoint like '0 -> 180'", bl_no, bl_raw
            )
        x, _, y = stripped.partition("->")
        try:
            reset.points.append((float(x.strip()), float(y.strip())))
        except ValueError:
            raise SequenceSyntaxError(
                "breakpoints must be numbers", bl_no, bl_raw
            )
    seq.resets.append(reset)
    return i


def _parse_loop(seq, lines, i):
    line_no, raw = lines[i]
    name = _after(raw.strip(), "loop").strip()
    if not name:
        raise SequenceSyntaxError("loop needs a name", line_no, raw)
    loop = Loop(name=name, process=name, output="", setpoint="")
    block, i = _block(lines, i + 1, _indent(raw))
    for bl_no, bl_raw in block:
        stripped = bl_raw.strip()
        key, _, value = stripped.partition(" ")
        key = key.lower()
        value = value.strip()
        if key == "measure" or key == "process":
            loop.process = value
        elif key == "output":
            loop.output = value
        elif key == "setpoint":
            loop.setpoint = value
        elif key == "acting":
            loop.action = value.lower()
        elif key == "throttling":
            loop.throttling_range = float(value.replace("range", "").strip())
        elif key == "range":
            low, _, high = value.partition("to")
            loop.output_low = float(low.strip())
            loop.output_high = float(high.strip())
        elif key == "integral":
            loop.integral = value.lower() not in ("off", "no", "none", "false")
        elif key == "sample":
            loop.sample_seconds = int(float(value.replace("seconds", "").strip()))
        elif key == "bias":
            loop.bias = float(value)
        else:
            raise SequenceSyntaxError(
                "unknown loop property %r" % key, bl_no, bl_raw
            )
    if not loop.output or not loop.setpoint:
        raise SequenceSyntaxError(
            "loop %r needs at least 'output' and 'setpoint'" % name, line_no, raw
        )
    seq.loops.append(loop)
    return i


def _parse_rule(seq, lines, i):
    line_no, raw = lines[i]
    stripped = raw.strip()
    body = stripped[5:] if stripped.lower().startswith("when ") else stripped
    if stripped.lower().startswith("rule "):
        body = stripped[5:]
    rule = Rule(when=parse_condition(body, line_no))
    block, i = _block(lines, i + 1, _indent(raw))
    target = rule.then
    for bl_no, bl_raw in block:
        s = bl_raw.strip()
        low = s.lower()
        if low in ("otherwise", "else", "otherwise:", "else:"):
            target = rule.otherwise
            continue
        if low.startswith("then "):
            s = s[5:]
        parts = s.split()
        if len(parts) < 2:
            raise SequenceSyntaxError(
                "an action needs a point and an action, e.g. 'SFAN off'",
                bl_no,
                bl_raw,
            )
        target.append((parts[0], " ".join(parts[1:])))
    if not rule.then and not rule.otherwise:
        raise SequenceSyntaxError("this rule has no actions", line_no, raw)
    seq.rules.append(rule)
    return i


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render(seq: Sequence) -> str:
    """Render a sequence back to the text form."""
    out = ['sequence "%s"' % seq.name]
    for key, value in (
        ("equipment", seq.equipment),
        ("author", seq.author),
        ("organization", seq.organization),
        ("version", seq.version),
    ):
        if value:
            out.append("  %s %s" % (key, value))
    if seq.description:
        out.append('  description "%s"' % seq.description.replace("\n", " "))

    if seq.points:
        out.append("")
        out.append("points")
        width = max(len(p.name) for p in seq.points)
        for p in seq.points:
            row = "  %-*s %-7s %s" % (width, p.name, p.kind, p.role)
            if p.description:
                row += "   " + p.description
            out.append(row.rstrip())

    if seq.modes:
        out.append("")
        out.append("modes")
        width = max(len(m.name) for m in seq.modes)
        for m in seq.modes:
            cond = render_condition(m.when)
            prefix = "" if cond == "otherwise" else "when "
            out.append("  %-*s %s%s" % (width, m.name, prefix, cond))

    if seq.table.cells and seq.modes:
        out.append("")
        out.append("table")
        modes = seq.mode_names()
        rows = [(p, [seq.table.action(p, m) or "-" for m in modes])
                for p in seq.table.outputs()]
        name_w = max([len(p) for p, _ in rows] + [0])
        widths = [
            max([len(modes[i])] + [len(r[1][i]) for r in rows])
            for i in range(len(modes))
        ]
        header = "  %-*s " % (name_w, "")
        header += " ".join("%-*s" % (widths[i], modes[i]) for i in range(len(modes)))
        out.append(header.rstrip())
        for point, actions in rows:
            row = "  %-*s " % (name_w, point)
            row += " ".join(
                "%-*s" % (widths[i], actions[i]) for i in range(len(modes))
            )
            out.append(row.rstrip())

    for il in seq.interlocks:
        out.append("")
        out.append("interlock %s" % il.name)
        if il.description:
            out.append('  description "%s"' % il.description)
        if il.trip is not None:
            out.append("  trip when %s" % render_condition(il.trip))
        if il.reset is not None:
            out.append("  reset when %s" % render_condition(il.reset))
        for point, action in il.forces:
            out.append("  force %s %s at %s"
                       % (point, action, il.priority.lstrip("@").lower()))

    for reset in seq.resets:
        out.append("")
        out.append("reset %s from %s" % (reset.output, reset.source))
        for x, y in reset.points:
            out.append("  %s -> %s" % (_render_literal(x), _render_literal(y)))

    for loop in seq.loops:
        out.append("")
        out.append("loop %s" % loop.name)
        out.append("  measure %s" % loop.process)
        out.append("  output %s" % loop.output)
        out.append("  setpoint %s" % loop.setpoint)
        out.append("  acting %s" % loop.action)
        out.append("  throttling %s" % _render_literal(loop.throttling_range))
        out.append("  range %s to %s"
                   % (_render_literal(loop.output_low),
                      _render_literal(loop.output_high)))
        if not loop.integral:
            out.append("  integral off")

    for rule in seq.rules:
        out.append("")
        out.append("when %s" % render_condition(rule.when))
        for point, action in rule.then:
            out.append("  %s %s" % (point, action))
        if rule.otherwise:
            out.append("  otherwise")
            for point, action in rule.otherwise:
                out.append("  %s %s" % (point, action))

    return "\n".join(out) + "\n"
