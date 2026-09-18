"""Read a field panel's PPCL DISPLAY REPORT.

A program exported as plain text is only the source. The panel knows four more
things about every line, and they are the things that explain why a program
that looks right is not doing anything:

* whether the line is **enabled** -- a disabled line is not evaluated at all,
  and nothing in the exported text records that;
* whether the panel has ever **executed** it since the trace bits were cleared;
* whether any point on it is **unresolved** -- named in the program but absent
  from the database, so the line cannot run;
* whether the panel **tried and failed** to execute it.

Those live in the report's state column::

    State  Line  Statement
    ---------------------------------------------------------------
    ET     100   IF(SECND4 .LT. 7) THEN GOTO 300
    D      200   SECND4 = 0
    ETU    300   IF("MISSING" .GT. 0) THEN ON("greenlight") ELSE
                 OFF("greenlight")

Columns, from the BACnet ALN Field Panel User's Manual (125-3020), "PPCL
Status Indicator Descriptions":

===  =======  =========================================================
Col  Char     Meaning
===  =======  =========================================================
1    ``E``    line is enabled
1    ``D``    line is disabled and will not execute
2    ``T``    executed at least once since the trace bits were cleared
3    ``U``    a point on this line is not in the system point database
4    ``F``    the panel tried to execute this line and failed
5    ``L``    the line is being tested by loop tuning
===  =======  =========================================================

This module is read-only and offline: it parses a report someone has already
pulled off a panel or out of a supervisor. Nothing here talks to a panel.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: The state column, then the line number, then the statement. The state may
#: be absent entirely -- some reports show only the enable flag -- so it is
#: matched loosely and validated afterwards.
_ROW = re.compile(r"^(?P<state>[EDTUFL ]{0,6}?)\s*(?P<line>\d{1,5})\s\s(?P<body>.*)$")

_HEADER = re.compile(r"^\s*State\s+Line\s+Statement\s*$", re.I)
_RULE = re.compile(r"^[-=\\ ]{10,}$")
_END = re.compile(r"^\s*End of report\s*$", re.I)
_PROGRAM = re.compile(r"PPCL program\s*<([^>]*)>", re.I)
_PANEL = re.compile(r"Field panel\s*<([^>]*)>", re.I)
_TITLE = re.compile(r"PPCL\s+DISPLAY\s+REPORT", re.I)


@dataclass
class LineState:
    """What the panel knows about one program line."""

    number: int
    enabled: bool = True
    traced: bool = False
    unresolved: bool = False
    failed: bool = False
    loop_tested: bool = False
    #: False when the state column carried neither ``E`` nor ``D``. The panel
    #: always prints one: "Either an E or a D will always be displayed" --
    #: Insight MMI Database Transfer help, UC PPCL Window. So a row without
    #: one is damaged input, and ``enabled`` below is this module's default
    #: rather than anything the panel said. Reported as R705, because the
    #: whole point of reading a report is to learn which lines are disabled
    #: and silently answering "enabled" is the wrong way to be wrong.
    state_known: bool = True
    #: The raw state characters, kept so an unrecognised flag is not lost.
    raw: str = ""

    @property
    def flags(self) -> str:
        """A short human summary, e.g. ``disabled, unresolved``."""
        out = []
        if not self.enabled:
            out.append("disabled")
        if self.unresolved:
            out.append("unresolved")
        if self.failed:
            out.append("failed")
        if not self.traced and self.enabled:
            out.append("never executed")
        if self.loop_tested:
            out.append("loop tuning")
        return ", ".join(out) or "ok"


@dataclass
class PanelReport:
    """A parsed PPCL DISPLAY REPORT."""

    program: str = ""
    panel: str = ""
    states: dict = field(default_factory=dict)   # line number -> LineState
    #: The program as plain PPCL, ready for ``parser.parse``.
    text: str = ""
    #: Lines that looked like rows but could not be read, as (index, text).
    skipped: list = field(default_factory=list)

    # -- convenience -------------------------------------------------------

    @property
    def disabled(self) -> set:
        return {n for n, s in self.states.items() if not s.enabled}

    @property
    def unresolved(self) -> set:
        return {n for n, s in self.states.items() if s.unresolved}

    @property
    def failed(self) -> set:
        return {n for n, s in self.states.items() if s.failed}

    @property
    def never_executed(self) -> set:
        """Enabled lines the panel has not run since the trace bits cleared.

        Only meaningful if the report carries trace bits at all; a report with
        no ``T`` anywhere was most likely taken after a clear, or from a
        supervisor that does not show them.
        """
        if not any(s.traced for s in self.states.values()):
            return set()
        return {n for n, s in self.states.items() if s.enabled and not s.traced}

    def summary(self) -> str:
        n = len(self.states)
        return (
            "%d line(s): %d disabled, %d with an unresolved point, %d failed, "
            "%d never executed"
            % (n, len(self.disabled), len(self.unresolved), len(self.failed),
               len(self.never_executed))
        )


def looks_like_report(text: str) -> bool:
    """True if this is a PPCL DISPLAY REPORT rather than a program file."""
    head = "\n".join(text.splitlines()[:40])
    return bool(_TITLE.search(head)) or bool(_HEADER.search(head))


def _apply_state(state: str, st: LineState) -> None:
    st.raw = state
    letters = set(state.strip())
    if "D" in letters:
        st.enabled = False
    elif "E" in letters:
        st.enabled = True
    else:
        st.state_known = False
    st.traced = "T" in letters
    st.unresolved = "U" in letters
    st.failed = "F" in letters
    st.loop_tested = "L" in letters


def parse(text: str) -> PanelReport:
    """Parse report ``text``. Never raises on malformed input."""
    report = PanelReport()
    body_lines = []
    in_body = False
    last_number = None

    for index, raw in enumerate(text.splitlines()):
        line = raw.rstrip()
        if not in_body:
            m = _PROGRAM.search(line)
            if m:
                report.program = m.group(1).strip()
            m = _PANEL.search(line)
            if m:
                report.panel = m.group(1).strip()
            if _HEADER.match(line):
                in_body = True
            continue

        if _RULE.match(line) or not line.strip():
            continue
        if _END.match(line):
            break

        m = _ROW.match(line)
        if m:
            state = m.group("state")
            number = int(m.group("line"))
            body = m.group("body").rstrip()
            st = LineState(number=number)
            _apply_state(state, st)
            report.states[number] = st
            body_lines.append("%05d\t%s" % (number, body.strip()))
            last_number = number
            continue

        # A statement wrapped onto the next line: no state, no line number.
        if last_number is not None and line.startswith(" "):
            body_lines[-1] += " " + line.strip()
            continue

        report.skipped.append((index + 1, line))

    report.text = "\n".join(body_lines) + ("\n" if body_lines else "")
    return report


def parse_file(path) -> PanelReport:
    import pathlib

    return parse(pathlib.Path(path).read_text(encoding="utf-8", errors="replace"))


def apply_to(report: PanelReport, program) -> int:
    """Mark ``program``'s lines disabled where the report says the panel is
    not evaluating them. Returns the number of lines marked.

    This is what makes a report worth having. Flow analysis over a program
    whose disabled lines are treated as live gives the wrong main loop, the
    wrong reachable set, and a page of findings about statements that are not
    running. Line numbers are matched exactly; a report of a different program
    marks nothing rather than guessing.
    """
    marked = 0
    for line in program.lines:
        state = report.states.get(line.number)
        if state is not None and not state.enabled and not line.disabled:
            line.disabled = True
            marked += 1
    return marked


#: Codes the report contributes. They are deliberately in their own R7xx range
#: rather than the Wxxx rule space, because they are not findings derived from
#: the source -- they are what the panel itself reported.
REPORT_CODES = {
    "R701": "Point unresolved, reported by the panel",
    "R702": "Line failed to execute, reported by the panel",
    "R703": "Line has never executed since the trace bits were cleared",
    "R704": "Line is disabled in the panel",
}


def diagnostics(report: PanelReport, program=None):
    """Turn the panel's own state column into diagnostics.

    These carry more weight than anything the linter derives, because they are
    observations from the running system rather than inferences from text.
    """
    from .diagnostics import Diagnostic, Severity

    out = []
    source_of = {}
    if program is not None:
        for line in program.lines:
            source_of[line.number] = line.source_line

    def add(code, severity, number, message, **kw):
        out.append(Diagnostic(
            code=code, severity=severity, message=message, line=number,
            source_line=source_of.get(number), **kw))

    for number in sorted(report.unresolved):
        add("R701", Severity.ERROR, number,
            "the panel reports an unresolved point on this line",
            detail="At least one point named here is not in the system point "
            "database, or the field panel has not been made ready. The line "
            "cannot do what it says until the name resolves.",
            suggestion="Check the spelling against the point database, and "
            "check the point actually exists on this panel -- a point in "
            "another panel resolves only if the system has discovered it.",
            manual="BACnet ALN Field Panel User's Manual 125-3020, PPCL "
                   "Status Indicator Descriptions")

    for number in sorted(report.failed):
        add("R702", Severity.ERROR, number,
            "the panel tried to execute this line and failed",
            detail="This is not a compile error -- the line loaded. Something "
            "about executing it went wrong on the panel.",
            manual="BACnet ALN Field Panel User's Manual 125-3020, PPCL "
                   "Status Indicator Descriptions")

    for number in sorted(report.disabled):
        add("R704", Severity.INFO, number,
            "this line is disabled in the panel and is not being evaluated",
            detail="Disabling is not visible in exported program text, so "
            "without this report the line would be analysed as if it were "
            "running.",
            manual="BACnet ALN Field Panel User's Manual 125-3020, PPCL "
                   "Status Indicator Descriptions")

    for number, st in sorted(report.states.items()):
        if st.state_known:
            continue
        add("R705", Severity.WARNING, number,
            "the report row for this line carries neither E nor D",
            detail="The panel always prints one -- \"Either an E or a D will "
            "always be displayed\" -- so this row did not survive whatever "
            "copied it here intact. The line is being treated as enabled "
            "because that is this tool's default, not because the panel said "
            "so. Anything derived from it about disabled lines is unsound.",
            manual="Insight MMI Database Transfer help, UC PPCL Window",
            suggestion="Re-capture the report. A column lost to text wrapping "
            "or a trimmed leading space is the usual cause.")

    for number in sorted(report.never_executed):
        add("R703", Severity.WARNING, number,
            "the panel has never executed this line",
            detail="The trace bit is clear while other lines in the same "
            "report have theirs set, so the panel has been running and has "
            "not reached this line. That is evidence of dead code, rather "
            "than an inference from the control flow.",
            suggestion="Check the branch that should reach it. If it is "
            "genuinely unreachable, delete it rather than leaving it to "
            "mislead the next person.",
            manual="BACnet ALN Field Panel User's Manual 125-3020, PPCL "
                   "Status Indicator Descriptions")

    return out
