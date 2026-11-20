"""The compiled interpreter: guarantees that would break quietly.

ppcl/simcompile.py replaced the tree-walking interpreter and is now the only
implementation of what a PPCL statement does. The rest of the suite checks
what programs do; these check the properties of the compiler itself that a
later change could break without any behaviour test noticing at first.
"""

import pytest

from ppcl import parser
from ppcl.simulator import Simulator


def _sim(*lines):
    return Simulator(parser.parse(chr(10).join(lines) + chr(10)))


def test_every_line_is_compiled_when_the_simulator_is_built():
    """And only the lines a panel could ever run -- not comments."""
    sim = _sim("10 C A COMMENT", "20 X = 1", "30 GOTO 20")
    assert len(sim._line_code) == len(sim.numbers)
    compiled = {n for n, code in zip(sim.numbers, sim._line_code) if code}
    assert compiled == {20, 30}


def test_a_temporary_expression_is_never_cached():
    """The debugger parses a fresh condition on every check.

    Caching those by id would let the next temporary node, handed the same
    id after the last one is freed, pick up the wrong compiled function.
    Only the program's own nodes, which live as long as it does, are cached.
    """
    sim = _sim("10 X = 5", "20 GOTO 10")
    sim.run(passes=1)
    before = len(sim._expr_code)
    for threshold in range(200):
        cond = parser.parse(
            "1 IF(X .GT. %d) THEN ON(Y)" % threshold + chr(10)).lines[0].stmt.cond
        assert sim._eval(cond) == (1.0 if 5 > threshold else 0.0)
    assert len(sim._expr_code) == before


#: For each operator, the left value that would let a short-circuiting
#: version skip its right side. .XOR. cannot short-circuit at all.
_WOULD_SKIP = [(".AND.", "0.0"), (".NAND.", "0.0"), (".OR.", "1.0"),
               (".XOR.", "0.0")]


@pytest.mark.parametrize("op, left", _WOULD_SKIP)
def test_logical_operators_still_evaluate_both_sides(op, left):
    """PPCL does not short-circuit, and neither did the tree-walker.

    The left value is the one that would let a short-circuiting operator
    skip its right side, and the right side carries a warning -- EXP's
    base is unconfirmed. A compiled operator that short-circuited would
    lose the warning, and anything else the right side does.
    """
    sim = _sim("10 X = %s %s EXP(1.0)" % (left, op), "20 GOTO 10")
    sim.run(passes=1)
    assert any("EXP" in w for w in sim.warnings), op


def test_a_table_of_literals_matches_one_read_from_points():
    """Literal breakpoints are folded once; anything else is read each run.

    The two paths must give the same answer, or folding changed a result.
    """
    folded = _sim(
        "10 TABLE(X,Y,0.0,10.0,50.0,30.0,100.0,90.0)", "20 GOTO 10")
    read = _sim(
        "10 TABLE(X,Y,A0,B0,A1,B1,A2,B2)", "20 GOTO 10")
    read.panel.load({"A0": 0.0, "B0": 10.0, "A1": 50.0, "B1": 30.0,
                     "A2": 100.0, "B2": 90.0})
    for x in (-5.0, 0.0, 12.5, 50.0, 73.0, 100.0, 140.0):
        for sim in (folded, read):
            sim.panel.get("X").value = x
            sim.run(passes=1)
        assert folded.panel.value("Y") == read.panel.value("Y"), x


def test_the_write_hook_still_sees_compiled_commands():
    """The debugger swaps sim.command after the program is compiled.

    So a compiled closure has to look command up when it runs. One that
    captured it while compiling would skip the hook and every write
    breakpoint would silently stop firing.
    """
    sim = _sim("10 ON(FAN)", "20 SET(42.0,SP)", "30 X = 7", "40 GOTO 10")
    seen = []
    original = sim.command

    def hooked(name, value, priority, line, what):
        seen.append((name.upper(), line))
        return original(name, value, priority, line, what)

    sim.command = hooked
    sim.run(passes=1)
    assert ("FAN", 10) in seen and ("SP", 20) in seen and ("X", 30) in seen


def test_a_root_of_a_negative_value_warns_instead_of_crashing():
    """Python raises nothing for (-0.01) ** 0.5: it returns a complex
    number, and the simulator fell over a line later. FLOW = K*(DP.ROOT.2)
    with DP a hair below zero as a fan starts is the real trigger."""
    sim = _sim("10 DP = -0.01", "20 FLOW = 2.0*(DP.ROOT.2)", "30 GOTO 10")
    sim.run(passes=2)
    assert sim.panel.value("FLOW") == 0.0
    assert any("ROOT" in w and "negative" in w for w in sim.warnings)


