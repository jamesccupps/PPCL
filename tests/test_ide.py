"""Tests for the IDE layer: debugger, transforms, point database, settings.

The theme is that each of these can quietly do the wrong thing and still look
right, so the assertions are about exactness -- a transform that leaves the
rest of the file byte for byte alone, a watchpoint that names the statement
that actually wrote the point, a settings file that rejects a bad value rather
than silently using it.
"""

import json
import os

import pytest

from ppcl import helpdocs, points, settings, transforms
from ppcl import parser as ppcl_parser
from ppcl.debug import Breakpoint, DebugError, DebugSession


PROGRAM = "\n".join([
    "00010\tC freeze protection demo",
    '00020\tLOCAL("FLAG")',
    '00030\tIF(MAT.LT.38.0) THEN "$FLAG" = 1.0',
    '00040\tIF(MAT.GT.45.0) THEN "$FLAG" = 0.0',
    '00050\tIF("$FLAG".EQ.1.0) THEN OFF(@EMER,SFAN)',
    '00060\tIF("$FLAG".EQ.0.0) THEN RELEAS(@EMER,SFAN)',
    "00070\tGOTO 30",
    "",
])


# --------------------------------------------------------------------------
# Debugger
# --------------------------------------------------------------------------


def test_a_program_that_does_not_parse_is_refused_up_front():
    with pytest.raises(DebugError) as excinfo:
        DebugSession("00010\tIF(((")
    assert "does not parse" in str(excinfo.value)


def test_a_line_breakpoint_stops_before_the_line_runs():
    session = DebugSession(PROGRAM)
    session.set_point("MAT", 50.0)
    session.add_breakpoint(Breakpoint(kind="line", line=50))
    stop = session.run()
    assert stop.reason == "breakpoint"
    assert stop.line == 50


def test_a_breakpoint_on_a_comment_is_refused_with_a_reason():
    session = DebugSession(PROGRAM)
    with pytest.raises(DebugError) as excinfo:
        session.add_breakpoint(Breakpoint(kind="line", line=10))
    assert "comment" in str(excinfo.value)


def test_a_breakpoint_on_a_line_that_does_not_exist_is_refused():
    session = DebugSession(PROGRAM)
    with pytest.raises(DebugError):
        session.add_breakpoint(Breakpoint(kind="line", line=999))


def test_a_write_breakpoint_names_the_statement_that_wrote_the_point():
    session = DebugSession(PROGRAM)
    session.set_point("MAT", 30.0)
    session.add_breakpoint(Breakpoint(kind="write", point="SFAN"))
    stop = session.run()
    assert stop.reason == "breakpoint"
    assert "line 50 wrote" in stop.detail


def test_a_write_breakpoint_does_not_fire_on_a_blocked_command():
    """A command refused by priority did not write, so it must not stop."""
    session = DebugSession(PROGRAM)
    session.set_point("MAT", 30.0)
    session.set_point("SFAN", 1.0, "@OPER")     # an operator owns it
    session.add_breakpoint(Breakpoint(kind="write", point="SFAN"))
    stop = session.run(budget=4000)
    assert stop.reason == "budget"
    assert session.point("SFAN")["value"] == 1.0


def test_a_conditional_breakpoint_uses_the_panels_own_expression_grammar():
    session = DebugSession(PROGRAM)
    session.set_point("MAT", 30.0)
    session.add_breakpoint(
        Breakpoint(kind="condition", expression='"$FLAG".EQ.1.0')
    )
    stop = session.run()
    assert stop.reason == "breakpoint"
    assert session.point("$FLAG")["value"] == 1.0


def test_a_conditional_breakpoint_with_bad_syntax_is_refused_when_set():
    session = DebugSession(PROGRAM)
    with pytest.raises(DebugError):
        session.add_breakpoint(
            Breakpoint(kind="condition", expression="MAT .LT.")
        )


def test_skip_lets_a_breakpoint_be_useful_inside_a_main_loop():
    session = DebugSession(PROGRAM)
    session.set_point("MAT", 50.0)
    session.add_breakpoint(Breakpoint(kind="line", line=30, skip=3))
    stop = session.run()
    assert stop.reason == "breakpoint"
    assert session.breakpoints[0].hits == 4


def test_a_disabled_breakpoint_does_not_fire():
    session = DebugSession(PROGRAM)
    session.set_point("MAT", 50.0)
    bp = session.add_breakpoint(Breakpoint(kind="line", line=50))
    bp.enabled = False
    assert session.run(budget=200).reason == "budget"


