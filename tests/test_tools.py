"""Tests for the formatter, simulator, generator and redactor."""

import json

import pytest

from ppcl import analyzer, formatter, generator, linter, parser, spec
from ppcl.simulator import Clock, Panel, Simulator


# --------------------------------------------------------------------------
# Renumbering
# --------------------------------------------------------------------------


def test_renumber_rewrites_every_reference():
    text = (
        "100\tONPWRT(300)\n"
        "200\tGOSUB 500\n"
        "300\tIF(A.GT.1) THEN GOTO 400\n"
        "350\tSAMPLE(60) GOSUB 500\n"
        "360\tACT(400,500)\n"
        "400\tGOTO 200\n"
        "500\tON(B)\n"
        "600\tRETURN\n"
    )
    prog = parser.parse(text)
    result = formatter.renumber(prog, start=10, step=10)
    assert result.warnings == []
    assert result.rewritten_references == 7

    out = result.text
    assert "ONPWRT(30)" in out
    assert "GOSUB 70" in out
    assert "THEN GOTO 60" in out
    assert "ACT(60,70)" in out
    assert "GOTO 20" in out


def test_renumber_rewrites_pdl_group_bounds_but_not_its_flags():
    """PDL delimits each priority group by PDLDAT *line numbers*.

    Siemens' own example is ``PDL(1,TOTKW1,TGT1,100,199,0,...)`` -- an integer
    meter area, two points, then triples of (start line, end line, shed mode).
    Renumbering has to move the line numbers and leave the area and the shed
    modes alone. The modes are 0 or 1 and 1 is a legal line number, so this
    cannot be done by rewriting every number in the call.
    """
    text = (
        "100\tPDLDAT(FAN17,10,5,180,10)\n"
        "199\tPDLDAT(FAN18,10,5,180,10)\n"
        "200\tPDLDAT(FAN19,10,5,180,10)\n"
        "299\tPDLDAT(FAN20,10,5,180,10)\n"
        "1000\tPDL(1,TOTKW1,TGT1,100,199,0,200,299,1)\n"
        "1100\tGOTO 100\n"
    )
    result = formatter.renumber(parser.parse(text), start=10, step=10)
    assert result.warnings == []
    assert "PDL(1,TOTKW1,TGT1,10,20,0,30,40,1)" in result.text


def test_renumber_handles_the_physical_form_of_pdl():
    """Physical firmware drops the area number: PDL(totkw,target,g1s,g1e,...)."""
    text = (
        "100\tPDLDAT(FAN17,10,5,180,10)\n"
        "199\tPDLDAT(FAN18,10,5,180,10)\n"
        "1000\tPDL(TOTKW1,TGT1,100,199)\n"
        "1100\tGOTO 100\n"
    )
    result = formatter.renumber(parser.parse(text), start=10, step=10)
    assert "PDL(TOTKW1,TGT1,10,20)" in result.text


def test_renumber_preserves_analysis():
    prog = parser.parse_file("samples/time.ppcl")
    before = analyzer.analyze(prog)
    result = formatter.renumber(prog, preserve_blocks=True)
    after = analyzer.analyze(parser.parse(result.text))
    assert len(before.reachable) == len(after.reachable)
    assert len(before.steady_state) == len(after.steady_state)
    assert len(before.subroutines) == len(after.subroutines)


def test_renumber_refuses_duplicates_by_default():
    prog = parser.parse("10\tON(A)\n10\tOFF(B)\n20\tGOTO 10\n")
    result = formatter.renumber(prog)
    assert result.text == ""
    assert "refusing to renumber" in result.warnings[0]


def test_split_duplicates_keeps_every_line():
    prog = parser.parse("10\tON(A)\n10\tOFF(B)\n20\tGOTO 10\n")
    result = formatter.renumber(prog, split_duplicates=True)
    assert len([l for l in result.text.splitlines() if l.strip()]) == 3
    assert any("split" in w for w in result.warnings)


def test_allow_duplicates_matches_the_panel():
    prog = parser.parse("10\tON(A)\n10\tOFF(B)\n20\tGOTO 10\n")
    result = formatter.renumber(prog, allow_duplicates=True)
    assert len([l for l in result.text.splitlines() if l.strip()]) == 2
    assert "OFF(B)" not in result.text


