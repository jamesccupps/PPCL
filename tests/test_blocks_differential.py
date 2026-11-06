"""A compiled diagram computes what the diagram says.

Random diagrams of logic, comparison and arithmetic blocks are compiled,
run in the simulator, and checked against the diagram evaluated directly,
block by block. Every case the tests elsewhere wrote by hand put compound
expressions in the places that happened to be safe; this put them
everywhere, and before it the compiler emitted NOT(A OR B) as A OR (B = 0),
Z-(X+Y) as Z-X+Y, and a three-input NAND that was true with all three on.
"""

import math
import random

import pytest

from ppcl import parser
from ppcl.blocks import Block, BlockError, Diagram, compile_diagram
from ppcl.simulator import Simulator

ANALOG = ("X", "Y", "Z")
BINARY = ("B1", "B2")
COMPARISONS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
               "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
               "=": lambda a, b: a == b, "!=": lambda a, b: a != b}


def _truth(x):
    return 1.0 if x else 0.0


#: Each block's meaning, in the diagram's own terms. Logic reads any
#: non-zero input as true, the way the compiler coerces it.
SEMANTICS = {
    "add": lambda p, v: v[0] + v[1],
    "subtract": lambda p, v: v[0] - v[1],
    "multiply": lambda p, v: v[0] * v[1],
    "divide": lambda p, v: v[0] / v[1],
    "and": lambda p, v: _truth(all(x != 0 for x in v)),
    "or": lambda p, v: _truth(any(x != 0 for x in v)),
    "nand": lambda p, v: _truth(not all(x != 0 for x in v)),
    "xor": lambda p, v: _truth(sum(x != 0 for x in v) == 1),
    "not": lambda p, v: _truth(v[0] == 0),
    "compare": lambda p, v: _truth(COMPARISONS[p["op"]](v[0], v[1])),
}
PINS = {"add": "ab", "subtract": "ab", "multiply": "ab", "divide": "ab",
        "and": "abcd", "or": "abcd", "nand": "abcd", "xor": "ab",
        "not": ["in"], "compare": "ab"}


def _random_diagram(rng, size):
    d = Diagram(name="random")
    graph = {}      # block id -> (type, params, [input ids])
    pool = []
    for name in ANALOG + BINARY:
        bid = "p" + name
        d.add(Block(id=bid, type="point_in",
                    params={"point": name,
                            "ptype": "AV" if name in ANALOG else "BV"}))
        graph[bid] = ("point", {"point": name}, [])
        pool.append(bid)
    for i in range(2):
        bid = "k%d" % i
        value = rng.choice([-3.0, -1.5, -0.5, 0.5, 1.0, 2.0, 4.0])
        d.add(Block(id=bid, type="constant", params={"value": value}))
        graph[bid] = ("constant", {"value": value}, [])
        pool.append(bid)
    for i in range(size):
        btype = rng.choice(sorted(SEMANTICS))
        pins = list(PINS[btype])
        if btype in ("and", "or", "nand"):
            pins = pins[:rng.randint(2, 4)]
        params = {}
        if btype == "compare":
            params["op"] = rng.choice(sorted(COMPARISONS))
        # Prefer recent blocks, so expressions nest deeply.
        inputs = [rng.choice(pool[-6:] if rng.random() < 0.7 else pool)
                  for _ in pins]
        bid = "b%d" % i
        label = "L%d" % i if rng.random() < 0.2 else ""
        d.add(Block(id=bid, type=btype, label=label, params=params))
        for pin, src in zip(pins, inputs):
            d.connect(src, "out", bid, pin)
        graph[bid] = (btype, params, inputs)
        pool.append(bid)
    d.add(Block(id="w", type="assign", params={"point": "OUT"}))
    d.connect(pool[-1], "out", "w", "in")
    return d, graph, pool[-1]


def _evaluate(graph, root, values):
    memo = {}

    def value(bid):
        if bid not in memo:
            btype, params, inputs = graph[bid]
            if btype == "point":
                memo[bid] = values[params["point"]]
            elif btype == "constant":
                memo[bid] = params["value"]
            else:
                args = [value(i) for i in inputs]
                if btype == "divide" and abs(args[1]) < 1e-6:
                    raise ZeroDivisionError
                memo[bid] = SEMANTICS[btype](params, args)
        return memo[bid]

    return value(root)


def _inputs(rng):
    # Quarter steps keep sums and products exact in binary, so a
    # comparison never flips on rounding the two sides do differently.
    out = {n: rng.randint(-16, 16) / 4.0 for n in ANALOG}
    out.update({n: float(rng.randint(0, 1)) for n in BINARY})
    return out


@pytest.mark.parametrize("seed", range(40))
def test_random_diagrams_compute_what_they_draw(seed):
    rng = random.Random(seed)
    checked = 0
    for _ in range(8):
        diagram, graph, root = _random_diagram(rng, rng.randint(2, 9))
        try:
            text, _warnings = compile_diagram(diagram)
        except BlockError:
            continue        # over the statement limits: not this test's job
        program = parser.parse(text)
        assert not program.errors, text
        for _ in range(4):
            values = _inputs(rng)
            try:
                expected = _evaluate(graph, root, values)
            except ZeroDivisionError:
                continue
            sim = Simulator(program)
            sim.panel.load(values)
            sim.run(passes=2)
            got = sim.panel.value("OUT")
            assert math.isclose(got, expected, rel_tol=1e-9, abs_tol=1e-9), (
                "inputs %s: diagram gives %r, compiled PPCL gives %r\n%s"
                % (values, expected, got, text))
            checked += 1
    assert checked, "no diagram in this seed compiled"


def _diagram(blocks, wires):
    d = Diagram(name="t")
    for bid, btype, params in blocks:
        d.add(Block(id=bid, type=btype, params=dict(params)))
    for src, dst, pin in wires:
        d.connect(src, "out", dst, pin)
    return d


def test_xor_takes_two_inputs_and_says_so():
    """Chained, a three-input XOR is parity, not "exactly one"."""
    d = _diagram(
        [("a", "point_in", {"point": "A"}), ("b", "point_in", {"point": "B"}),
         ("c", "point_in", {"point": "C"}), ("x", "xor", {}),
         ("w", "assign", {"point": "OUT"})],
        [("a", "x", "a"), ("b", "x", "b"), ("c", "x", "c"), ("x", "w", "in")])
    with pytest.raises(BlockError) as err:
        compile_diagram(d)
    assert "its inputs are a, b" in err.value.message


def test_a_falling_scale_keeps_a_compound_input_whole():
    """Scale writes (low - x) for a falling slope; x = Y+Z must stay (Y+Z)."""
    d = _diagram(
        [("y", "point_in", {"point": "Y"}), ("z", "point_in", {"point": "Z"}),
         ("s", "add", {}),
         ("sc", "scale", {"in_low": 0.0, "in_high": 10.0, "out_low": 100.0,
                          "out_high": 0.0}),
         ("w", "assign", {"point": "OUT"})],
        [("y", "s", "a"), ("z", "s", "b"), ("s", "sc", "in"), ("sc", "w", "in")])
    text, _ = compile_diagram(d)
    sim = Simulator(parser.parse(text))
    sim.panel.load({"Y": 2.0, "Z": 3.0})
    sim.run(passes=2)
    # 0..10 maps to 100..0, so Y+Z = 5 is 50.
    assert sim.panel.value("OUT") == pytest.approx(50.0), text