def test_run_returns_rather_than_hanging_when_nothing_fires():
    session = DebugSession(PROGRAM)
    stop = session.run(budget=500)
    assert stop.reason == "budget"
    assert "500" in stop.detail


def test_overriding_a_point_mid_run_changes_what_happens_next():
    session = DebugSession(PROGRAM)
    session.set_point("MAT", 50.0)
    session.run(budget=400)
    assert session.point("$FLAG")["value"] == 0.0
    session.set_point("MAT", 20.0)
    session.run(budget=400)
    assert session.point("$FLAG")["value"] == 1.0


def test_stepping_advances_exactly_one_statement():
    session = DebugSession(PROGRAM)
    first = session.current_line
    session.step()
    assert session.current_line != first
    assert session.executed == 1


def test_coverage_reports_lines_that_have_never_run():
    text = "\n".join([
        "00010\tON(A)",
        "00020\tGOTO 40",
        "00030\tON(NEVER)",
        "00040\tGOTO 10",
    ])
    session = DebugSession(text)
    session.run(budget=60)
    state = session.state()
    assert 30 in state["starved"]
    assert 30 not in state["coverage"]


def test_reset_clears_execution_but_keeps_breakpoints():
    session = DebugSession(PROGRAM)
    session.add_breakpoint(Breakpoint(kind="line", line=50))
    session.run(budget=200)
    session.reset()
    assert session.executed == 0
    assert len(session.breakpoints) == 1
    assert session.breakpoints[0].hits == 0


def test_the_watch_window_reports_priority_not_just_value():
    session = DebugSession(PROGRAM)
    session.set_point("MAT", 30.0)
    session.add_watch("SFAN")
    session.run(budget=400)
    watched = session.state()["watch"][0]
    assert watched["priority"] == "@EMER"


# --------------------------------------------------------------------------
# Transforms
# --------------------------------------------------------------------------


DEFINED = "\n".join([
    "00010\tC air handler",
    "00020\tDEFINE(A01,Bld01.Ahu01)",
    '00030\tIF(SECND1.GT.15.0) THEN ON("%A01%.RAF") ELSE OFF("%A01%.RAF")',
    '00040\tIF("Bld01.Ahu01.MAT".LT.38.0) THEN OFF(@EMER,"Bld01.Ahu01.SFAN")',
    "00050\tGOTO 30",
])


def test_expanding_defines_substitutes_every_reference():
    result = transforms.expand_defines(DEFINED)
    assert "%A01%" not in result.text
    assert '"Bld01.Ahu01.RAF"' in result.text
    assert result.changed == 2


def test_collapsing_defines_leaves_the_define_statement_alone():
    expanded = transforms.expand_defines(DEFINED).text
    collapsed = transforms.collapse_defines(expanded).text
    assert "DEFINE(A01,Bld01.Ahu01)" in collapsed
    assert '"%A01%.MAT"' in collapsed


def test_a_program_with_no_defines_says_so_rather_than_doing_nothing_quietly():
    result = transforms.expand_defines("00010\tON(A)")
    assert result.changed == 0
    assert result.warnings


def test_swapping_separators_leaves_numbers_and_operators_untouched():
    result = transforms.swap_separators(DEFINED, "_")
    assert ".GT." in result.text          # the dotted operator survives
    assert "15.0" in result.text          # so does the decimal
    assert '"Bld01_Ahu01_MAT"' in result.text


def test_swapping_separators_round_trips_byte_for_byte():
    once = transforms.swap_separators(DEFINED, "_").text
    twice = transforms.swap_separators(once, ".").text
    assert twice == DEFINED


def test_a_disabled_line_becomes_a_comment_and_warns_about_loading():
    result = transforms.set_disabled(DEFINED, [40])
    assert transforms.disabled_lines(result.text) == [40]
    assert "Re-enable before loading" in " ".join(result.warnings)
    program = ppcl_parser.parse(result.text)
    assert program.by_number()[40].is_comment


def test_enabling_restores_the_original_text_exactly():
    disabled = transforms.set_disabled(DEFINED, [40]).text
    restored = transforms.set_disabled(disabled, [40], False).text
    assert restored == DEFINED


def test_cloning_renames_points_and_retargets_branches():
    result = transforms.clone_lines(
        DEFINED, 30, 50,
        {"Bld01.Ahu01.MAT": "Bld01.Ahu02.MAT",
         "Bld01.Ahu01.SFAN": "Bld01.Ahu02.SFAN"},
    )
    assert '"Bld01.Ahu02.MAT"' in result.text
    assert "GOTO 60" in result.text            # not GOTO 30
    assert ppcl_parser.parse(result.text).errors == []


