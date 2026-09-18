"""Tests for the sequence document, its text form, and the compiler.

The compiler's whole point is that certain PPCL defects become unwritable, so
most of these assert a *property of every compiled program* rather than an
exact string: one backward GOTO, matched releases, no time-based command
inside a conditional.
"""

import pytest

from ppcl import linter, parser, sequence, spec
from ppcl.analyzer import analyze, substatements
from ppcl.ast_nodes import CommandCall, Goto, If, Sampled
from ppcl.sequence import (
    All,
    Between,
    CompileError,
    Compare,
    Interlock,
    Loop,
    Mode,
    Point,
    Reset,
    Rule,
    Sequence,
    SequenceSyntaxError,
    compile_sequence,
)

AHU = """
sequence "Test AHU"
  equipment AHU1
  author Tester

points
  SFAN    digital output
  OADPR   analog  output
  HVLV    analog  output
  CVLV    analog  output
  MAT     analog  input
  DAT     analog  input
  ZNT     analog  input
  DASP    analog  virtual
  DMD     analog  local

modes
  Unoccupied  otherwise
  Occupied    when TIME between 6:00 and 18:00
  Shutdown    when Freeze is on

table
              Unoccupied  Occupied  Shutdown
  SFAN        off         on        off
  OADPR       closed      20        closed
  HVLV        closed      modulate  closed
  CVLV        closed      modulate  closed

interlock Freeze
  trip when MAT < 38
  reset when MAT > 45
  force SFAN off at emer

reset DASP from ZNT
  68 -> 95
  74 -> 55

loop Discharge
  measure DAT
  output DMD
  setpoint DASP
  acting reverse
  throttling 10
  range 0 to 100

reset HVLV from DMD
  50 -> 0
  100 -> 100

reset CVLV from DMD
  0 -> 100
  50 -> 0
"""


def compile_text(text):
    seq = sequence.parse(text)
    ppcl_text, warnings = compile_sequence(seq)
    return seq, ppcl_text, warnings


# --------------------------------------------------------------------------
# Text form
# --------------------------------------------------------------------------


def test_parses_a_full_document():
    seq = sequence.parse(AHU)
    assert seq.name == "Test AHU"
    assert seq.equipment == "AHU1"
    assert [m.name for m in seq.modes] == ["Unoccupied", "Occupied", "Shutdown"]
    assert seq.table.action("SFAN", "Occupied") == "on"
    assert seq.table.action("HVLV", "Unoccupied") == "closed"
    assert len(seq.interlocks) == 1
    assert len(seq.loops) == 1
    # Discharge setpoint reset, plus the two that split demand into the
    # heating and cooling valves.
    assert len(seq.resets) == 3


def test_between_keeps_its_own_and():
    """TIME between 6:00 and 18:00 must not split on the inner 'and'."""
    cond = sequence.text.parse_condition("TIME between 6:00 and 18:00")
    assert isinstance(cond, Between)
    assert cond.low == "6:00" and cond.high == "18:00"


def test_and_still_splits_around_between():
    cond = sequence.text.parse_condition(
        "TIME between 6:00 and 18:00 and MAT > 40"
    )
    assert isinstance(cond, All)
    assert len(cond.parts) == 2
    assert isinstance(cond.parts[0], Between)


def test_or_binds_looser_than_and():
    from ppcl.sequence.model import Any

    cond = sequence.text.parse_condition("A > 1 and B > 2 or C > 3")
    assert isinstance(cond, Any)


def test_status_comparison():
    cond = sequence.text.parse_condition("SFAN is on")
    assert isinstance(cond, Compare)
    assert cond.op == "is" and cond.right == "on"


def test_text_round_trip_is_stable():
    seq = sequence.parse(AHU)
    once = sequence.render(seq)
    twice = sequence.render(sequence.parse(once))
    assert once == twice


def test_json_round_trip_preserves_everything():
    seq = sequence.parse(AHU)
    restored = sequence.loads(sequence.dumps(seq))
    assert sequence.render(restored) == sequence.render(seq)
    assert compile_sequence(restored)[0] == compile_sequence(seq)[0]