def test_renumber_refuses_to_exceed_the_line_maximum():
    prog = parser.parse("".join("%d\tON(A)\n" % (i * 10) for i in range(1, 50)))
    result = formatter.renumber(prog, start=32000, step=100)
    assert result.text == ""
    assert "32767" in result.warnings[0]


def test_format_sorts_by_line_number():
    prog = parser.parse("20\tON(A)\n10\tOFF(B)\n")
    out = formatter.format_text(prog)
    assert out.splitlines()[0].startswith("00010")


def test_insert_line_rejects_a_taken_number():
    prog = parser.parse("10\tON(A)\n")
    with pytest.raises(ValueError):
        formatter.insert_line(prog, 10, "OFF(B)")


# --------------------------------------------------------------------------
# Simulator
# --------------------------------------------------------------------------


def run(text, points=None, passes=3, interval=1.0, hours=8.0):
    prog = parser.parse(text)
    sim = Simulator(prog, clock=Clock(hours=hours))
    if points:
        sim.panel.load(points)
    sim.run(passes=passes, seconds_per_pass=interval)
    return sim


def test_table_interpolates_linearly():
    sim = run("10\tTABLE(OAT,HWSP,0,180,60,100)\n20\tGOTO 10\n", {"OAT": 30})
    assert sim.panel.value("HWSP") == pytest.approx(140.0)


def test_table_clamps_outside_its_range():
    sim = run("10\tTABLE(OAT,HWSP,0,180,60,100)\n20\tGOTO 10\n", {"OAT": -20})
    assert sim.panel.value("HWSP") == pytest.approx(180.0)
    sim = run("10\tTABLE(OAT,HWSP,0,180,60,100)\n20\tGOTO 10\n", {"OAT": 90})
    assert sim.panel.value("HWSP") == pytest.approx(100.0)


def test_priority_blocks_a_lower_priority_command():
    text = (
        "10\tOFF(@EMER,SFAN)\n"
        "20\tON(SFAN)\n"
        "30\tGOTO 10\n"
    )
    sim = run(text)
    assert sim.panel.value("SFAN") == 0.0
    assert sim.panel.get("SFAN").priority == "@EMER"
    assert any(e.kind == "blocked" for e in sim.events)


def test_release_restores_ppcl_control():
    text = (
        "10\tIF(TRIP.EQ.1) THEN OFF(@EMER,SFAN)\n"
        "20\tIF(TRIP.EQ.0) THEN RELEAS(@EMER,SFAN)\n"
        "30\tIF(TRIP.EQ.0) THEN ON(SFAN)\n"
        "40\tGOTO 10\n"
    )
    prog = parser.parse(text)
    sim = Simulator(prog)
    sim.panel.load({"TRIP": 1})
    sim.run(passes=2)
    assert sim.panel.get("SFAN").priority == "@EMER"
    sim.panel.load({"TRIP": 0})
    sim.run(passes=2)
    assert sim.panel.get("SFAN").priority == "@NONE"
    assert sim.panel.value("SFAN") == 1.0


def test_release_at_too_low_a_priority_does_nothing():
    text = "10\tOFF(@OPER,SFAN)\n20\tRELEAS(@PDL,SFAN)\n30\tGOTO 10\n"
    sim = run(text)
    assert sim.panel.get("SFAN").priority == "@OPER"


def test_priority_comparison_tests_priority_not_value():
    text = (
        "10\tOFF(@EMER,SFAN)\n"
        "20\tIF(SFAN.EQ.@EMER) THEN ON(HORN)\n"
        "30\tGOTO 10\n"
    )
    sim = run(text)
    assert sim.panel.value("HORN") == 1.0


def test_min_and_max():
    sim = run(
        "10\tMAX(HI,A,B,C)\n20\tMIN(LO,A,B,C)\n30\tGOTO 10\n",
        {"A": 5, "B": 12, "C": -3},
    )
    assert sim.panel.value("HI") == 12
    assert sim.panel.value("LO") == -3


def test_dbswit_holds_inside_the_dead_band():
    text = "10\tDBSWIT(1,RMTEMP,55,58,SFAN)\n20\tGOTO 10\n"
    sim = run(text, {"RMTEMP": 50})
    assert sim.panel.value("SFAN") == 1.0
    sim.panel.load({"RMTEMP": 56.5})
    sim.run(passes=2)
    assert sim.panel.value("SFAN") == 1.0  # unchanged inside the band
    sim.panel.load({"RMTEMP": 60})
    sim.run(passes=2)
    assert sim.panel.value("SFAN") == 0.0


