"""Tests for the analyzer's view of the main loop.

``runs_every_pass`` answers "does every trip round the main loop execute this
line?". It is computed for all lines at once, from dominators; these tests
hold it to the plain definition, walked the slow way, on programs built to
branch in every way PPCL allows.
"""

import random

import pytest

from ppcl import analyzer, linter, parser


def _walked(a, number):
    """The definition, the slow way: go round the loop avoiding the line."""
    ln = a.index.get(number)
    entry = a.loop_entry
    if (ln is None or ln.disabled or ln.unknown or entry is None
            or number not in a.steady_state):
        return False
    if number == entry:
        return True
    seen, stack = set(), list(a._loop_successors(entry))
    while stack:
        n = stack.pop()
        if n == entry:
            return False
        if n == number or n in seen:
            continue
        seen.add(n)
        stack.extend(a._loop_successors(n))
    return True


def _random_program(rng, size):
    body = []
    numbers = [10 * (i + 1) for i in range(size)]
    for n in numbers[:-1]:
        roll = rng.random()
        target = rng.choice(numbers)
        if roll < 0.2:
            body.append("%d\tIF(A.GT.%d.0) THEN GOTO %d" % (n, n, target))
        elif roll < 0.3:
            body.append("%d\tGOTO %d" % (n, target))
        elif roll < 0.35:
            body.append("%d\tIF(B.EQ.ON) THEN GOTO %d ELSE GOTO %d"
                        % (n, target, rng.choice(numbers)))
        elif roll < 0.4:
            body.append("%d\tC a note" % n)
        else:
            body.append("%d\tSET(%d.0,P%d)" % (n, n, n % 5))
    body.append("%d\tGOTO %d" % (numbers[-1], rng.choice(numbers[:3])))
    return "\n".join(body) + "\n"


@pytest.mark.parametrize("seed", range(40))
def test_every_pass_matches_the_definition(seed):
    rng = random.Random(seed)
    a = analyzer.analyze(parser.parse(_random_program(rng, rng.randint(4, 40))))
    for number in a.numbers:
        assert a.runs_every_pass(number) == _walked(a, number), number


def _large_program(n):
    lines = []
    for i in range(n):
        if i % 5 == 0:
            lines.append("IF(MODE.EQ.1.0) THEN GOTO %d" % ((i + 4) * 10 + 10))
        elif i % 5 == 1:
            lines.append("SET(%d.0,P%03d)" % (i, i % 200))
        elif i % 5 == 2:
            lines.append("OFF(F%03d)" % (i % 200))
        elif i % 5 == 3:
            lines.append("ON(F%03d)" % (i % 200))
        else:
            lines.append("TABLE(P%03d,Q%03d,0,0,100,100)" % (i % 200, i % 200))
    lines.append("GOTO 10")
    return "".join("%05d\t%s\n" % ((j + 1) * 10, s)
                   for j, s in enumerate(lines))


def test_w332_walks_the_loop_once_not_once_per_line(monkeypatch):
    """Asked line by line, every-pass walked the whole loop each time: on
    4,000 lines W332 took 11 of lint's 11.5 seconds. Counted rather than
    timed, so a slow machine cannot make this flaky."""
    prog = parser.parse(_large_program(1000))
    a = analyzer.analyze(prog)
    calls = [0]
    original = analyzer._successors_wrapping

    def counting(an, ln):
        calls[0] += 1
        return original(an, ln)

    monkeypatch.setattr(analyzer, "_successors_wrapping", counting)
    linter.lint(prog, analysis=a)
    assert calls[0] <= 2 * len(a.steady_state) + 10, calls[0]
