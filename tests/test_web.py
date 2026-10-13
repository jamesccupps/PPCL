"""Tests for the web API and the workspace sandbox.

The API is a thin layer over code that is already tested, so these focus on
the parts that are new and the parts that are dangerous: request validation,
error handling that must not leak a traceback as a crash, and above all the
workspace boundary, because this process writes files.
"""

import json
import os

import pytest

from ppcl import sequence
from ppcl.web import api


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "programs").mkdir()
    (tmp_path / "programs" / "a.ppcl").write_text(
        "00010\tON(SFAN)\n00020\tGOTO 10\n", encoding="utf-8"
    )
    (tmp_path / "notes.md").write_text("not a program", encoding="utf-8")
    return api.Workspace(str(tmp_path))


# --------------------------------------------------------------------------
# Workspace sandbox
# --------------------------------------------------------------------------


def test_resolves_a_path_inside_the_workspace(ws):
    resolved = ws.resolve("programs/a.ppcl")
    assert resolved.startswith(ws.root)


@pytest.mark.parametrize(
    "bad",
    [
        "../outside.ppcl",
        "../../etc/passwd",
        "programs/../../escape.ppcl",
        "programs/./../../escape.ppcl",
    ],
)
def test_parent_traversal_is_refused(ws, bad):
    with pytest.raises(api.ApiError):
        ws.resolve(bad)


def test_absolute_paths_are_refused(ws):
    with pytest.raises(api.ApiError):
        ws.resolve(os.path.abspath(os.sep + "etc" + os.sep + "passwd"))


def test_unc_paths_are_refused(ws):
    with pytest.raises(api.ApiError):
        ws.resolve("\\\\server\\share\\x.ppcl")


def test_empty_path_is_refused(ws):
    with pytest.raises(api.ApiError):
        ws.resolve("")


def test_unexpected_extensions_are_refused(ws):
    with pytest.raises(api.ApiError):
        ws.resolve("evil.py")
    with pytest.raises(api.ApiError):
        ws.resolve("programs/script.sh")


def test_listing_only_shows_program_files(ws):
    paths = [f["path"] for f in ws.listing()]
    assert "programs/a.ppcl" in paths
    assert not any(p.endswith(".md") for p in paths)


# --------------------------------------------------------------------------
# Dispatch and error handling
# --------------------------------------------------------------------------


def test_unknown_endpoint_is_404(ws):
    status, payload = api.dispatch("/api/nope", {}, ws)
    assert status == 404
    assert "error" in payload


def test_api_error_becomes_its_status(ws):
    status, payload = api.dispatch("/api/open", {"path": "../x.ppcl"}, ws)
    assert status == 400
    assert "outside the workspace" in payload["error"]


def test_a_bug_returns_500_not_a_crash(ws, monkeypatch):
    def boom(body, workspace=None):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(api.ROUTES, "/api/lint", boom)
    status, payload = api.dispatch("/api/lint", {"text": "x"}, ws)
    assert status == 500
    assert "kaboom" in payload["error"]
    assert "traceback" in payload


def test_non_string_text_is_rejected(ws):
    status, payload = api.dispatch("/api/lint", {"text": 42}, ws)
    assert status == 400
    assert "must be a string" in payload["error"]


def test_oversized_body_is_rejected(ws):
    huge = "x" * (api.MAX_UPLOAD + 1)
    status, payload = api.dispatch("/api/lint", {"text": huge}, ws)
    assert status == 400


def test_unknown_firmware_is_rejected(ws):
    status, payload = api.dispatch(
        "/api/lint", {"text": "10\tON(A)\n", "firmware": "wibble"}, ws
    )
    assert status == 400
    assert "unknown firmware" in payload["error"]


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


def test_meta_is_json_serialisable_and_complete(ws):
    status, payload = api.dispatch("/api/meta", {}, ws)
    assert status == 200
    json.dumps(payload)  # the UI has to be able to receive it
    for key in ("commands", "rules", "faults", "weather", "priorities",
                "cell_actions", "bacnet_object_types"):
        assert payload[key], key
    assert "LOOP" in payload["commands"]