def test_tod_switches_on_the_schedule():
    text = "10\tTOD(1,1,8:00,17:00,LITES)\n20\tGOTO 10\n"
    assert run(text, hours=12.0).panel.value("LITES") == 1.0
    assert run(text, hours=6.0).panel.value("LITES") == 0.0


def test_tod_handles_a_schedule_that_wraps_midnight():
    text = "10\tTOD(1,1,17:00,7:00,OLITE)\n20\tGOTO 10\n"
    assert run(text, hours=22.0).panel.value("OLITE") == 1.0
    assert run(text, hours=12.0).panel.value("OLITE") == 0.0


def test_todset_commands_analog_values():
    text = "10\tTODSET(1,1,9:00,72.0,17:00,55.0,SPTEMP)\n20\tGOTO 10\n"
    assert run(text, hours=12.0).panel.value("SPTEMP") == 72.0
    assert run(text, hours=20.0).panel.value("SPTEMP") == 55.0


def test_sample_rate_limits_its_statement():
    text = '10\tLOCAL("N")\n20\tSAMPLE(10) "$N" = "$N" + 1.0\n30\tGOTO 20\n'
    prog = parser.parse(text)
    sim = Simulator(prog)
    sim.run(passes=30, seconds_per_pass=1.0)
    # One immediate execution plus roughly one per 10 seconds.
    assert 2 <= sim.panel.value("$N") <= 5


def test_wait_delays_after_a_trigger_edge():
    text = "10\tWAIT(60,CNPUMP,CHPUMP,11)\n20\tGOTO 10\n"
    prog = parser.parse(text)
    sim = Simulator(prog)
    sim.panel.load({"CNPUMP": 0})
    sim.run(passes=2, seconds_per_pass=1.0)
    assert sim.panel.value("CHPUMP") == 0.0
    sim.panel.load({"CNPUMP": 1})
    sim.run(passes=2, seconds_per_pass=1.0)
    assert sim.panel.value("CHPUMP") == 0.0  # still waiting
    sim.run(passes=70, seconds_per_pass=1.0)
    assert sim.panel.value("CHPUMP") == 1.0


def test_gosub_and_return():
    text = (
        "10\tGOSUB 100\n"
        "20\tGOTO 10\n"
        "100\tON(A)\n"
        "110\tRETURN\n"
    )
    sim = run(text)
    assert sim.panel.value("A") == 1.0


def test_gosub_passes_arguments_as_arg_locals():
    text = (
        "10\tGOSUB 100 SRC\n"
        "20\tGOTO 10\n"
        '100\tDEST = "$ARG1"\n'
        "110\tRETURN\n"
    )
    sim = run(text, {"SRC": 42})
    assert sim.panel.value("DEST") == 42


def test_disabled_lines_do_not_execute():
    text = "10\tDISABL(30)\n20\tON(A)\n30\tON(B)\n40\tGOTO 20\n"
    sim = run(text)
    assert sim.panel.value("A") == 1.0
    assert sim.panel.value("B") == 0.0


def test_main_loop_is_not_treated_as_a_runaway():
    """A GOTO back to the top is the normal structure, not an error."""
    sim = run("10\tON(A)\n20\tGOTO 10\n", passes=5)
    assert sim.warnings == []
    assert sim.panel.value("A") == 1.0


def test_starved_lines_are_reported():
    """An inner loop that never exits leaves the rest of the program unrun."""
    text = (
        "10\tON(A)\n"
        "20\tON(B)\n"
        "30\tGOTO 20\n"
        "40\tON(NEVER)\n"
    )
    prog = parser.parse(text)
    sim = Simulator(prog)
    sim.execute_lines(500)
    assert 40 in sim.starved_lines
    assert sim.panel.value("NEVER") == 0.0


def test_execution_state_persists_across_calls():
    text = '10\tLOCAL("N")\n20\t"$N" = "$N" + 1.0\n30\tGOTO 20\n'
    prog = parser.parse(text)
    sim = Simulator(prog)
    sim.execute_lines(4)
    first = sim.panel.value("$N")
    sim.execute_lines(4)
    assert sim.panel.value("$N") > first