def test_cloning_without_renames_warns_that_the_copy_fights_the_original():
    result = transforms.clone_lines(DEFINED, 30, 40, {})
    assert "commanding one point fight" in " ".join(result.warnings)


def test_cloning_onto_existing_line_numbers_is_refused():
    result = transforms.clone_lines(DEFINED, 30, 40, {}, start=10)
    assert result.changed == 0
    assert "already" in " ".join(result.warnings) or \
           "existing" in " ".join(result.warnings)


def test_comment_toggling_decides_by_majority_and_reverses():
    once = transforms.toggle_comments(DEFINED, [30, 40]).text
    assert ppcl_parser.parse(once).by_number()[30].is_comment
    twice = transforms.toggle_comments(once, [30, 40]).text
    assert twice == DEFINED


# --------------------------------------------------------------------------
# Point database
# --------------------------------------------------------------------------


CSV = (
    "Point Name,Point Type,Description,Units,Present Value\n"
    "AHU1.MAT,LAI,Mixed air temperature,DEG F,54.2\n"
    "AHU1.SFAN,LOOAP,Supply fan,,ON\n"
    "AHU1.HVLV,LAO,Heating valve,PCT,0\n"
)


def test_csv_import_maps_columns_by_alias_not_position():
    db = points.load_csv(CSV, source="test.csv")
    assert len(db) == 3
    record = db.lookup("AHU1.MAT")
    assert record.ptype == "LAI"
    assert record.units == "DEG F"
    assert record.value == 54.2
    assert record.kind == "analog"


def test_a_named_state_imports_as_a_number():
    db = points.load_csv(CSV)
    assert db.lookup("AHU1.SFAN").value == 1.0


def test_a_file_with_no_recognisable_name_column_says_what_it_looked_for():
    with pytest.raises(ValueError) as excinfo:
        points.load_csv("alpha,beta\n1,2\n")
    assert "point-name column" in str(excinfo.value)
    assert "alpha" in str(excinfo.value)


def test_unrecognised_columns_are_reported_rather_than_dropped_silently():
    db = points.load_csv(CSV.replace("Units", "Widget"))
    assert "Widget" in db.ignored_columns


def test_a_short_name_resolves_against_a_qualified_reference():
    db = points.load_csv("Name,Type\nMAT,LAI\n")
    assert db.lookup("Bld01.Ahu01.MAT") is not None
    assert db.lookup('"MAT"') is not None


def test_unresolved_finds_the_missing_point_and_ignores_the_rest():
    db = points.load_csv("Name,Type\nSFAN,LOOAP\n")
    program = ppcl_parser.parse(
        '00010\tLOCAL("FLAG")\n'
        '00020\tIF(TIME.GT.6:00) THEN ON(SFAN)\n'
        '00030\tIF(MISSING.GT.1.0) THEN "$FLAG" = 1.0\n'
    )
    missing = db.unresolved(program)
    names = [row["name"] for row in missing]
    assert "MISSING" in names
    assert "TIME" not in names               # resident
    assert not any(n.startswith("$") for n in names)   # locals


def test_json_import_accepts_a_bare_list_and_a_wrapped_object():
    rows = [{"name": "MAT", "type": "LAI"}]
    assert len(points.load_json(json.dumps(rows))) == 1
    assert len(points.load_json(json.dumps({"points": rows}))) == 1


def test_load_decides_the_format_from_the_content():
    assert len(points.load(CSV)) == 3
    assert len(points.load('[{"name":"X"}]')) == 1


def test_search_puts_prefix_matches_first():
    db = points.load_csv(
        "Name,Type\nZONE_TEMP,LAI\nAHU1.ZONE,LAI\nZONE_SP,LAI\n"
    )
    found = [r.name for r in db.search("ZONE")]
    assert found[:2] == ["ZONE_SP", "ZONE_TEMP"]


def test_types_feeds_the_linters_type_rules():
    db = points.load_csv(CSV)
    assert db.types()["AHU1.HVLV"] == "LAO"


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def test_defaults_cover_every_declared_setting():
    values = settings.defaults()
    assert set(values) == {s.key for s in settings.SETTINGS}


def test_a_value_out_of_range_is_rejected_with_the_limit_named():
    with pytest.raises(ValueError) as excinfo:
        settings.coerce("line_step", 99999)
    assert "at most" in str(excinfo.value)


def test_an_unknown_firmware_is_rejected_rather_than_accepted():
    with pytest.raises(ValueError):
        settings.coerce("firmware", "not-a-panel")


def test_a_list_setting_accepts_a_comma_separated_string():
    assert settings.coerce("disabled_rules", "S601, P707") == ["S601", "P707"]