def test_table_row_width_is_checked():
    bad = AHU.replace("  SFAN        off         on", "  SFAN        off")
    with pytest.raises(SequenceSyntaxError) as exc:
        sequence.parse(bad)
    assert "cells" in str(exc.value)


def test_table_header_must_name_declared_modes():
    bad = AHU.replace("              Unoccupied  Occupied",
                      "              Unoccupied  Nonsense")
    with pytest.raises(SequenceSyntaxError) as exc:
        sequence.parse(bad)
    assert "Nonsense" in str(exc.value)


def test_syntax_error_reports_the_line():
    with pytest.raises(SequenceSyntaxError) as exc:
        sequence.parse("sequence \"x\"\n\nmodes\n  Broken when ???\n")
    assert exc.value.line_no > 0


# --------------------------------------------------------------------------
# Compiler guarantees
# --------------------------------------------------------------------------


def test_compiled_program_parses_and_lints_clean():
    _seq, text, warnings = compile_text(AHU)
    prog = parser.parse(text, name="t")
    assert prog.errors == []
    bad = [
        d for d in linter.lint(prog)
        if d.severity.value in ("error", "warning")
    ]
    assert bad == [], "\n".join(d.format("t") for d in bad)
    assert warnings == []


def test_exactly_one_backward_goto_and_it_is_last():
    """The single backward branch the Desigo compiler permits."""
    _seq, text, _w = compile_text(AHU)
    prog = parser.parse(text)
    gotos = [
        (ln.number, stmt)
        for ln in prog.lines
        for stmt in substatements(ln.stmt)
        if isinstance(stmt, Goto)
    ]
    backward = [(n, s) for n, s in gotos if s.target <= n]
    assert len(backward) == 1
    assert backward[0][0] == max(n for n, _ in gotos)


def test_every_interlock_gets_a_matching_release():
    _seq, text, _w = compile_text(AHU)
    prog = parser.parse(text)
    forced = {}
    released = {}
    for ln in prog.lines:
        for stmt in substatements(ln.stmt):
            if not isinstance(stmt, CommandCall) or stmt.priority is None:
                continue
            names = [a.name.upper() for a in stmt.args if hasattr(a, "name")]
            target = released if stmt.name == "RELEAS" else forced
            for name in names:
                target.setdefault(name, set()).add(stmt.priority.name)
    assert forced
    for name, priorities in forced.items():
        assert name in released, "%s is forced but never released" % name
        assert priorities <= released[name], (
            "%s is forced at %s but released at %s"
            % (name, priorities, released[name])
        )


def test_time_based_commands_are_never_conditional():
    """LOOP and TABLE must be evaluated on every pass, so never inside an IF."""
    _seq, text, _w = compile_text(AHU)
    prog = parser.parse(text)
    for ln in prog.lines:
        if not isinstance(ln.stmt, If):
            continue
        for stmt in substatements(ln.stmt):
            if isinstance(stmt, Sampled):
                pytest.fail("SAMPLE inside an IF at line %d" % ln.number)
            if isinstance(stmt, CommandCall) and stmt.name in spec.TIME_BASED_COMMANDS:
                pytest.fail(
                    "%s inside an IF at line %d" % (stmt.name, ln.number)
                )


def test_no_subroutines_so_no_subroutine_hazards():
    _seq, text, _w = compile_text(AHU)
    a = analyze(parser.parse(text))
    assert a.subroutines == {}


def test_whole_program_is_one_steady_state_loop():
    _seq, text, _w = compile_text(AHU)
    a = analyze(parser.parse(text))
    assert a.steady_state, "the compiled program does not loop"
    assert a.loop_entry is not None


def test_local_points_carry_their_sigil():
    """A local declared as DMD must be referenced as "$DMD" everywhere."""
    _seq, text, _w = compile_text(AHU)
    assert 'LOCAL("MODE","FREEZE","DMD")' in text
    loop_line = next(l for l in text.splitlines() if "LOOP(" in l)
    assert '"$DMD"' in loop_line
    table_line = next(l for l in text.splitlines() if "HVLV" in l and "TABLE(" in l)
    assert '"$DMD"' in table_line