def test_bad_scenario_value_is_rejected_clearly():
    panel = Panel()
    with pytest.raises(ValueError) as exc:
        panel.load({"MAT": "warmish"})
    assert "MAT" in str(exc.value)


# --------------------------------------------------------------------------
# Generator
# --------------------------------------------------------------------------


def test_builder_resolves_labels():
    b = generator.Builder()
    b.label("TOP")
    b.code("ON(A)")
    b.code("GOTO {t}", t="TOP")
    out = b.render()
    assert "00010\tON(A)" in out
    assert "GOTO 10" in out


def test_builder_binds_labels_past_comments():
    b = generator.Builder()
    b.label("TOP")
    b.comment("a header")
    b.code("ON(A)")
    b.goto("TOP")
    # The label must land on the executable line, not the comment.
    assert "GOTO 20" in b.render()


def test_builder_rejects_an_undefined_label():
    b = generator.Builder()
    b.code("GOTO {t}", t="NOWHERE")
    with pytest.raises(generator.BuildError):
        b.render()


def test_builder_rejects_a_duplicate_label():
    b = generator.Builder()
    b.label("X")
    b.code("ON(A)")
    b.label("X")
    b.code("ON(B)")
    with pytest.raises(generator.BuildError):
        b.render()


def test_proportional_gain_matches_the_manual_formula():
    assert generator.proportional_gain(100.0, 10.0) == 10000
    assert generator.integral_gain(10000) == 200
    assert generator.loop_bias(3.0, 8.0) == pytest.approx(5.5)


@pytest.mark.parametrize(
    "triple,digit",
    [
        ((False, False, False), "0"),
        ((True, False, False), "1"),
        ((False, True, False), "2"),
        ((True, True, False), "3"),
        ((False, False, True), "4"),
        ((True, False, True), "5"),
        ((False, True, True), "6"),
        ((True, True, True), "7"),
    ],
)
def test_duty_cycle_digit_matches_table_4_1(triple, digit):
    """Every row of the manual's Table 4-1, applied to the first segment.

    The four digits are read right to left, so putting the pattern in the
    first 15-minute segment puts its digit in the last character.
    """
    slots = list(triple) + [False] * 9
    assert generator.duty_cycle_pattern(slots)[-1] == digit


def test_duty_cycle_segments_are_ordered_right_to_left():
    # First segment ON/OFF/OFF (1), second OFF/ON/OFF (2),
    # third OFF/OFF/ON (4), fourth ON/ON/ON (7)  ->  "7421"
    slots = [True, False, False,
             False, True, False,
             False, False, True,
             True, True, True]
    assert generator.duty_cycle_pattern(slots) == "7421"


def test_table_statement_rejects_descending_x():
    with pytest.raises(ValueError):
        generator.table_statement("OAT", "HWSP", [(60, 100), (0, 180)])


@pytest.mark.parametrize("name", sorted(generator.TEMPLATES))
def test_every_template_parses_and_lints_clean(name):
    fn = generator.TEMPLATES[name]
    if name == "skeleton":
        text = fn("Test")
    elif name == "schedule":
        text = fn(("SFAN", "RFAN"))
    elif name == "leadlag":
        text = fn(("PMP1", "PMP2"))
    else:
        text = fn()
    prog = parser.parse(text, name=name)
    assert prog.errors == []
    bad = [
        d for d in linter.lint(prog)
        if d.severity.value in ("error", "warning")
    ]
    assert bad == [], "\n".join(d.format(name) for d in bad)


def test_generated_ahu_releases_its_freeze_latch():
    """The template must not reproduce the bug it exists to avoid."""
    text = generator.air_handler()
    prog = parser.parse(text)
    sim = Simulator(prog, clock=Clock(hours=12.0))
    sim.panel.load({"MAT": 30.0, "OAT": 40.0, "DAT": 55.0, "DASP": 55.0})
    sim.run(passes=3, seconds_per_pass=10)
    assert sim.panel.get("SFAN").priority == "@EMER"
    assert sim.panel.value("SFAN") == 0.0

    sim.panel.load({"MAT": 55.0})
    sim.run(passes=4, seconds_per_pass=10)
    assert sim.panel.get("SFAN").priority == "@NONE"
    assert sim.panel.value("SFAN") == 1.0


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------


