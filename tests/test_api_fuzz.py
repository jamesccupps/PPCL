"""Every API route, every field it might read, the wrong type of value.

Two outside reviews found ordinary mistakes in a request -- a word where a
number belongs, a string where an object belongs -- answered with an
internal error and a traceback. Each was fixed where it was found; this
keeps them fixed, and catches the next route that forgets.

A 500 here is a bug: the request was wrong, and the answer should say so.
"""

import json

import pytest

from ppcl import cli, sequence
from ppcl.web import api

# api_ide's routes join the table on the first dispatch; the parameters are
# collected before that, and would otherwise cover only the core routes.
api._ensure_ide_routes()
assert len(api.ROUTES) > 30

FIELDS = [
    "text", "points", "faults", "start", "step", "passes", "day_of_week",
    "offset", "options", "point_types", "document", "values", "diagram",
    "watch", "count", "line", "index", "skip", "limit", "first", "last",
    "renames", "lines", "variables", "seconds", "dt", "query", "point",
    "expression", "op", "mode", "action", "kind", "disabled", "min_severity",
    "time", "interval", "start_hour", "firmware", "encoding", "newline",
    "name", "topic", "preset", "weather", "path",
]

#: A word, a list and an object: between them, every wrong type a field
#: that wants a number, a string, a list or an object can be handed.
WRONG = ["a", [1], {"a": 1}]


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    root = tmp_path_factory.mktemp("ws")
    (root / "programs").mkdir()
    ws = api.Workspace(str(root))
    program, _w = sequence.compile_sequence(
        sequence.parse(cli.STARTER_SEQUENCE % {"author": "A"}))
    _s, started = api.dispatch("/api/debug/start", {"text": program}, ws)
    _s, parsed = api.dispatch(
        "/api/seq/parse", {"text": cli.STARTER_SEQUENCE % {"author": "A"}}, ws)
    _s, fresh = api.dispatch("/api/blocks/new", {}, ws)
    base = {"text": program, "session": started["session"],
            "path": "programs/f.ppcl", "seconds": 60,
            "document": parsed["document"], "diagram": fresh.get("diagram", {})}
    return ws, base


@pytest.mark.parametrize("route", sorted(api.ROUTES))
def test_no_wrong_field_is_an_internal_error(setup, route):
    ws, base = setup
    failures = []
    for field in FIELDS:
        for wrong in WRONG:
            body = json.loads(json.dumps(dict(base, **{field: wrong})))
            status, reply = api.dispatch(route, body, ws)
            if status >= 500:
                failures.append("%s=%r: %s" % (field, wrong,
                                               reply.get("error", "")[:80]))
    assert not failures, "\n".join(failures)