_DEFINED = ['00005\tDEFINE(A,"CT1.")', "00010\tON(%A%FAN)",
            "00020\tSPD = 40.0", "00030\tIF(%A%FAN .EQ. ON) THEN SPD = 90.0",
            '00040\tSET(55.0,"%A%SP")', "00050\tGOTO 10"]


def test_define_abbreviations_name_the_points_they_spell():
    """DEFINE was a no-op: "%A%SP" was a point of that literal name, and a
    bare %A%FAN read 0 and dropped its writes, so ON(%A%FAN) did nothing
    and the IF testing it took the wrong branch."""
    sim = Simulator(parser.parse(chr(10).join(_DEFINED) + chr(10)))
    sim.run(passes=2)
    assert sim.panel.value("CT1.FAN") == 1.0
    assert sim.panel.value("SPD") == 90.0
    assert sim.panel.value("CT1.SP") == 55.0
    assert not any("%" in name for name in sim.panel.points)
    assert not any("DEFINE" in w for w in sim.warnings)


def test_expanding_defines_leaves_the_parsed_program_alone():
    """The linter and the editor share the parse; the simulator compiles
    expanded copies of its own."""
    prog = parser.parse(chr(10).join(_DEFINED) + chr(10))
    before = repr(prog.lines[1].stmt)
    Simulator(prog).run(passes=1)
    assert repr(prog.lines[1].stmt) == before and "%A%FAN" in before


def test_a_debugger_condition_can_name_a_point_through_its_define():
    sim = _sim(*[l.replace("\t", " ") for l in _DEFINED])
    sim.run(passes=1)
    cond = parser.parse("1 IF(%A%FAN .EQ. ON) THEN ON(Y)" + chr(10)).lines[0].stmt.cond
    assert sim._eval(cond) == 1.0


def test_an_abbreviation_with_no_define_says_so():
    sim = _sim("10 ON(%NONE%FAN)", "20 GOTO 10")
    sim.run(passes=1)
    assert any("%NONE% has no DEFINE" in w for w in sim.warnings)


@pytest.mark.parametrize("args", ["1,8:00,55.0,17:00", "1,1,8:00,55.0,17:00"])
def test_a_short_todset_is_skipped_with_a_warning_not_a_crash(args):
    """TODSET needs six fixed arguments and a point. With four or five it
    indexed past the end of its arguments, and the IndexError ended the
    whole run -- the bench, the debugger, ppcl run."""
    sim = Simulator(parser.parse("10\tTODSET(%s)\n20\tON(FAN)\n30\tGOTO 10\n"
                                 % args))
    sim.run(passes=2)
    assert sim.panel.value("FAN") == 1.0           # the rest still ran
    assert any("TODSET at line 10" in w for w in sim.warnings)


@pytest.mark.parametrize("month, day, hours, expect", [
    (6, 30, 1, (7, 1)), (12, 31, 1, (1, 1)), (2, 28, 1, (3, 1)),
    (1, 31, 24 * 31, (3, 3)), (6, 15, 24 * 365, (6, 15)),
])
def test_the_clock_rolls_the_month_over(month, day, hours, expect):
    """June 30 plus an hour past midnight was June 31: day_of_month grew
    and the month never changed, so HOLIDA dates and the MONTH and DAYOFM
    points went wrong on any run that crossed a month end."""
    from ppcl.simulator import Clock

    clock = Clock(hours=23.5, day_of_month=day, month=month)
    clock.advance(hours * 3600.0)
    assert (clock.month, clock.day_of_month) == expect


def test_an_absurd_clock_advance_still_returns_at_once():
    from ppcl.simulator import Clock

    clock = Clock()
    clock.advance(1e15)
    assert 1 <= clock.month <= 12 and 1 <= clock.day_of_month <= 31


def test_an_overflow_is_simulated_as_zero_with_a_warning_not_a_crash():
    """X = X * 1000000.0 reaches infinity after about fifty passes, and the
    next write crashed the run formatting its trace line: an
    OverflowError, and an internal error from ppcl run or the bench."""
    sim = Simulator(parser.parse("10\tX = X * 1000000.0\n20\tON(FAN)\n"
                                 "30\tGOTO 10\n"))
    sim.panel.load({"X": 1.0})
    sim.run(passes=80)
    assert sim.panel.value("X") == 0.0
    assert sim.panel.value("FAN") == 1.0
    assert any("X overflowed" in w for w in sim.warnings)


def test_a_division_by_zero_says_so():
    """It was simulated as 0 without a word, though what a panel returns is
    not documented -- the case .ROOT. of a negative already warned about."""
    sim = Simulator(parser.parse("10\tZ = 1.0 / Y\n20\tW = 4.0 / 2.0\n"
                                 "30\tGOTO 10\n"))
    sim.run(passes=2)
    assert sim.panel.value("Z") == 0.0 and sim.panel.value("W") == 2.0
    assert any("division by zero" in w for w in sim.warnings)