def test_lint_returns_diagnostics_and_stats(ws):
    # Line 20 is duplicated, which is an error, and line 10 branches to a line
    # that does not exist, which is a warning -- so the response has to carry
    # both severities and both counts.
    text = "00010\tGOTO 15\n00020\tON(A)\n00020\tOFF(A)\n00030\tGOTO 20\n"
    status, payload = api.dispatch("/api/lint", {"text": text}, ws)
    assert status == 200
    codes = {d["code"] for d in payload["diagnostics"]}
    assert "W202" in codes  # branch to a line that does not exist
    assert "E102" in codes  # duplicate line number
    assert payload["stats"]["executable"] == 4
    assert payload["counts"]["error"] >= 1
    assert payload["counts"]["warning"] >= 1


def test_lint_carries_source_lines_for_the_gutter(ws):
    text = "00010\tC ok\n00020\tON(\n00030\tGOTO 10\n"
    _s, payload = api.dispatch("/api/lint", {"text": text}, ws)
    parse_errors = [d for d in payload["diagnostics"] if d["code"] == "E100"]
    assert parse_errors and parse_errors[0]["source_line"] == 2


def test_analyze_returns_flow_and_points(ws):
    text = "00010\tON(SFAN)\n00020\tGOSUB 100\n00030\tGOTO 10\n100\tOFF(RFAN)\n110\tRETURN\n"
    _s, payload = api.dispatch("/api/analyze", {"text": text}, ws)
    assert payload["subroutines"] and payload["subroutines"][0]["entry"] == 100
    names = {p["name"] for p in payload["points"]}
    assert {"SFAN", "RFAN"} <= names
    assert any(e["kind"] == "gosub" for e in payload["edges"])


def test_renumber_reports_refusal_rather_than_guessing(ws):
    text = "00010\tON(A)\n00010\tOFF(B)\n00020\tGOTO 10\n"
    _s, payload = api.dispatch("/api/renumber", {"text": text}, ws)
    assert payload["ok"] is False
    assert any("refusing" in w for w in payload["warnings"])


@pytest.mark.parametrize("text, reason", [
    ("10 ON(A)" + chr(10) + "10 OFF(B)" + chr(10) + "20 GOTO 10" + chr(10),
     "duplicates"),
    ("100 IF(" + chr(34) + "FAN .EQ. ON) THEN GOTO 200" + chr(10) +
     "200 GOTO 100" + chr(10), "unreadable"),
])
def test_a_renumber_refusal_says_which_kind_it_is(ws, text, reason):
    """The UI offers the split-or-drop choice only for duplicates.

    Every other refusal needs the code fixed, and asking "how should
    duplicates be handled?" about an unreadable line -- which is what the UI
    did, because it treated every refusal as that one -- is nonsense.
    """
    status, payload = api.dispatch("/api/renumber", {"text": text}, ws)
    assert status == 200
    assert payload["ok"] is False
    assert payload["refused"] == reason


def test_a_successful_renumber_reports_no_refusal(ws):
    status, payload = api.dispatch(
        "/api/renumber", {"text": "10 X = 1" + chr(10) + "20 GOTO 10" + chr(10)}, ws)
    assert payload["ok"] is True and payload["refused"] is None

def test_renumber_rewrites_references(ws):
    text = "00100\tONPWRT(300)\n00200\tGOTO 300\n00300\tON(A)\n00400\tGOTO 100\n"
    _s, payload = api.dispatch("/api/renumber", {"text": text}, ws)
    assert payload["ok"]
    assert payload["rewritten"] == 3
    assert "ONPWRT(30)" in payload["text"]


def test_explain_a_command(ws):
    _s, payload = api.dispatch("/api/explain", {"topic": "loop"}, ws)
    assert payload["kind"] == "command"
    assert payload["time_based"] is True
    assert payload["subroutine_safe"] is False
    assert payload["min_args"] == 12


def test_explain_a_priority_lists_the_hierarchy(ws):
    _s, payload = api.dispatch("/api/explain", {"topic": "EMER"}, ws)
    assert payload["kind"] == "priority"
    assert [o["name"] for o in payload["order"]][0] == "@NONE"


def test_explain_an_unknown_topic_is_404(ws):
    status, payload = api.dispatch("/api/explain", {"topic": "zzz"}, ws)
    assert status == 404


