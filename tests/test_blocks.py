"""Tests for the block diagram compiler.

The important ones are the property tests at the bottom. Like the sequence
compiler's, they assert things about *every* compiled program rather than
comparing against a fixed string, so the guarantees survive a change to the
generated layout.
"""

import pytest

from ppcl import blocks, linter, spec
from ppcl import parser as ppcl_parser
from ppcl.blocks import Block, BlockError, Diagram
from ppcl.diagnostics import Severity


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def diagram(*specs, **kw):
    """Build a diagram from ``(id, type, params)`` triples."""
    d = Diagram(name=kw.pop("name", "test"), **kw)
    for entry in specs:
        block_id, block_type = entry[0], entry[1]
        params = entry[2] if len(entry) > 2 else {}
        label = entry[3] if len(entry) > 3 else ""
        d.add(Block(id=block_id, type=block_type, label=label,
                    params=dict(params)))
    return d


def compile_text(d):
    text, _warnings = blocks.compile_diagram(d)
    return text


def statements(text):
    """Executable statement bodies, comments dropped."""
    program = ppcl_parser.parse(text)
    return [ln.body for ln in program.lines if not ln.is_comment]


def simple():
    """A minimal but complete diagram: read a point, command another."""
    d = diagram(
        ("t", "point_in", {"point": "MAT"}),
        ("c", "constant", {"value": 38}),
        ("cmp", "compare", {"op": "<"}),
        ("out", "command", {"point": "SFAN", "action": "off"}),
    )
    d.connect("t", "out", "cmp", "a")
    d.connect("c", "out", "cmp", "b")
    d.connect("cmp", "out", "out", "when")
    return d


# --------------------------------------------------------------------------
# The catalog
# --------------------------------------------------------------------------


def test_every_block_declares_a_known_category():
    for name, bt in blocks.CATALOG.items():
        assert bt.category in blocks.CATEGORIES, name


def test_every_block_has_help_or_a_summary():
    for name, bt in blocks.CATALOG.items():
        assert bt.summary, name


def test_every_emitted_command_exists_in_the_spec():
    """A block may not claim to emit a command the language does not have."""
    for name, bt in blocks.CATALOG.items():
        for command in bt.emits:
            assert command in spec.ALL, "%s emits unknown %s" % (name, command)


def test_catalog_payload_is_json_shaped():
    payload = blocks.catalog_payload()
    assert payload["categories"] == blocks.CATEGORIES
    entry = payload["blocks"]["latch"]
    assert entry["stateful"] is True
    assert [p["name"] for p in entry["inputs"]] == ["set", "reset"]


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------


def test_an_input_accepts_only_one_wire():
    d = simple()
    d.connect("c", "out", "cmp", "a")           # replaces the MAT wire
    landing = [w for w in d.wires if w.dst == "cmp" and w.dst_pin == "a"]
    assert len(landing) == 1
    assert landing[0].src == "c"


def test_removing_a_block_removes_its_wires():
    d = simple()
    d.remove("cmp")
    assert not [w for w in d.wires if "cmp" in (w.src, w.dst)]


def test_json_round_trip():
    d = simple()
    again = blocks.loads(blocks.dumps(d))
    assert blocks.to_dict(again) == blocks.to_dict(d)


# --------------------------------------------------------------------------
# Compilation basics
# --------------------------------------------------------------------------


def test_pure_logic_costs_no_extra_statements():
    """An AND of two comparisons is one expression, not three statements."""
    d = diagram(
        ("a", "point_in", {"point": "MAT"}),
        ("b", "point_in", {"point": "OAT"}),
        ("k1", "constant", {"value": 38}),
        ("k2", "constant", {"value": 50}),
        ("c1", "compare", {"op": "<"}),
        ("c2", "compare", {"op": ">"}),
        ("and1", "and"),
        ("out", "command", {"point": "SFAN", "action": "off"}),
    )
    d.connect("a", "out", "c1", "a")
    d.connect("k1", "out", "c1", "b")
    d.connect("b", "out", "c2", "a")
    d.connect("k2", "out", "c2", "b")
    d.connect("c1", "out", "and1", "a")
    d.connect("c2", "out", "and1", "b")
    d.connect("and1", "out", "out", "when")

    body = statements(compile_text(d))
    commanding = [s for s in body if "OFF(" in s]
    assert len(commanding) == 1
    assert ".AND." in commanding[0]
    assert "LOCAL" not in "".join(body)