def test_settings_round_trip_through_a_file(tmp_path):
    store = settings.Settings(str(tmp_path))
    assert store.update({"line_step": 20, "theme": "light"}) == []
    store.save()
    again = settings.Settings(str(tmp_path)).load()
    assert again.get("line_step") == 20
    assert again.get("theme") == "light"


def test_a_broken_settings_file_falls_back_to_defaults_and_says_so(tmp_path):
    path = os.path.join(str(tmp_path), settings.FILENAME)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{ this is not json")
    store = settings.Settings(str(tmp_path)).load()
    assert store.get("line_step") == 10
    assert store.problems


def test_an_unknown_key_is_kept_but_reported(tmp_path):
    path = os.path.join(str(tmp_path), settings.FILENAME)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"from_a_newer_version": 1}, fh)
    store = settings.Settings(str(tmp_path)).load()
    assert store.values["from_a_newer_version"] == 1
    assert "from_a_newer_version" in " ".join(store.problems)


def test_update_rejects_a_bad_value_without_changing_anything():
    store = settings.Settings(".")
    rejected = store.update({"font_size": 900})
    assert rejected
    assert store.get("font_size") == 13


# --------------------------------------------------------------------------
# Help
# --------------------------------------------------------------------------


def test_every_help_page_has_a_body_and_a_known_category():
    categories = {group["category"] for group in helpdocs.contents()}
    for entry in helpdocs.PAGES:
        assert entry["body"].strip()
        assert entry["summary"]
        assert entry["category"] in categories


def test_every_see_also_reference_resolves():
    for entry in helpdocs.PAGES:
        for ref in entry.get("see_also", []):
            assert ref in helpdocs.BY_ID, "%s -> %s" % (entry["id"], ref)


def test_search_finds_a_page_by_its_body_and_returns_an_excerpt():
    results = helpdocs.search("RELEAS")
    assert results
    assert any(hit["id"] == "priority" for hit in results)
    assert all(hit["excerpt"] for hit in results)


def test_an_unknown_topic_returns_nothing_rather_than_raising():
    assert helpdocs.page("no-such-topic") is None


# --------------------------------------------------------------------------
# The IDE endpoints
# --------------------------------------------------------------------------


@pytest.fixture
def ws(tmp_path):
    from ppcl.web import api

    (tmp_path / "programs").mkdir()
    (tmp_path / "programs" / "a.ppcl").write_text(
        "00010\tON(SFAN)\n00020\tGOTO 10\n", encoding="utf-8"
    )
    return api.Workspace(str(tmp_path))


def call(path, body=None, ws=None):
    from ppcl.web import api

    return api.dispatch(path, body or {}, ws)


def test_every_ide_route_is_reachable_through_dispatch(ws):
    """No endpoint may be missing from the table or blow up on a bare call.

    A session-scoped debug endpoint answers 404 for an unknown session, which
    is correct, so the assertion is on the reason rather than the status.
    """
    from ppcl.web.api_ide import IDE_ROUTES

    for path in IDE_ROUTES:
        status, payload = call(path, {"text": "00010\tON(A)"}, ws)
        assert "no such endpoint" not in str(payload.get("error", "")), (
            "%s is not routed" % path
        )
        assert "traceback" not in payload, "%s crashed: %s" % (path, payload)


def test_the_route_table_stays_patchable(ws):
    """A merged copy would make ROUTES no longer the routing table."""
    from ppcl.web import api

    api.dispatch("/api/meta", {}, ws)          # force the deferred merge
    assert "/api/blocks/catalog" in api.ROUTES


def test_the_starter_diagram_compiles_clean(ws):
    status, new = call("/api/blocks/new", {}, ws)
    assert status == 200
    status, out = call("/api/blocks/compile", {"diagram": new["diagram"]}, ws)
    assert out["ok"], out.get("error")
    assert out["counts"]["error"] == 0
    assert out["counts"]["warning"] == 0
    # The example exists to demonstrate the released interlock.
    assert "RELEAS(@EMER,SFAN)" in out["text"]


def test_a_broken_diagram_names_the_block_so_the_ui_can_mark_it(ws):
    status, out = call("/api/blocks/compile", {
        "diagram": {
            "name": "t",
            "blocks": [
                {"id": "pid", "type": "pid", "params": {}},
                {"id": "w", "type": "assign", "params": {"point": "X"}},
            ],
            "wires": [{"src": "pid", "src_pin": "out",
                       "dst": "w", "dst_pin": "in"}],
        },
    }, ws)
    assert out["ok"] is False
    assert out["block"] == "pid"
    assert "not wired" in out["error"]