def test_redaction_hides_site_names_but_keeps_logic():
    from ppcl.redact import Redactor

    r = Redactor()
    text = '00010\tIF(TIME.GT.8:00) THEN ON("PORTLAND.TOWER.AHU01.SFAN")\n'
    out = r.redact_text(text)
    assert "PORTLAND" not in out
    assert "TOWER" not in out
    assert "IF(TIME.GT.8:00) THEN ON(" in out
    assert "SFAN" in out  # generic vocabulary survives
    assert "AHU01" in out


def test_redaction_is_consistent_across_uses():
    from ppcl.redact import Redactor

    r = Redactor()
    a = r.redact_text('10\tON("SITEX.PT1")\n')
    b = r.redact_text('20\tOFF("SITEX.PT1")\n')
    token = a.split('"')[1]
    assert token in b


def test_redaction_drops_comment_text():
    from ppcl.redact import Redactor

    out = Redactor().redact_text("10\tC Suite 400 tenant Acme Corp\n")
    assert "Acme" not in out and "Suite" not in out


def test_the_analysis_holds_together(sample_files):
    """Invariants a dozen flow rules rest on, asserted rather than assumed.

    Checked across every program in samples/ here, and across all 69 real
    programs in the research corpora when this was first run: zero violations.

    The one apparent violation was this test being wrong. `edges` records the
    target a branch was WRITTEN with, not where control lands -- resolution is
    `resolve_target`'s job, because the panel sends a branch to a missing line
    on to the next line after it. So the real invariant is stronger and is the
    last one here: a target that does not exist is always both resolved AND
    reported, never silently redirected.
    """
    from ppcl import analyzer, linter, parser

    for path in sample_files:
        prog = parser.parse(open(path, encoding="utf-8").read(), name=path)
        a = analyzer.analyze(prog)
        numbers = set(a.numbers)

        assert a.steady_state <= a.reachable, path
        assert not (a.one_shot & a.steady_state), path
        assert a.one_shot <= a.reachable, path
        for name in ("reachable", "steady_state", "one_shot", "subroutine_lines"):
            assert getattr(a, name) <= numbers, (path, name)

        for n in numbers:
            target = a.resolve_target(n)
            assert target is None or target in numbers, (path, n, target)

        reported = {d.line for d in linter.lint(prog, analysis=a)
                    if d.code in ("E201", "W202")}
        for frm, to, kind in a.edges:
            if to is None or to in numbers:
                continue
            # written at a line that does not exist: resolved, and reported.
            assert a.resolve_target(to) in numbers or a.resolve_target(to) is None
            assert frm in reported, (path, frm, to, kind)


def test_renumber_rewrites_every_line_carrying_command():
    """Eight commands take a line number, and missing one corrupts a file
    somebody loads into a panel -- an ONPWRT restarting at the wrong place,
    an ACT enabling somebody else's block.
    """
    cases = {
        "GOTO":   "10\tGOTO 500\n500\tON(A)\n510\tGOTO 10\n",
        "GOSUB":  "10\tGOSUB 500\n20\tGOTO 10\n500\tON(A)\n510\tRETURN\n",
        "ACT":    "10\tACT(500)\n500\tON(A)\n510\tGOTO 10\n",
        "DEACT":  "10\tDEACT(500)\n500\tON(A)\n510\tGOTO 10\n",
        "ENABLE": "10\tENABLE(500)\n500\tON(A)\n510\tGOTO 10\n",
        "DISABL": "10\tDISABL(500)\n500\tON(A)\n510\tGOTO 10\n",
        "ONPWRT": "10\tONPWRT(500)\n500\tON(A)\n510\tGOTO 10\n",
        # and the nested forms, which is where this usually breaks
        "in IF":  "10\tIF(A.GT.1.0) THEN ACT(500) ELSE DEACT(510)\n"
                  "500\tON(B)\n510\tOFF(B)\n520\tGOTO 10\n",
        "in SAMPLE": "10\tSAMPLE(60) ACT(500)\n500\tON(B)\n510\tGOTO 10\n",
        "multi":  "10\tACT(500,510)\n500\tON(B)\n510\tON(C)\n520\tGOTO 10\n",
    }
    for name, text in cases.items():
        prog = parser.parse(text)
        assert prog.errors == [], (name, prog.errors)
        out = formatter.renumber(prog, start=1010, step=10)
        for line in out.text.splitlines():
            arg = line.split(chr(9), 1)[-1]
            assert "500" not in arg and "510" not in arg, (name, line)