def test_naming_a_block_gives_it_a_local_you_can_watch():
    d = simple()
    d.block("cmp").label = "COLD"
    text = compile_text(d)
    assert 'LOCAL("COLD")' in text
    assert '"$COLD"' in text


def test_a_value_used_twice_becomes_a_local():
    d = diagram(
        ("t", "point_in", {"point": "MAT"}),
        ("k", "constant", {"value": 38}),
        ("cmp", "compare", {"op": "<"}),
        ("f", "command", {"point": "SFAN", "action": "off"}),
        ("g", "command", {"point": "RFAN", "action": "off"}),
    )
    d.connect("t", "out", "cmp", "a")
    d.connect("k", "out", "cmp", "b")
    d.connect("cmp", "out", "f", "when")
    d.connect("cmp", "out", "g", "when")
    text = compile_text(d)
    assert "LOCAL(" in text


def test_not_compiles_to_a_comparison_and_no_statement():
    d = diagram(
        ("p", "point_in", {"point": "ALARM1", "kind": "digital"}),
        ("n", "not"),
        ("out", "command", {"point": "SFAN", "action": "on"}),
    )
    d.connect("p", "out", "n", "in")
    d.connect("n", "out", "out", "when")
    body = statements(compile_text(d))
    # ONPWRT, the command itself, and the closing GOTO. NOT costs no line.
    assert len(body) == 3
    assert any("ALARM1.EQ.0.0" in s for s in body)


def test_latch_emits_set_and_clear_and_holds_state():
    d = diagram(
        ("t", "point_in", {"point": "MAT"}),
        ("lo", "constant", {"value": 38}),
        ("hi", "constant", {"value": 45}),
        ("trip", "compare", {"op": "<"}),
        ("clear", "compare", {"op": ">"}),
        ("l", "latch", {}, "FRZ"),
        ("out", "command", {"point": "SFAN", "action": "off"}),
    )
    d.connect("t", "out", "trip", "a")
    d.connect("lo", "out", "trip", "b")
    d.connect("t", "out", "clear", "a")
    d.connect("hi", "out", "clear", "b")
    d.connect("trip", "out", "l", "set")
    d.connect("clear", "out", "l", "reset")
    d.connect("l", "out", "out", "when")
    text = compile_text(d)
    assert '"$FRZ" = 1.0' in text
    assert '"$FRZ" = 0.0' in text


def test_clamp_emits_max_then_min_and_does_not_trip_the_linter():
    """MAX to a floor then MIN to a ceiling is the standard PPCL clamp."""
    d = diagram(
        ("p", "point_in", {"point": "OAT"}),
        ("c", "limit", {"low": 20, "high": 100}),
        ("w", "assign", {"point": "OADMPR"}),
    )
    d.connect("p", "out", "c", "in")
    d.connect("c", "out", "w", "in")
    text = compile_text(d)
    program = ppcl_parser.parse(text)
    findings = [
        d_ for d_ in linter.lint(program)
        if d_.severity in (Severity.ERROR, Severity.WARNING)
    ]
    assert findings == [], [f.message for f in findings]


def test_falling_scale_never_emits_an_operator_pair():
    d = diagram(
        ("p", "point_in", {"point": "OAT"}),
        ("s", "scale", {"in_low": 30, "in_high": 60,
                        "out_low": 100, "out_high": 20}),
        ("w", "assign", {"point": "OADMPR"}),
    )
    d.connect("p", "out", "s", "in")
    d.connect("s", "out", "w", "in")
    text = compile_text(d)
    assert "*-" not in text
    assert "+-" not in text


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_a_combinational_cycle_is_refused_and_names_the_blocks():
    d = diagram(
        ("p", "point_in", {"point": "X"}),
        ("a", "and"),
        ("n", "not"),
        ("o", "command", {"point": "Y", "action": "on"}),
    )
    d.connect("p", "out", "a", "a")
    d.connect("n", "out", "a", "b")
    d.connect("a", "out", "n", "in")
    d.connect("a", "out", "o", "when")
    with pytest.raises(BlockError) as excinfo:
        compile_text(d)
    assert "loop" in excinfo.value.message
    assert "Latch" in excinfo.value.message