def test_completion_offers_commands_in_siemens_own_categories(ws):
    status, out = call("/api/complete",
                       {"text": "00010\tDBSW", "offset": 11}, ws)
    names = [item["text"] for item in out["items"]]
    assert "DBSWIT" in names
    assert out["items"][0]["category"] == "Special Function"


def test_completion_inside_a_call_reports_the_argument_being_typed(ws):
    status, out = call("/api/complete",
                       {"text": "00010\tLOOP(128,DAT,", "offset": 19}, ws)
    signature = out["signature"]
    assert signature["name"] == "LOOP"
    assert signature["argument"] == 2
    assert signature["params"][2]["name"] == "cv"
    assert signature["time_based"] is True


def test_completion_offers_real_point_names_once_a_database_is_loaded(ws):
    call("/api/points/import",
         {"text": "Name,Type\nAHU1.MIXEDAIR,LAI\n"}, ws)
    status, out = call("/api/complete",
                       {"text": "00010\tON(MIXE", "offset": 13}, ws)
    assert any(item["kind"] == "point" for item in out["items"])


def test_a_debug_session_expires_cleanly_rather_than_crashing(ws):
    status, payload = call("/api/debug/state", {"session": "nope"}, ws)
    assert status == 404
    assert "expired" in payload["error"]


def test_a_debug_session_runs_and_reports_a_blocked_command(ws):
    program = "\n".join([
        '00010\tIF(MAT.LT.38.0) THEN OFF(@EMER,SFAN)',
        '00020\tIF(MAT.GT.45.0) THEN ON(SFAN)',
        "00030\tGOTO 10",
    ])
    status, started = call("/api/debug/start",
                           {"text": program, "points": {"MAT": 30.0}}, ws)
    assert started["ok"]
    session = started["session"]
    call("/api/debug/point",
         {"session": session, "action": "watch", "name": "SFAN"}, ws)
    call("/api/debug/step", {"session": session, "mode": "run"}, ws)
    call("/api/debug/point",
         {"session": session, "action": "set", "name": "MAT", "value": 60}, ws)
    status, stepped = call("/api/debug/step",
                           {"session": session, "mode": "run"}, ws)
    state = stepped["state"]
    assert state["watch"][0]["priority"] == "@EMER"
    # ON(SFAN) at @NONE cannot beat @EMER, and the trace has to say so.
    assert any("blocked" in text.lower() for text in state["blocked"])


def test_settings_reject_a_bad_value_and_say_which_limit(ws):
    status, out = call("/api/settings/save", {"values": {"font_size": 900}}, ws)
    assert out["ok"] is False
    assert any("at most" in message for message in out["rejected"])


def test_importing_points_turns_on_unresolved_marking(ws):
    call("/api/points/import", {"text": "Name,Type\nSFAN,LOOAP\n"}, ws)
    status, out = call("/api/points/check",
                       {"text": "00010\tON(SFAN)\n00020\tON(GHOST)\n"}, ws)
    assert out["loaded"]
    assert [row["name"] for row in out["unresolved"]] == ["GHOST"]


def test_a_transform_reports_what_it_changed(ws):
    status, out = call("/api/transform", {
        "op": "expand_defines",
        "text": '00010\tDEFINE(A,B.C)\n00020\tON("%A%.D")\n',
    }, ws)
    assert out["ok"]
    assert '"B.C.D"' in out["text"]
    assert out["notes"]


def test_an_unknown_transform_lists_the_ones_that_exist(ws):
    status, payload = call("/api/transform",
                           {"op": "nonsense", "text": "00010\tON(A)"}, ws)
    assert status == 400
    assert "expand_defines" in payload["error"]


def test_help_pages_and_search_are_served(ws):
    status, out = call("/api/help", {"topic": "priority"}, ws)
    assert out["page"]["title"] == "Point priority"
    status, found = call("/api/help", {"query": "RELEAS"}, ws)
    assert any(hit["id"] == "priority" for hit in found["results"])


def test_meta_publishes_the_command_categories_the_ui_filters_by(ws):
    status, meta = call("/api/meta", {}, ws)
    assert "Energy Management" in meta["command_categories"]
    assert "LOOP" in meta["command_categories"]["Energy Management"]
    assert meta["limits"]["locals"] == 16


# -- panel error codes -----------------------------------------------------