def test_table_groups_points_sharing_an_action():
    """Points with the same action in a mode become one multi-point command."""
    text = AHU.replace(
        "  SFAN        off         on        off",
        "  SFAN        off         on        off\n"
        "  RFAN        off         on        off",
    ).replace(
        "  SFAN    digital output",
        "  SFAN    digital output\n  RFAN    digital output",
    )
    _seq, ppcl_text, _w = compile_text(text)
    assert "ON(SFAN,RFAN)" in ppcl_text
    assert "OFF(SFAN,RFAN)" in ppcl_text


def test_modulate_emits_nothing_and_leaves_the_point_to_its_loop():
    _seq, text, _w = compile_text(AHU)
    occupied = [
        l for l in text.splitlines()
        if '"$MODE".EQ.2.0' in l
    ]
    assert not any("HVLV" in l for l in occupied)


def test_table_is_emitted_after_the_loops():
    """Order is what lets a table cell override a loop output."""
    _seq, text, _w = compile_text(AHU)
    lines = text.splitlines()
    loop_at = next(i for i, l in enumerate(lines) if "LOOP(" in l)
    table_at = next(i for i, l in enumerate(lines) if '"$MODE".EQ.' in l and "SET" in l)
    assert table_at > loop_at


# --------------------------------------------------------------------------
# Compiler refusals
# --------------------------------------------------------------------------


def test_interlock_without_a_reset_is_refused():
    seq = Sequence(name="t")
    seq.modes = [Mode("Only")]
    seq.interlocks = [
        Interlock("Freeze", trip=Compare("MAT", "<", 38), reset=None,
                  forces=[("SFAN", "off")])
    ]
    with pytest.raises(CompileError) as exc:
        compile_sequence(seq)
    assert "reset" in str(exc.value).lower()


def test_reset_breakpoints_must_ascend():
    seq = Sequence(name="t")
    seq.modes = [Mode("Only")]
    seq.resets = [Reset(output="HWSP", source="OAT", points=[(60, 100), (0, 180)])]
    with pytest.raises(CompileError) as exc:
        compile_sequence(seq)
    assert "ascending" in str(exc.value)


def test_unknown_cell_action_is_refused():
    bad = AHU.replace("  SFAN        off         on", "  SFAN        off         wibble")
    seq = sequence.parse(bad)
    with pytest.raises(CompileError) as exc:
        compile_sequence(seq)
    assert "wibble" in str(exc.value)


def test_over_long_mode_condition_is_refused():
    seq = Sequence(name="t")
    seq.modes = [
        Mode("Base"),
        Mode("Big", All([Compare("P%d" % i, ">", i) for i in range(1, 12)])),
    ]
    with pytest.raises(CompileError) as exc:
        compile_sequence(seq)
    assert "operands" in str(exc.value)


def test_empty_document_is_refused():
    with pytest.raises(CompileError):
        compile_sequence(Sequence(name="empty"))


def test_modulate_without_a_driver_warns():
    text = AHU.replace(
        "reset HVLV from DMD\n  50 -> 0\n  100 -> 100\n", ""
    ).replace("loop Discharge\n  measure DAT\n  output DMD\n", "loop Discharge\n  measure DAT\n  output DMD2\n")
    seq = sequence.parse(text)
    seq.points.append(Point(name="DMD2", kind="analog", role="local"))
    _text, warnings = compile_sequence(seq)
    assert any("HVLV" in w and "modulate" in w for w in warnings)


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------


def test_rule_with_one_action_each_way_becomes_if_then_else():
    seq = Sequence(name="t")
    seq.modes = [Mode("Only")]
    seq.rules = [
        Rule(when=Compare("OAT", "<", 60), then=[("HTG", "on")],
             otherwise=[("HTG", "off")])
    ]
    text, _w = compile_sequence(seq)
    assert "IF(OAT.LT.60.0) THEN ON(HTG) ELSE OFF(HTG)" in text