def test_a_cycle_through_a_latch_is_allowed_and_reported():
    d = diagram(
        ("p", "point_in", {"point": "TRIP", "kind": "digital"}),
        ("l", "latch", {}, "HOLD"),
        ("n", "not"),
        ("o", "command", {"point": "SFAN", "action": "on"}),
    )
    d.connect("p", "out", "l", "set")
    d.connect("n", "out", "l", "reset")
    d.connect("l", "out", "n", "in")
    d.connect("l", "out", "o", "when")
    text, warnings = blocks.compile_diagram(d)
    assert "previous pass" in " ".join(warnings)
    assert "Feedback paths" in text


def test_a_diagram_that_commands_nothing_is_refused():
    d = diagram(("p", "point_in", {"point": "X"}))
    with pytest.raises(BlockError) as excinfo:
        compile_text(d)
    assert "commands a point" in excinfo.value.message


def test_an_unwired_required_input_is_refused_by_name():
    d = diagram(
        ("p", "point_in", {"point": "X"}),
        ("cmp", "compare", {"op": ">"}),
        ("o", "command", {"point": "Y", "action": "on"}),
    )
    d.connect("p", "out", "cmp", "a")
    d.connect("cmp", "out", "o", "when")
    with pytest.raises(BlockError) as excinfo:
        compile_text(d)
    assert "not wired" in excinfo.value.message
    assert excinfo.value.block_id == "cmp"


def test_running_out_of_locals_says_what_to_do_about_it():
    d = Diagram(name="too many")
    d.add(Block(id="src", type="point_in", params={"point": "OAT"}))
    for i in range(blocks.MAX_LOCALS + 2):
        d.add(Block(id="c%d" % i, type="limit", label="W%d" % i,
                    params={"low": 0, "high": 100}))
        d.connect("src", "out", "c%d" % i, "in")
    d.add(Block(id="w", type="assign", params={"point": "Y"}))
    d.connect("c0", "out", "w", "in")
    with pytest.raises(BlockError) as excinfo:
        compile_text(d)
    assert "local variables" in excinfo.value.message
    assert "split the diagram" in excinfo.value.message


def test_more_than_sixteen_locals_are_split_across_LOCAL_statements():
    """Sixteen names per LOCAL statement is a compiler limit, not a ceiling.

    The Insight Program Editor help is explicit: "A program can have an
    unlimited number of local points, however, a statement can only reference
    up to 16 local points at a time."  So a diagram that needs seventeen is
    legal PPCL and must compile, declaring them across two statements.
    """
    wanted = blocks.LOCALS_PER_STATEMENT + 1
    d = Diagram(name="many locals")
    d.add(Block(id="src", type="point_in", params={"point": "OAT"}))
    for i in range(wanted):
        d.add(Block(id="c%d" % i, type="limit", label="W%d" % i,
                    params={"low": 0, "high": 100}))
        d.connect("src", "out", "c%d" % i, "in")
    d.add(Block(id="w", type="assign", params={"point": "Y"}))
    d.connect("c0", "out", "w", "in")

    text = compile_text(d)
    groups = [line.split("LOCAL(", 1)[1].rsplit(")", 1)[0]
              for line in text.split("\n") if "LOCAL(" in line]
    assert len(groups) == 2, "expected the declarations to be split in two"
    names = []
    for group in groups:
        parts = [p.strip().strip('"') for p in group.split(",")]
        assert len(parts) <= blocks.LOCALS_PER_STATEMENT
        names.extend(parts)
    assert len(names) == wanted
    assert len(set(names)) == wanted, "declared a name twice"


def test_a_reset_schedule_with_descending_breakpoints_is_refused():
    d = diagram(
        ("p", "point_in", {"point": "OAT"}),
        ("r", "reset", {"points": "60,55\n0,95"}),
        ("w", "assign", {"point": "DASP"}),
    )
    d.connect("p", "out", "r", "in")
    d.connect("r", "out", "w", "in")
    with pytest.raises(BlockError) as excinfo:
        compile_text(d)
    assert "ascend" in excinfo.value.message


