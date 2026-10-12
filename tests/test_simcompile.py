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