def test_renumber_says_so_when_it_joins_a_continuation():
    """The one place renumbering is not byte-for-byte, stated rather than hidden.

    The parser joins a statement split across '&' into one body and keeps only
    a flag, so renumbering returns the joined form. A continuation is usually
    there to stay under the line limit -- 66 characters on APOGEE against 198
    continued -- so the join can turn a compliant program into one W104 fires
    on. It is not re-split, because the construct appears zero times in 11,873
    lines of real PPCL and machinery for a case nobody has is complexity this
    project refuses elsewhere.
    """
    text = ("100\tIF(ZONE1.GT.75.0.AND.ZONE2.GT.75.0.AND.ZONE3.GT.75.0) THEN &\n"
            "\tON(" + chr(34) + "AHU1.COOLING.STAGE1" + chr(34) + ")\n"
            "110\tGOTO 100\n")
    out = formatter.renumber(parser.parse(text), start=500, step=10)
    assert any("continuation" in w for w in out.warnings)

    plain = formatter.renumber(parser.parse("100\tON(A)\n110\tGOTO 100\n"),
                               start=500, step=10)
    assert not any("continuation" in w for w in plain.warnings)


def test_every_command_is_either_simulated_or_declared_unsimulated():
    """The third state -- neither -- is the one that lies.

    Seven commands used to fall past every branch of _exec_command to a bare
    `return None`: ADAPTM, ADAPTS, LSQ2, LSQDAT, LSTSQR, GETVAL, SETVAL. Not
    modelled and not warned about. A program whose ADAPTM drives a damper
    simulated cleanly, left cv at whatever it already held, and every IF
    downstream took the wrong branch with nothing to say why.

    Modelling them is the wrong fix -- an invented adaptive output is a
    confident wrong number, which is what LOOP's standing disclaimer exists to
    avoid. Saying so is the right one.
    """
    import pathlib
    import re
    from ppcl import simulator, spec

    src = pathlib.Path("ppcl/simulator.py").read_text(encoding="utf-8")
    body = src[src.index("def _exec_command"):
               src.index("# -- individual command implementations")]
    modelled = set(re.findall(r'name == "([A-Z0-9]+)"', body))
    for grp in re.findall(r"name in \(([^)]*)\)", body):
        modelled |= set(re.findall(r'"([A-Z0-9]+)"', grp))

    # GOTO/GOSUB/RETURN/SAMPLE are their own AST nodes, handled before this
    # ever sees a CommandCall.
    branch_nodes = {"GOTO", "GOSUB", "RETURN", "SAMPLE"}
    unaccounted = (set(spec.ALL) - modelled - set(simulator.UNMODELLED)
                   - branch_nodes)
    assert unaccounted == set(), sorted(unaccounted)


def test_an_unsimulated_command_names_the_points_it_did_not_write():
    """"Skipped" and "these values are stale" are different warnings."""
    from ppcl import parser
    from ppcl.simulator import Simulator

    text = ("10\tADAPTM(SAT,CV,SP,MATC,MAM,10,3.0,60,60,60,HER,DBR,DER,ERRP)\n"
            "20\tGOTO 10\n")
    sim = Simulator(parser.parse(text))
    sim.panel.load({"SAT": 55.0, "SP": 55.0, "CV": 0.0})
    sim.run(passes=2, seconds_per_pass=1.0)

    assert len(sim.warnings) == 1
    w = sim.warnings[0]
    assert "ADAPTM" in w and "not simulated" in w
    # The output points are named, because everything downstream of them is
    # unsound and the person needs to know which values to distrust.
    assert "CV" in w and "ERRP" in w