def test_arc_is_rejected_with_the_correct_name():
    d = diagram(
        ("p", "point_in", {"point": "X"}),
        ("f", "function", {"fn": "ARC"}),
        ("w", "assign", {"point": "Y"}),
    )
    d.connect("p", "out", "f", "in")
    d.connect("f", "out", "w", "in")
    with pytest.raises(BlockError) as excinfo:
        compile_text(d)
    assert "ATN, not ARC" in excinfo.value.message


# --------------------------------------------------------------------------
# Properties that must hold for every compiled diagram
# --------------------------------------------------------------------------


def all_example_diagrams():
    """Diagrams covering every block type at least once."""
    from ppcl.web.api_ide import _starter_diagram

    out = [simple(), _starter_diagram()]

    d = diagram(
        ("pv", "point_in", {"point": "DAT"}),
        ("oat", "point_in", {"point": "OAT"}),
        ("rst", "reset", {"points": "0,95\n60,55"}, "DASP"),
        ("pid", "pid", {"action": "reverse", "throttling": 10}, "HTG"),
        ("avg", "average", {"sample": 60, "samples": 10}),
        ("db", "deadband", {"low": 68, "high": 72, "sense": "1"}),
        ("dly", "delay", {"seconds": 120, "mode": "11"}),
        ("sel", "select"),
        ("k1", "constant", {"value": 55}),
        ("k2", "constant", {"value": 65}),
        ("mm", "extreme", {"which": "MIN"}),
        ("sc", "scale", {"in_low": 0, "in_high": 100,
                         "out_low": 0, "out_high": 10}),
        ("add", "add"),
        ("sub", "subtract"),
        ("mul", "multiply"),
        ("div", "divide"),
        ("lim", "limit", {"low": 0, "high": 100}),
        ("fn", "function", {"fn": "SQRT"}),
        ("x", "xor"),
        ("nd", "nand"),
        ("orb", "or"),
        ("btw", "between", {"low": 60, "high": 80}),
        ("res", "resident", {"which": "TIME"}),
        ("alm", "alarm", {"point": "DATALM", "action": "disable"}),
        ("note", "note", {"text": "example"}),
        ("cmd", "command", {"point": "HVLV", "action": "set"}),
        ("wr", "assign", {"point": "CALC"}),
    )
    d.connect("oat", "out", "rst", "in")
    d.connect("pv", "out", "pid", "pv")
    d.connect("rst", "out", "pid", "sp")
    d.connect("pid", "out", "cmd", "in")
    d.connect("pv", "out", "avg", "in")
    d.connect("avg", "out", "db", "in")
    d.connect("db", "out", "dly", "in")
    d.connect("dly", "out", "sel", "sel")
    d.connect("k1", "out", "sel", "a")
    d.connect("k2", "out", "sel", "b")
    d.connect("sel", "out", "mm", "a")
    d.connect("pv", "out", "mm", "b")
    d.connect("mm", "out", "sc", "in")
    d.connect("sc", "out", "add", "a")
    d.connect("k1", "out", "add", "b")
    d.connect("add", "out", "sub", "a")
    d.connect("k2", "out", "sub", "b")
    d.connect("sub", "out", "mul", "a")
    d.connect("k1", "out", "mul", "b")
    d.connect("mul", "out", "div", "a")
    d.connect("k2", "out", "div", "b")
    d.connect("div", "out", "lim", "in")
    d.connect("lim", "out", "fn", "in")
    d.connect("fn", "out", "wr", "in")
    d.connect("db", "out", "x", "a")
    d.connect("dly", "out", "x", "b")
    d.connect("db", "out", "nd", "a")
    d.connect("dly", "out", "nd", "b")
    d.connect("x", "out", "orb", "a")
    d.connect("nd", "out", "orb", "b")
    d.connect("res", "out", "btw", "in")
    d.connect("btw", "out", "alm", "when")
    d.connect("orb", "out", "cmd", "when")
    out.append(d)
    return out