# --------------------------------------------------------------------------
# Sequence endpoints
# --------------------------------------------------------------------------

DOC = """
sequence "Web test"

points
  SFAN  digital output
  MAT   analog input

modes
  Normal    otherwise
  Shutdown  when Freeze is on

table
            Normal  Shutdown
  SFAN      on      off

interlock Freeze
  trip when MAT < 38
  reset when MAT > 45
  force SFAN off at emer
"""


def test_seq_parse_and_render_round_trip(ws):
    _s, parsed = api.dispatch("/api/seq/parse", {"text": DOC}, ws)
    assert parsed["ok"]
    _s, rendered = api.dispatch(
        "/api/seq/render", {"document": parsed["document"]}, ws
    )
    assert rendered["ok"]
    _s, again = api.dispatch("/api/seq/parse", {"text": rendered["text"]}, ws)
    assert again["document"] == parsed["document"]


def test_seq_parse_reports_the_line_of_a_syntax_error(ws):
    bad = 'sequence "x"\n\nmodes\n  Broken when ???\n'
    _s, payload = api.dispatch("/api/seq/parse", {"text": bad}, ws)
    assert payload["ok"] is False
    assert payload["line"] == 4


def test_seq_compile_lints_what_it_generates(ws):
    _s, payload = api.dispatch("/api/seq/compile", {"text": DOC}, ws)
    assert payload["ok"]
    assert payload["counts"]["error"] == 0
    assert payload["counts"]["warning"] == 0
    assert 'IF("$FREEZE".EQ.1.0)' in payload["text"]


def test_seq_compile_reports_a_compile_error_without_raising(ws):
    bad = DOC.replace("  reset when MAT > 45\n", "")
    _s, payload = api.dispatch("/api/seq/compile", {"text": bad}, ws)
    assert payload["ok"] is False
    assert "reset" in payload["error"].lower()


def test_seq_compile_accepts_a_document_object(ws):
    seq = sequence.parse(DOC)
    _s, payload = api.dispatch(
        "/api/seq/compile", {"document": sequence.to_dict(seq)}, ws
    )
    assert payload["ok"]


# --------------------------------------------------------------------------
# Bench endpoint
# --------------------------------------------------------------------------


def test_bench_returns_series_and_checks(ws):
    _s, compiled = api.dispatch("/api/seq/compile", {"text": DOC}, ws)
    _s, payload = api.dispatch(
        "/api/bench",
        {"text": compiled["text"], "weather": "design_winter", "seconds": 1800,
         "dt": 20},
        ws,
    )
    assert payload["ok"]
    assert payload["series"]
    assert payload["checks"]
    for points in payload["series"].values():
        assert points and len(points[0]) == 2


def test_bench_refuses_a_program_that_does_not_parse(ws):
    _s, payload = api.dispatch("/api/bench", {"text": "00010\tON(\n"}, ws)
    assert payload["ok"] is False
    assert payload["errors"]


def test_bench_clamps_an_absurd_duration(ws):
    """A silly duration is capped, and the reduced line rate is reported."""
    _s, payload = api.dispatch(
        "/api/bench",
        {"text": "00010\tON(SFAN)\n00020\tGOTO 10\n", "seconds": 10 ** 9,
         "dt": 60},
        ws,
    )
    assert payload["ok"]
    assert payload["duration"] <= 86400
    # A long run drops the panel line rate rather than taking minutes, and the
    # response says so instead of silently changing the model.
    assert payload["lines_per_second"] < 500
    assert any("line rate was reduced" in w for w in payload["warnings"])


def test_the_default_bench_run_gets_the_full_panel_rate():
    """The UI's default run length fits the budget at the full line rate.

    The budget was sized for that, so raising the default hours, or the
    panel rate, without the budget would quietly halve the fidelity of the
    run most people press.
    """
    import re

    from ppcl import parser
    from ppcl.plant import TestBench, build_plant, default_bindings

    html = open(os.path.join(os.path.dirname(api.__file__), "static",
                             "index.html"), encoding="utf-8").read()
    hours = int(re.search(r'id="b-hours" value="(\d+)"', html).group(1))
    bench = TestBench(parser.parse("00010\tGOTO 10\n"),
                      build_plant({"preset": "single_zone_ahu"}),
                      default_bindings())
    assert hours * 3600 * bench.lines_per_second <= api.BENCH_LINE_BUDGET


