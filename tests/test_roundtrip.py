"""Parse, re-render, parse again: nothing that runs may change.

``fmt --normalize`` re-renders every statement from the parse, so it is the
whole parser and the whole unparser in one pass. On every program the
repository ships -- the four real-world samples, every template, the shipped
sequence and diagram -- the normalized text must run exactly what the
original ran (the writers' own self-check says so), and normalizing it a
second time must change nothing at all.

A failure here means a construct the parser reads one way and the unparser
writes another: the kind of defect that turned "# OFF(FAN)" into a live
OFF(FAN) once.
"""

import glob
import json
import os

import pytest

from ppcl import blocks, formatter, generator, parser, sequence, textio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _programs():
    out = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "samples", "*"))):
        out["samples/" + os.path.basename(path)] = textio.read(path).text
    for name, build in generator.TEMPLATES.items():
        out["template " + name] = (build("Round trip") if name == "skeleton"
                                   else build())
    for path in sorted(glob.glob(os.path.join(ROOT, "sequences", "*.seq"))):
        text, _w = sequence.compile_sequence(
            sequence.parse(textio.read(path).text))
        out["sequences/" + os.path.basename(path)] = text
    for path in sorted(glob.glob(os.path.join(ROOT, "diagrams", "*.json"))):
        with open(path, encoding="utf-8") as fh:
            text, _w = blocks.compile_diagram(blocks.from_dict(json.load(fh)))
        out["diagrams/" + os.path.basename(path)] = text
    return out


#: The constructs that have broken a writer before, in one program: a PXC.A
#: disabled line, an UNKNOWN wrapper, a comment ending in &, a continuation,
#: DEFINE references, priorities, colon and @ names, SAMPLE, a GOTO to a
#: missing line, line lists, times. No shipped program has most of them.
CONSTRUCTS = "\n".join([
    '00005\tDEFINE(X,"AHU1.")',
    "00010\tC header ending in &",
    "00020\tLOCAL(\"T\")",
    "00030\tONPWRT(40)",
    '00040\tIF("%X%MAT".LT.38.0) THEN OFF(@EMER,"%X%SFAN") ELSE GOTO 75',
    "00050\t# ON(@OPER,FAN)",
    "00060\tUNKNOWN (FROB(1,2))",
    "00070\tSAMPLE(60) \"$T\" = DEV201:ZNT.ROOT.2.0 + @1STFLR.SP &",
    "\t* 1.5",
    "00080\tTOD(1,1,7:30,17:00,FAN)",
    "00090\tACT(40,50)",
    "00100\tIF(FAN.EQ.ON.AND.PRFON.NE.FAILED) THEN RELEAS(@EMER,\"%X%SFAN\")",
    "00110\tGOTO 40",
]) + "\n"


PROGRAMS = _programs()
PROGRAMS["constructs"] = CONSTRUCTS


def test_the_round_trip_covers_every_kind_of_shipped_program():
    kinds = {name.split("/")[0].split(" ")[0] for name in PROGRAMS}
    assert kinds == {"samples", "template", "sequences", "diagrams",
                     "constructs"}


@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_normalizing_changes_nothing_that_runs(name):
    program = parser.parse(PROGRAMS[name])
    once = formatter.format_text(program, sort=True, normalize=True)
    assert formatter.check_rewrite(program, once) == []
    again = formatter.format_text(parser.parse(once), sort=True,
                                  normalize=True)
    assert again == once