def test_the_examples_cover_every_block_type():
    used = set()
    for d in all_example_diagrams():
        used.update(b.type for b in d.blocks)
    missing = set(blocks.CATALOG) - used
    assert missing == set(), "no example uses: %s" % ", ".join(sorted(missing))


@pytest.mark.parametrize("index", range(len(all_example_diagrams())))
def test_every_compiled_diagram_parses_without_error(index):
    text = compile_text(all_example_diagrams()[index])
    program = ppcl_parser.parse(text)
    assert program.errors == []


@pytest.mark.parametrize("index", range(len(all_example_diagrams())))
def test_every_compiled_diagram_has_one_backward_goto_and_it_is_last(index):
    """The one branch the Desigo compiler permits, in the only place it does."""
    text = compile_text(all_example_diagrams()[index])
    program = ppcl_parser.parse(text)
    backward = [
        ln for ln in program.lines
        if not ln.is_comment and ln.body.upper().startswith("GOTO")
        and int(ln.body.split()[1]) < ln.number
    ]
    assert len(backward) == 1
    executable = [ln for ln in program.lines if not ln.is_comment]
    assert backward[0] is executable[-1]


@pytest.mark.parametrize("index", range(len(all_example_diagrams())))
def test_no_time_based_command_is_ever_inside_an_if(index):
    """LOOP, TOD, WAIT and TIMAVG must be evaluated on every pass."""
    text = compile_text(all_example_diagrams()[index])
    program = ppcl_parser.parse(text)
    for line in program.lines:
        if line.is_comment:
            continue
        body = line.body.upper()
        if not body.startswith("IF("):
            continue
        for command in spec.TIME_BASED_COMMANDS:
            assert "%s(" % command not in body, (
                "line %d puts %s inside an IF" % (line.number, command)
            )


@pytest.mark.parametrize("index", range(len(all_example_diagrams())))
def test_no_compiled_diagram_uses_a_subroutine(index):
    """Nothing generated here needs GOSUB, so nothing can be trapped in one."""
    text = compile_text(all_example_diagrams()[index])
    assert "GOSUB" not in text.upper()


@pytest.mark.parametrize("index", range(len(all_example_diagrams())))
def test_every_compiled_diagram_declares_the_locals_it_uses(index):
    text = compile_text(all_example_diagrams()[index])
    used = set()
    for line in text.split("\n"):
        if "LOCAL(" in line:
            continue
        for chunk in line.split('"'):
            if chunk.startswith("$"):
                used.add(chunk[1:])
    declared = set()
    for line in text.split("\n"):
        if "LOCAL(" not in line:
            continue
        inner = line.split("LOCAL(", 1)[1].rsplit(")", 1)[0]
        declared.update(part.strip().strip('"') for part in inner.split(","))
    assert used <= declared, "undeclared: %s" % (used - declared)


@pytest.mark.parametrize("index", range(len(all_example_diagrams())))
def test_every_compiled_diagram_lints_without_errors(index):
    text = compile_text(all_example_diagrams()[index])
    program = ppcl_parser.parse(text)
    errors = [
        d for d in linter.lint(program) if d.severity is Severity.ERROR
    ]
    assert errors == [], [d.message for d in errors]


@pytest.mark.parametrize("index", range(len(all_example_diagrams())))
def test_no_statement_exceeds_the_operand_or_operator_limits(index):
    text = compile_text(all_example_diagrams()[index])
    program = ppcl_parser.parse(text)
    findings = [
        d for d in linter.lint(program) if d.code in ("E314", "E315")
    ]
    assert findings == [], [d.message for d in findings]


def test_a_command_with_release_writes_the_matching_release():
    """The single most common PPCL field defect, made impossible."""
    d = diagram(
        ("p", "point_in", {"point": "TRIP", "kind": "digital"}),
        ("o", "command", {"point": "SFAN", "action": "off",
                          "priority": "@EMER",
                          "release_when_false": "yes"}),
    )
    d.connect("p", "out", "o", "when")
    text = compile_text(d)
    assert "OFF(@EMER,SFAN)" in text
    assert "RELEAS(@EMER,SFAN)" in text