def test_panel_error_lookup_tells_compiler_from_runtime():
    """The distinction is the point: R-codes refuse the line, E-codes do not.

    A rule that predicts an R-code is saying "this will not load". One that
    predicts an E-code is saying "this will load and then not work", which is
    the more dangerous of the two.
    """
    from ppcl import spec

    kind, text, _why = spec.panel_error("R5")
    assert kind == "compiler"
    assert "control statement" in text.lower()

    kind, text, why = spec.panel_error("E4")
    assert kind == "runtime"
    assert text == "Priority too low"
    assert "never released" in why

    assert spec.panel_error("e12")[0] == "runtime"      # case insensitive
    assert spec.panel_error("W999") is None             # not a panel code


def test_the_error_tables_are_well_formed():
    from ppcl import spec

    for code, entry in spec.PPCL_COMPILER_ERRORS.items():
        assert code.startswith("R") and len(entry) == 2, code
    for code, entry in spec.PANEL_RUNTIME_ERRORS.items():
        assert code.startswith("E") and len(entry) == 3, code
        assert entry[0].startswith("0x"), code
    # R4 and R12 are genuinely absent from the manual; do not invent them.
    assert "R4" not in spec.PPCL_COMPILER_ERRORS
    assert "R12" not in spec.PPCL_COMPILER_ERRORS


def test_the_word_form_comparisons_are_reserved():
    """``EQUAL`` and ``LESS`` are reserved names, not operators.

    Neither has a documented syntax -- no manual shows ``A EQUAL B`` -- but
    both sit in the published reserved-word lists, so neither may be a point
    name. ``LESS`` is the harder one: it is in the Program Editor's shipped
    list, alphabetically between ``LE`` and ``LINK``, and missing from
    125-1896 Rev. 5 Chapter 5, which goes straight from ``LE`` to ``LINK``.
    One source reserves it and none contradicts, so it is reserved here.
    """
    from ppcl import spec

    assert "EQUAL" in spec.RESERVED_WORDS
    assert "LESS" in spec.RESERVED_WORDS
    for word in ("EQ", "NE", "LT", "LE", "GT", "GE"):
        assert word in spec.RESERVED_WORDS, word
    # Reserved, but not operators: the parser must not accept them as one.
    assert "LESS" not in spec.DOTTED_OPS
    assert ".LESS." not in spec.DOTTED_OPS


