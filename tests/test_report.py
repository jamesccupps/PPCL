"""Reading a field panel's PPCL DISPLAY REPORT.

The report carries four things exported program text does not: whether a line
is enabled, whether the panel has executed it, whether a point on it resolves,
and whether executing it failed. Everything here is checked against the format
printed in the BACnet ALN Field Panel User's Manual (125-3020).
"""

import pytest

from ppcl import analyzer, parser, report
from ppcl.diagnostics import Severity

# The manual's own example, extended with a U, an F and an untraced line so
# every indicator is exercised.
SAMPLE = """\
07/17/2008 THU                  PPCL DISPLAY REPORT                     08:09pm
------------------------------------------------------------------------\\-------
Search for <*> Line numbers <1 to 32767> PPCL program <BLD990.AHU01.TEST>  Field panel  <1>
State  Line  Statement
------------------------------------------------------------------------\\-------
ET     100   IF(SECND4 .LT. 7) THEN GOTO 300
D      200   ON("dead")
ETU    300   IF(SECND4 .GT. 0 .AND. SECND4 .LT. 5) THEN ON("greenlight") ELSE
             OFF("greenlight")
E      400   IF(SECND4 .GE. 5) THEN ON("redlight") ELSE OFF("redlight")
ETF    450   ON("missingpoint")
ET     500   GOTO 100
End of report
"""


def test_the_header_identifies_the_program_and_panel():
    r = report.parse(SAMPLE)
    assert r.program == "BLD990.AHU01.TEST"
    assert r.panel == "1"
    assert r.skipped == []


def test_every_indicator_is_read():
    r = report.parse(SAMPLE)
    assert r.disabled == {200}
    assert r.unresolved == {300}
    assert r.failed == {450}
    assert r.never_executed == {400}
    assert r.states[100].enabled and r.states[100].traced


def test_a_wrapped_statement_is_rejoined():
    r = report.parse(SAMPLE)
    line = [l for l in r.text.splitlines() if l.startswith("00300")][0]
    assert line.endswith('ELSE OFF("greenlight")')


def test_the_reconstructed_program_parses_cleanly():
    r = report.parse(SAMPLE)
    prog = parser.parse(r.text)
    assert len(prog.lines) == 6
    assert not [l for l in prog.lines if type(l.stmt).__name__ == "Unparsed"]


def test_a_report_is_told_apart_from_a_program():
    assert report.looks_like_report(SAMPLE)
    assert not report.looks_like_report("00010\tON(A)\n00020\tGOTO 10\n")


def test_applying_a_report_takes_disabled_lines_out_of_the_flow():
    """The point of the whole exercise.

    Analysing a disabled line as if it were running gives the wrong answer
    about what the panel is doing.
    """
    r = report.parse(SAMPLE)
    prog = parser.parse(r.text)
    before = sum(1 for l in prog.lines if l.is_executable)
    assert report.apply_to(r, prog) == 1
    after = sum(1 for l in prog.lines if l.is_executable)
    assert after == before - 1
    assert prog.by_number()[200].disabled
    assert not prog.by_number()[200].is_executable


def test_applying_a_report_for_a_different_program_marks_nothing():
    r = report.parse(SAMPLE)
    other = parser.parse("00700\tON(A)\n00710\tGOTO 700\n")
    assert report.apply_to(r, other) == 0


def test_the_diagnostics_carry_the_panels_own_findings():
    r = report.parse(SAMPLE)
    prog = parser.parse(r.text)
    report.apply_to(r, prog)
    by_code = {d.code: d for d in report.diagnostics(r, prog)}

    assert by_code["R701"].line == 300           # unresolved point
    assert by_code["R701"].severity is Severity.ERROR
    assert by_code["R702"].line == 450           # failed to execute
    assert by_code["R702"].severity is Severity.ERROR
    assert by_code["R703"].line == 400           # never executed
    assert by_code["R704"].line == 200           # disabled
    assert by_code["R704"].severity is Severity.INFO
    assert all(d.manual for d in by_code.values())


def test_trace_bits_are_ignored_when_the_report_has_none():
    """A report taken right after a clear says nothing about dead code."""
    cleared = SAMPLE.replace("ET  ", "E   ").replace("ETU", "E U").replace("ETF", "E F")
    r = report.parse(cleared)
    assert r.never_executed == set()


def test_a_report_with_no_body_is_not_a_crash():
    r = report.parse("PPCL DISPLAY REPORT\nEnd of report\n")
    assert r.states == {}
    assert r.text == ""


def test_analysis_over_a_report_skips_the_disabled_line():
    r = report.parse(SAMPLE)
    prog = parser.parse(r.text)
    report.apply_to(r, prog)
    a = analyzer.analyze(prog)
    executable = {l.number for l in prog.lines if l.is_executable}
    assert 200 not in executable
    assert a.reachable  # still analysable

def test_a_row_with_neither_E_nor_D_is_reported_not_assumed():
    """"Either an E or a D will always be displayed."

    -- Insight MMI Database Transfer help, UC PPCL Window. So a row carrying
    neither did not survive being copied here, and the enabled flag is this
    module's default rather than anything the panel said. Answering "enabled"
    silently is the wrong way to be wrong: the entire reason to read a report
    is to learn which lines the panel is not evaluating.
    """
    from ppcl import report

    text = (
        "PPCL program <TEST>\n"
        "State  Line   Statement\n"
        "-----  -----  ---------\n"
        "E      00010  ON(FAN)\n"
        "D T    00020  OFF(FAN)\n"
        "  TU   00030  ON(PUMP)\n"
        "End of report\n"
    )
    r = report.parse(text)

    assert r.states[10].state_known and r.states[10].enabled
    assert r.states[20].state_known and not r.states[20].enabled
    assert not r.states[30].state_known

    codes = {d.code: d for d in report.diagnostics(r)}
    assert "R705" in codes
    assert codes["R705"].line == 30
    # The other findings on that row still stand -- U is legible either way.
    assert "R701" in codes and codes["R701"].line == 30


def test_the_status_field_decodes_four_and_five_letter_forms():
    """The books disagree about how many letters there are, and it does not
    matter, because the field is decoded as a set rather than by position.

    The MMI Database Transfer help says "five letters" and then lists four,
    giving DTUF as its worked example. The controller's own PPCL_data record
    carries five booleans, the fifth being the loop flag. Both decode.
    """
    from ppcl import report

    text = (
        "State  Line   Statement\n"
        "-----  -----  ---------\n"
        "DTUF   00010  ON(FAN)\n"
        "E   L  00020  LOOP(A,B,C,D,E,F,G,H)\n"
        "End of report\n"
    )
    r = report.parse(text)

    first = r.states[10]
    assert not first.enabled and first.traced and first.unresolved
    assert first.failed and not first.loop_tested

    second = r.states[20]
    assert second.enabled and second.loop_tested and not second.traced