def test_short_bench_runs_at_the_full_panel_rate(ws):
    _s, payload = api.dispatch(
        "/api/bench",
        {"text": "00010\tON(SFAN)\n00020\tGOTO 10\n", "seconds": 600, "dt": 20},
        ws,
    )
    assert payload["lines_per_second"] == 500
    assert not any("line rate" in w for w in payload["warnings"])


def test_bench_injects_a_fault(ws):
    _s, compiled = api.dispatch("/api/seq/compile", {"text": DOC}, ws)
    _s, payload = api.dispatch(
        "/api/bench",
        {
            "text": compiled["text"],
            "weather": "design_winter",
            "seconds": 3600,
            "dt": 20,
            "faults": [
                {"system": "AHU1", "kind": "oa_damper_stuck", "at": 600,
                 "value": 100}
            ],
        },
        ws,
    )
    assert payload["ok"]
    assert payload["faults"]


# --------------------------------------------------------------------------
# File endpoints
# --------------------------------------------------------------------------


def test_open_and_save_round_trip(ws):
    _s, opened = api.dispatch("/api/open", {"path": "programs/a.ppcl"}, ws)
    assert "ON(SFAN)" in opened["text"]

    _s, saved = api.dispatch(
        "/api/save",
        {"path": "programs/b.ppcl", "text": "00010\tOFF(SFAN)\n"},
        ws,
    )
    assert saved["ok"]
    assert os.path.isfile(os.path.join(ws.root, "programs", "b.ppcl"))


def test_save_keeps_a_backup_of_what_it_overwrote(ws):
    api.dispatch("/api/save", {"path": "programs/a.ppcl", "text": "00010\tC new\n"}, ws)
    backup = os.path.join(ws.root, "programs", "a.ppcl.bak")
    assert os.path.isfile(backup)
    with open(backup, encoding="utf-8") as fh:
        assert "ON(SFAN)" in fh.read()


#: How Desigo and Insight export: CRLF, and Windows-1252 in comments.
_EXPORT = b"00010\tC RESET AT 85\xb0F\r\n00020\tGOTO 10\r\n"


def test_a_windows_1252_export_keeps_its_degree_sign(ws):
    """Read as UTF-8 with replacement, 0xB0 became U+FFFD, and Save wrote
    that out -- the byte was gone from the file and from its .bak."""
    path = os.path.join(ws.root, "programs", "export.pcl")
    with open(path, "wb") as fh:
        fh.write(_EXPORT)

    _s, opened = api.dispatch("/api/open", {"path": "programs/export.pcl"}, ws)
    assert opened["encoding"] == "cp1252"
    assert "85\N{DEGREE SIGN}F" in opened["text"]
    assert "\r" not in opened["text"]

    api.dispatch("/api/save", {"path": "programs/export.pcl",
                               "text": opened["text"],
                               "encoding": opened["encoding"]}, ws)
    with open(path, "rb") as fh:
        assert b"85\xb0F" in fh.read()
    with open(path + ".bak", "rb") as fh:
        assert fh.read() == _EXPORT


def test_save_refuses_a_character_the_file_encoding_cannot_hold(ws):
    path = os.path.join(ws.root, "programs", "a.ppcl")
    with open(path, "rb") as fh:
        before = fh.read()
    status, payload = api.dispatch(
        "/api/save", {"path": "programs/a.ppcl", "encoding": "cp1252",
                      "text": "00010\tC ok\n00020\tC \N{RIGHTWARDS ARROW}\n"}, ws)
    assert status == 400
    assert "line 2" in payload["error"]
    with open(path, "rb") as fh:
        assert fh.read() == before


def test_a_utf8_file_opens_and_saves_as_utf8(ws):
    _s, opened = api.dispatch("/api/open", {"path": "programs/a.ppcl"}, ws)
    assert opened["encoding"] == "utf-8"
    api.dispatch("/api/save", {"path": "programs/new.ppcl",
                               "text": "00010\tC 85\N{DEGREE SIGN}F\n"}, ws)
    with open(os.path.join(ws.root, "programs", "new.ppcl"), "rb") as fh:
        assert fh.read() == "00010\tC 85\N{DEGREE SIGN}F\n".encode("utf-8")