def test_no_runtime_string_hardcodes_a_count_the_code_can_compute():
    """A number in prose decays silently. Nothing fails, nobody notices.

    The MCP tool description told every agent the linter checked "75 rules"
    while it checked 88, and the in-app help said "72 lint rules". Both were
    written by hand and both went stale, in exactly the places a person or a
    model would trust them: the text an agent reads before deciding whether to
    run the linter, and the page a user opens to learn what the tool is.

    Both now ask. This stops the next one being written.
    """
    import pathlib
    import re

    pattern = re.compile(
        r"[^%d](" + r"[0-9]{2,3})" + r"[ ]+(lint rules|rules|commands|"
        r"point types|block types|help pages|settings)" + r"[^a-z]")
    offenders = []
    for path in sorted(pathlib.Path("ppcl").rglob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue          # a comment is a note to a reader, not output
            m = pattern.search(line)
            if m:
                offenders.append("%s:%d %s" % (path, n, stripped[:70]))
    assert offenders == [], (
        "counts written by hand in runtime text:" + chr(10)
        + chr(10).join(offenders))


def test_the_help_and_the_mcp_surface_report_the_real_rule_count():
    import ppcl.linter as L
    from ppcl import helpdocs
    from ppcl.mcp_server import TOOLS

    L._load_rules()
    live = str(len(L.REGISTRY))

    lint_tool = next(t for t in TOOLS if t["name"] == "ppcl_lint")
    assert live in lint_tool["description"]

    start = helpdocs.page("start")["body"]
    assert live + " lint rules" in start
    assert "{rules}" not in start


def test_cloning_a_disabled_line_keeps_it_disabled_and_renames_it():
    """A PXC.A disabled line carries "# " in the text, and the lexer has no
    business seeing it -- cloning a block containing one used to raise
    LexError. That is the wrong answer to a line the panel itself keeps and
    runs the moment somebody deletes two characters.
    """
    from ppcl import transforms

    text = ('00100\t# ON("AHU1.SFAN")\n00110\tON("AHU1.RFAN")\n'
            '00120\tGOTO 100\n')
    r = transforms.clone_lines(
        text, 100, 120,
        {"AHU1.SFAN": "AHU2.SFAN", "AHU1.RFAN": "AHU2.RFAN"},
        start=300, step=10)

    copy = r.text.split("00300")[1]
    assert "# ON(" in copy                  # still disabled
    assert "AHU2.SFAN" in copy              # and renamed behind the marker
    assert "AHU1" not in copy
    assert "renaming 2 reference" in r.notes[0]


def test_cloning_renames_inside_an_OIP_keystroke_sequence():
    """Cloning an AHU program is the whole reason this transform exists, and
    it used to hand back a copy that still commanded the original equipment.

    An OIP sequence is one quoted token holding what an operator would type,
    so a point name inside it is a component and a whole-token rename can
    never match it. The copy renamed ON("AHU1.SFAN") and left the OIP
    sequence pointing at AHU1 -- silently, in a file you load to a panel.

    Siemens hit the same wall and stopped there: "Point names in comments or
    OIP statements are not modified and must be modified manually."
    """
    from ppcl import transforms

    text = ('00100\tOIP(TRIG,"P/T/D/H///AHU1.SFAN/1/")\n'
            '00110\tON("AHU1.SFAN")\n00120\tGOTO 100\n')
    r = transforms.clone_lines(text, 100, 120, {"AHU1.SFAN": "AHU2.SFAN"},
                               start=200, step=10)
    copy = r.text.split("00200")[1]

    assert "AHU2.SFAN" in copy
    assert "AHU1" not in copy          # neither in the command nor the sequence
    # The menu keystrokes are untouched.
    assert "P/T/D/H///" in copy and "/1/" in copy
    # Both references counted, not just the command.
    assert "renaming 2 reference" in r.notes[0]
    # And it still warns, because a sequence can name a point in a form no
    # rename map will match.
    assert any("OIP" in w for w in r.warnings)


def test_cloning_without_an_OIP_does_not_raise_the_OIP_warning():
    from ppcl import transforms

    text = '00100\tON("AHU1.SFAN")\n00110\tGOTO 100\n'
    r = transforms.clone_lines(text, 100, 110, {"AHU1.SFAN": "AHU2.SFAN"},
                               start=200, step=10)
    assert not any("OIP" in w for w in r.warnings)


def test_the_three_speed_point_types_are_accepted_by_the_speed_commands():
    """LFMSSL/LFMSSP postdate Table 3-2, and leaving them out rejected real code.

    125-1896 Rev. 5 names only LFSSL and LFSSP wherever a speed type appears.
    The Insight Program Editor help names all four on FAST, SLOW, EMFAST and
    EMSLOW and on the FAST, SLOW and OFF status indicators, and the point-type
    enum puts the pair at 22 and 23 -- after the original block, which is what
    a later addition looks like.
    """
    from ppcl import spec

    for name in ("LFMSSL", "LFMSSP"):
        assert name in spec.POINT_TYPES, name
        assert name in spec.SPEED_TYPES, name
        assert name in spec.DIGITAL_COMMANDABLE, name
        assert spec.POINT_TYPES[name].proof_optional
        # The address organisation is inferred, and has to say so.
        assert any("inferred" in n for n in spec.POINT_TYPES[name].notes), name

    for cmd in ("FAST", "SLOW", "EMFAST", "EMSLOW"):
        assert spec.ALL[cmd].point_types == spec.SPEED_TYPES, cmd

    # AUTO is still the two On/Off/Auto types and nothing else.
    assert spec.AUTO_TYPES == frozenset({"LOOAL", "LOOAP"})


def test_a_three_speed_point_is_not_flagged_by_the_speed_commands():
    """The end-to-end version: a real three-speed point, commanded."""
    from ppcl import linter, parser

    prog = parser.parse("10\tFAST(SF1)\n20\tSLOW(SF2)\n30\tGOTO 10\n")

    def e3(types):
        return [d.code for d in linter.lint(prog, point_types=types)
                if d.code.startswith("E3")]

    assert e3({"SF1": "LFMSSL", "SF2": "LFMSSP"}) == []
    # The two-speed pair still works -- that is the regression that matters.
    assert e3({"SF1": "LFSSL", "SF2": "LFSSP"}) == []
    # And a type that genuinely cannot take FAST is still caught.
    assert e3({"SF1": "LDO", "SF2": "LDO"}) != []


def test_firmware_only_tokens_are_not_offered_as_commands():
    """Known to exist, unknown in every other way -- so not in spec.ALL.

    A name whose arguments nobody can state must not be completed, generated,
    or listed as a command the engineer can reach for. The whole point of the
    separate table is that it carries the name without implying a signature.
    """
    from ppcl import spec

    assert len(spec.FIRMWARE_STATEMENT_TOKENS) == 5
    for name, (value, note) in spec.FIRMWARE_STATEMENT_TOKENS.items():
        assert name not in spec.ALL, name
        assert name not in spec.FUNCTIONS, name
        assert 1 <= value <= 71, name
        assert note
    # And they are still reserved: a point may not be named one.
    for name in spec.FIRMWARE_STATEMENT_TOKENS:
        assert len(name) <= 6, name


def test_the_node_resident_points_start_at_zero():
    """NODE0 is real, and one Siemens table says the range starts at 1.

    The Program Editor's reserved-word page prints "NODE1 through NODE99".
    The dedicated page in the same book states the range in prose -- from 0
    through 99, twice -- and carries NODE0 in its title; so do that book's
    glossary, the PPCL Debugger help and Desigo CC. Four to one, and the one
    is contradicted by its own book. Node 0 is a real drop address besides.
    """
    from ppcl import spec

    assert "NODE0" in spec.RESERVED_WORDS
    assert "NODE99" in spec.RESERVED_WORDS
    assert "NODE100" not in spec.RESERVED_WORDS
    assert spec.is_resident("NODE0")


def test_point_database_carries_slope_and_intercept():
    """Needed for panel error E12, and useful on its own.

    An analog point's engineering value maps to a count through slope and
    intercept, and the panel refuses any command whose count leaves 0..32,767.
    """
    from ppcl import points

    db = points.load_csv(
        "Point Name,Point Type,Slope,Intercept\n"
        "DASP,LAO,0.1,0\n"
        "MAT,LAI,0.01,-40\n"
        "SFAN,LDO,,\n"
    )
    assert db.lookup("DASP").intercept == 0.0
    assert db.lookup("MAT").intercept == -40.0
    assert db.lookup("SFAN").slope is None
    assert db.ignored_columns == []


def test_slope_and_intercept_accept_the_usual_column_names():
    from ppcl import points

    db = points.load_csv("name,type,gain,offset\nX,LAO,2.5,10\n")
    assert db.lookup("X").slope == 2.5
    assert db.lookup("X").intercept == 10.0


def test_bundled_point_types_record_the_proof_being_optional():
    """Every bundled type except L2SL documents its proof DI as optional.

    It matters because PRFON on a point with no proof wired can never be true,
    and nothing in the program text says so.
    """
    from ppcl import spec

    for name in ("L2SP", "LOOAL", "LOOAP", "LFSSL", "LFSSP"):
        assert spec.POINT_TYPES[name].proof_optional, name
    assert not spec.POINT_TYPES["L2SL"].proof_optional


def test_LOOAP_records_that_it_mixes_pulsed_and_latched_outputs():
    from ppcl import spec

    note = " ".join(spec.POINT_TYPES["LOOAP"].notes)
    assert "PULSED" in note and "LATCHED" in note


def test_pdl_order_and_roles_are_declared():
    from ppcl import spec

    assert spec.PDL_COMMAND_ORDER == ("PDLMTR", "PDLSET", "PDLDPG", "PDL",
                                      "PDLDAT")
    everything = set(spec.PDL_ROLES["predictor"]) | set(
        spec.PDL_ROLES["load_handler"])
    assert everything == set(spec.PDL_COMMAND_ORDER)


def test_LSTSQR_is_recorded_as_undocumented():
    """A command recovered from code, not from a manual.

    It appears in Siemens' shipped chiller-sequence programs and in no
    documentation available here. The category says so rather than filing it
    under a Siemens heading, which would imply a source that does not exist.
    """
    from ppcl import spec

    cmd = spec.ALL["LSTSQR"]
    assert spec.category_of("LSTSQR") == "Undocumented"
    assert "Undocumented" not in spec.SIEMENS_COMMAND_CATEGORIES
    # The argument count is not enforced, because no source states it.
    assert not cmd.signature_known
    assert any("UNDOCUMENTED" in n for n in cmd.notes)


def test_an_LSTSQR_call_is_neither_unknown_nor_miscounted():
    from ppcl import linter, parser

    src = ("10\tLSTSQR(1,C,B,$LOC5,X11,Y11,X12,Y12,X13,Y13,"
           "X14,Y14,X15,Y15,X16,Y16)\n20\tGOTO 10\n")
    codes = {d.code for d in linter.lint(parser.parse(src))}
    assert "E110" not in codes     # it is a real command
    assert "E111" not in codes     # but we do not claim to know its arity


def test_the_siemens_categories_stay_a_verbatim_transcription():
    from ppcl import spec

    assert spec.SIEMENS_COMMAND_CATEGORIES == frozenset({
        "Point Control", "Operational Control", "Emergency Control",
        "Program Control", "Energy Management", "Special Function",
        "Arithmetic Function",
    })