def test_rule_with_several_actions_repeats_the_test():
    """PPCL allows only one statement per IF branch."""
    seq = Sequence(name="t")
    seq.modes = [Mode("Only")]
    seq.rules = [
        Rule(when=Compare("OAT", "<", 60), then=[("HTG", "on"), ("PMP", "on")])
    ]
    text, _w = compile_sequence(seq)
    assert text.count("IF(OAT.LT.60.0) THEN") == 2


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------


def test_compiled_sequence_actually_controls():
    """The document must produce a program that holds the zone."""
    from ppcl.plant import (
        TestBench,
        build_plant,
        default_bindings,
        default_checks,
    )

    _seq, text, _w = compile_text(AHU)
    for weather in ("design_winter", "design_summer", "shoulder"):
        prog = parser.parse(text, name="t")
        plant = build_plant({"preset": "single_zone_ahu", "weather": weather})
        bench = TestBench(prog, plant, default_bindings())
        bench.clock.hours = 5.0
        for check in default_checks():
            bench.add_check(check)
        result = bench.run(seconds=7200, dt=10)
        failed = [c for c in result.checks if not c.passed]
        assert not failed, "%s: %s" % (
            weather, "; ".join("%s (%s)" % (c.name, c.detail) for c in failed)
        )


def test_generated_interlock_latches_and_blocks_the_schedule():
    from ppcl.plant import Fault, TestBench, build_plant, default_bindings

    _seq, text, _w = compile_text(AHU)
    prog = parser.parse(text, name="t")
    plant = build_plant({"preset": "single_zone_ahu", "weather": "design_winter"})
    bench = TestBench(prog, plant, default_bindings())
    bench.clock.hours = 8.0
    bench.add_fault(
        Fault(at_seconds=600, system="AHU1", kind="oa_damper_stuck", value=100.0)
    )
    result = bench.run(seconds=3600, dt=10)
    assert bench.panel.get("SFAN").priority == "@EMER"
    assert any("SFAN" in b for b in result.blocked)


def test_a_mode_can_be_driven_by_an_interlock():
    """`when Freeze is on` must compile to a test of the latch flag."""
    _seq, text, _w = compile_text(AHU)
    assert 'IF("$FREEZE".EQ.1.0) THEN "$MODE" = 3.0' in text
    # The latch has to be computed before the mode that reads it.
    lines = text.splitlines()
    latch_at = next(i for i, l in enumerate(lines) if '"$FREEZE" = 1.0' in l)
    mode_at = next(i for i, l in enumerate(lines) if '"$MODE" = 3.0' in l)
    assert latch_at < mode_at


def test_interlock_forces_come_after_the_table():
    """A safety must override whatever the decision table just commanded."""
    _seq, text, _w = compile_text(AHU)
    lines = text.splitlines()
    table_at = max(i for i, l in enumerate(lines) if '"$MODE".EQ.' in l)
    force_at = next(i for i, l in enumerate(lines) if "OFF(@EMER" in l)
    assert force_at > table_at


def test_shutdown_mode_prevents_simultaneous_heating_and_cooling():
    """The defect the bench found: a latched fan left both valves hunting.

    With the fan off there is no airflow, so the discharge loop saturates. If
    nothing shuts the valves, the demand split drives heating and cooling at
    once. A Shutdown column in the table closes both.
    """
    from ppcl.plant import Fault, TestBench, build_plant, default_bindings

    _seq, text, _w = compile_text(AHU)
    for dt in (5, 10, 20):
        prog = parser.parse(text, name="t")
        plant = build_plant(
            {"preset": "single_zone_ahu", "weather": "design_winter"}
        )
        bench = TestBench(prog, plant, default_bindings())
        bench.clock.hours = 8.0
        bench.add_fault(
            Fault(at_seconds=1800, system="AHU1", kind="oa_damper_stuck",
                  value=100.0)
        )
        bench.run(seconds=5400, dt=dt)
        ahu = plant.systems["AHU1"]
        hw = ahu.heating_coil.valve.position
        cw = ahu.cooling_coil.valve.position
        assert min(hw, cw) < 5.0, (
            "dt=%d: both valves open after a freeze trip (HW %.1f%%, CW %.1f%%)"
            % (dt, hw, cw)
        )