def test_open_a_missing_file_is_404(ws):
    status, payload = api.dispatch("/api/open", {"path": "nope.ppcl"}, ws)
    assert status == 404


def test_save_outside_the_workspace_is_refused(ws, tmp_path):
    status, payload = api.dispatch(
        "/api/save", {"path": "../escaped.ppcl", "text": "x"}, ws
    )
    assert status == 400
    assert not (tmp_path.parent / "escaped.ppcl").exists()


# --------------------------------------------------------------------------
# The HTTP layer: what another web page can and cannot make it do
# --------------------------------------------------------------------------
#
# Everything above calls dispatch() directly, which is why this went untested:
# the request checks live in the server, not the API. Before them, a page on
# another domain overwrote a program in the workspace with a text/plain POST,
# and a DNS-rebound request listed every program and the absolute path. These
# run the real server on an ephemeral port and send what a browser would.


@pytest.fixture
def live(tmp_path):
    import threading

    from ppcl.web import server

    (tmp_path / "AHU1.ppcl").write_text("10 C ORIGINAL" + chr(10), encoding="utf-8")
    saved = server.Handler.workspace, server.Handler.quiet
    server.Handler.workspace = api.Workspace(str(tmp_path))
    server.Handler.quiet = True
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd.server_address[1], tmp_path
    finally:
        httpd.shutdown()
        httpd.server_close()
        server.Handler.workspace, server.Handler.quiet = saved


def _request(port, path, body=None, headers=None, method=None):
    import urllib.error
    import urllib.request

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), data=data,
        method=method or ("POST" if data is not None else "GET"),
        headers=headers or {},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


_TAMPER = {"path": "AHU1.ppcl", "text": "10 C TAMPERED" + chr(10)}


def test_another_page_cannot_save_with_a_simple_cross_site_post(live):
    """text/plain needs no preflight, so a browser sends it from any page."""
    port, root = live
    status, payload = _request(port, "/api/save", _TAMPER, {
        "Origin": "https://evil.example", "Content-Type": "text/plain"})
    assert status == 415
    assert (root / "AHU1.ppcl").read_text(encoding="utf-8").startswith("10 C ORIGINAL")


def test_another_page_cannot_save_even_with_a_json_content_type(live):
    """The Origin check stands on its own, not only behind the content type."""
    port, root = live
    status, payload = _request(port, "/api/save", _TAMPER, {
        "Origin": "https://evil.example", "Content-Type": "application/json"})
    assert status == 403
    assert "evil.example" in payload["error"]
    assert (root / "AHU1.ppcl").read_text(encoding="utf-8").startswith("10 C ORIGINAL")


def test_a_dns_rebound_request_cannot_read_the_workspace(live):
    """The rebound page arrives under its own domain, and only Host shows it."""
    port, _ = live
    status, payload = _request(port, "/api/files", headers={
        "Host": "attacker.example:%d" % port})
    assert status == 403
    assert "files" not in payload and "root" not in payload


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "LOCALHOST"])
def test_the_workbench_own_ui_still_saves(live, host):
    """Exactly what core.js sends. If this fails the UI cannot save."""
    port, root = live
    status, payload = _request(port, "/api/save", _TAMPER, {
        "Host": "%s:%d" % (host, port),
        "Origin": "http://%s:%d" % (host.lower(), port),
        "Content-Type": "application/json"})
    assert status == 200, payload
    assert (root / "AHU1.ppcl").read_text(encoding="utf-8").startswith("10 C TAMPERED")


def test_a_script_with_no_origin_still_works(live):
    """curl and scripts send no Origin; they were never the threat."""
    port, _ = live
    status, payload = _request(port, "/api/lint", {"text": "10 GOTO 10" + chr(10)},
                               {"Content-Type": "application/json"})
    assert status == 200, payload


def test_no_other_page_may_frame_the_ui(live):
    """Framing the UI is how a page would steer a click onto Save."""
    import urllib.request

    port, _ = live
    with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=10) as resp:
        assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