def test_redaction_keeps_an_OIP_keystroke_sequence_intact():
    """An OIP sequence is keystrokes, not a point name.

    Siemens' own database converter gives up here -- "Point names in comments
    or OIP statements are not modified and must be modified manually" -- so
    the sequence has to be split on its own separator before anything treats
    it as a name. Folding the whole string through the name mapper turned
    "P/T/D/H///SITE.TOWER.AHU01.SFAN/1/" into three meaningless tokens that
    did not match the same point named anywhere else in the program.
    """
    from ppcl.redact import Redactor

    r = Redactor()
    out = r.redact_text(
        '10\tOIP(TRIG,"P/T/D/H///SITEX.TOWER.AHU01.SFAN/1/")\n'
        '20\tON("SITEX.TOWER.AHU01.SFAN")\n30\tGOTO 10\n'
    )

    assert "SITEX" not in out and "TOWER" not in out
    # The menu structure survives: single keystrokes, the empty levels, the
    # typed number.
    assert "P/T/D/H///" in out and "/1/" in out
    # And the point is the SAME redacted name in both places.
    inside = out.splitlines()[0].split('"')[1].split("///")[1].split("/")[0]
    outside = out.splitlines()[1].split('"')[1]
    assert inside == outside, (inside, outside)
    # Generic vocabulary is preserved in both, not just one.
    assert inside.endswith(".AHU01.SFAN")
    # No junk keys: the mapper saw three real names and nothing else.
    assert set(r.mapping) == {"SITEX", "TOWER", "AHU01", "TRIG"}


def test_the_bare_pass_does_not_walk_back_into_a_quoted_name():
    """A pre-existing hole that only an OIP sequence was wide enough to show.

    The bare-name pass excludes a name preceded by a quote or a dot, which
    covers an ordinary "A.B.C" -- but not one preceded by the "/" inside an
    OIP sequence. So it re-redacted tokens the quoted pass had just written,
    and PT001 became PT007.
    """
    from ppcl.redact import Redactor

    out = Redactor().redact_text(
        '10\tOIP(TRIG,"A/SITEX.PUMP/B")\n20\tGOTO 10\n'
    )
    quoted = out.splitlines()[0].split('"')[1]
    # Exactly one redaction per component, no PTnnn wrapped in another PTnnn.
    assert quoted.count("PT") == 1, quoted
    assert quoted.startswith("A/") and quoted.endswith("/B")


def test_a_point_may_be_named_with_a_reserved_word():
    """Reserved means "do not name a point this", not "this name is illegal".

    Panels in the field do carry points whose names collide with PPCL
    keywords, and they enumerate them like any other point. A tool that
    rejected or rewrote such a name would break a program that works. So the
    unresolved-reference check skips reserved words rather than reporting
    them missing, and the redactor leaves them alone rather than mapping them
    to a name the panel does not have.
    """
    from ppcl import points
    from ppcl.redact import Redactor

    text = '10\tIF("ALARM".EQ.ON) THEN ON("SFAN")\n'
    prog = parser.parse(text)
    assert prog.errors == []

    db = points.load_csv("Point Name,Point Type\nSFAN,LDO\n")
    assert [row["name"] for row in db.unresolved(prog)] == []

    assert '"ALARM"' in Redactor().redact_text(text)


def test_redacted_program_still_parses():
    from ppcl.redact import Redactor

    original = open("samples/time.ppcl", encoding="utf-8").read()
    out = Redactor().redact_text(original)
    prog = parser.parse(out)
    assert prog.errors == []


def test_duty_cycle_pattern_matches_the_manuals_worked_example():
    """A6V10374898 Ch.3 "DC (Duty cycle)", Table 3-1 and its example.

    The 2000 manual's Table 4-1 and the worked example beside it disagreed, and
    this encoder followed the table. The PXC.A manual settles it: its Table 3-1
    matches, and this time the worked example agrees with the table.

        first 15 min  ON, OFF, OFF  -> 1
        second        OFF, OFF, OFF -> 0
        third         OFF, OFF, OFF -> 0
        fourth        ON, ON, ON    -> 7
        entered in reverse order    -> DC(HFAN,7001)
    """
    from ppcl.generator import duty_cycle_pattern

    T, F = True, False
    assert duty_cycle_pattern([T, F, F, F, F, F, F, F, F, T, T, T]) == "7001"


def test_every_duty_cycle_code_matches_table_3_1():
    from ppcl.generator import duty_cycle_pattern

    T, F = True, False
    table_3_1 = {
        (F, F, F): "0", (T, F, F): "1", (F, T, F): "2", (T, T, F): "3",
        (F, F, T): "4", (T, F, T): "5", (F, T, T): "6", (T, T, T): "7",
    }
    for slots, code in table_3_1.items():
        # Put the segment first in the hour, so it is the right-most digit.
        assert duty_cycle_pattern(list(slots) + [F] * 9)[-1] == code
