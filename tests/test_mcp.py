"""Tests for the MCP server.

The protocol handling is small but it is the whole interface an agent sees, so
the tests cover the handshake, the tool contract, and the rendering -- a
renderer that raises would otherwise turn a good result into a silent failure
inside someone else's session.
"""


import json
import os
import subprocess
import sys

import pytest

from ppcl import mcp_server, spec
from ppcl.web.api import Workspace


@pytest.fixture
def ws(tmp_path):
    return Workspace(str(tmp_path))


PROGRAM = (
    '10\tOIP(TRIG,"P/D")\n'
    "20\tIF(MAT.LT.38.0) THEN OFF(@EMER,SFAN)\n"
    "30\tGOTO 20\n"
)


# -- protocol ---------------------------------------------------------------


def test_initialize_reports_the_protocol_and_server():
    r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSION
    assert r["result"]["serverInfo"]["name"] == "ppcl"
    assert "instructions" in r["result"]


def test_tools_list_matches_the_declared_tools():
    r = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in r["result"]["tools"]]
    assert names == [t["name"] for t in mcp_server.TOOLS]
    for tool in r["result"]["tools"]:
        assert tool["description"].strip()
        assert tool["inputSchema"]["type"] == "object"


def test_a_notification_gets_no_reply():
    assert mcp_server.handle(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    ) is None


def test_an_unknown_method_is_a_jsonrpc_error():
    r = mcp_server.handle({"jsonrpc": "2.0", "id": 9, "method": "nope"})
    assert r["error"]["code"] == -32601


def test_an_unknown_tool_is_an_error_result_not_a_crash(ws):
    text, is_error = mcp_server.call_tool("nope", {}, ws)
    assert is_error
    assert "No such tool" in text


# -- the tool contract ------------------------------------------------------


def test_every_tool_routes_to_a_real_endpoint():
    from ppcl.web import api

    api.dispatch("/api/meta", {}, None)       # force the deferred merge
    for tool in mcp_server.TOOLS:
        assert tool["endpoint"] in api.ROUTES, tool["name"]
        assert tool["format"] in mcp_server.RENDERERS, tool["name"]


def test_no_file_writing_endpoint_is_exposed():
    """An agent gets to analyse, not to overwrite the engineer's files."""
    exposed = {tool["endpoint"] for tool in mcp_server.TOOLS}
    for forbidden in ("/api/save", "/api/open", "/api/files"):
        assert forbidden not in exposed


def test_firmware_enums_in_schemas_stay_in_step_with_the_spec():
    """A stale enum would offer an agent a firmware that no longer exists."""
    values = {f.value for f in spec.Firmware}
    for tool in mcp_server.TOOLS:
        prop = tool["schema"].get("properties", {}).get("firmware")
        if prop:
            assert set(prop["enum"]) == values, tool["name"]


# -- rendering --------------------------------------------------------------


def test_lint_renders_findings_with_their_citations(ws):
    text, is_error = mcp_server.call_tool(
        "ppcl_lint", {"text": PROGRAM, "firmware": "pxc_a"}, ws
    )
    assert not is_error
    assert "E119" in text
    assert "OIP is not available on pxc_a" in text
    assert "manual:" in text


def test_the_same_program_is_clean_of_e119_on_apogee(ws):
    text, _ = mcp_server.call_tool(
        "ppcl_lint", {"text": PROGRAM, "firmware": "apogee"}, ws
    )
    assert "E119" not in text


def test_explain_renders_parameters_and_notes(ws):
    text, is_error = mcp_server.call_tool(
        "ppcl_explain", {"topic": "SETVAL"}, ws
    )
    assert not is_error
    assert "srcValSpec" in text
    assert "@OoServe" in text


def test_simulate_surfaces_a_priority_block(ws):
    text, is_error = mcp_server.call_tool(
        "ppcl_simulate",
        {"text": "10\tIF(MAT.LT.38.0) THEN OFF(@EMER,SFAN)\n"
                 "20\tON(SFAN)\n30\tGOTO 10\n",
         "points": {"MAT": 30}, "passes": 5},
        ws,
    )
    assert not is_error
    assert "BLOCKED BY POINT PRIORITY" in text
    assert "SFAN" in text


def test_help_serves_a_page_and_a_search(ws):
    page, _ = mcp_server.call_tool("ppcl_help", {"topic": "priority"}, ws)
    assert "Point priority" in page
    found, _ = mcp_server.call_tool("ppcl_help", {"query": "RELEAS"}, ws)
    assert "priority" in found


def test_commands_are_grouped_by_the_siemens_categories(ws):
    text, _ = mcp_server.call_tool("ppcl_commands", {}, ws)
    assert "Energy Management" in text
    assert "LOOP" in text


def test_every_renderer_survives_an_empty_payload():
    """A renderer that raises would lose a good result inside a session."""
    for name, render in mcp_server.RENDERERS.items():
        out = render({})
        assert isinstance(out, str), name


def test_a_tool_error_is_reported_not_raised(ws):
    text, is_error = mcp_server.call_tool("ppcl_explain", {"topic": "NOPE"}, ws)
    assert is_error
    assert "failed" in text


def _serve(lines, monkeypatch, tmp_path):
    import io
    import json

    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(lines) + "\n"))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert mcp_server.serve(str(tmp_path)) == 0
    return [json.loads(l) for l in out.getvalue().splitlines()]


def test_a_line_that_is_not_an_object_does_not_end_the_session(
        monkeypatch, tmp_path):
    """[] parsed as JSON, then message.get raised -- in the except clause
    meant to report errors -- and the server exited."""
    replies = _serve(["[]", "1", "null",
                      '{"jsonrpc":"2.0","id":7,"method":"tools/list"}'],
                     monkeypatch, tmp_path)
    assert [r.get("id") for r in replies] == [None, None, None, 7]
    assert all(r["error"]["code"] == -32600 for r in replies[:3])
    assert "tools" in replies[3]["result"]


def test_a_tool_call_sent_as_a_notification_is_not_run(monkeypatch, tmp_path):
    """A notification has no id and gets no reply; a tools/call without one
    used to run and answer with id null."""
    replies = _serve(['{"jsonrpc":"2.0","method":"tools/call","params":'
                      '{"name":"ppcl_lint","arguments":{"text":"10 GOTO 10"}}}'],
                     monkeypatch, tmp_path)
    assert replies == []


# -- how .mcp.json starts the server ----------------------------------------

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _launch(cwd, plugin_root):
    """Start the server the way Claude Code does from .mcp.json.

    Claude Code replaces the exact text ${CLAUDE_PLUGIN_ROOT} for a plugin
    and leaves it as it is for a project's own .mcp.json, so plugin_root=None
    is this repository opened as a project.
    """
    with open(os.path.join(_ROOT, ".mcp.json"), encoding="utf-8") as fh:
        server = json.load(fh)["mcpServers"]["ppcl"]
    def expand(text):
        if plugin_root is None:
            return text
        return text.replace("${CLAUDE_PLUGIN_ROOT}",
                            plugin_root.replace("\\", "/"))

    args = [expand(a) for a in server["args"]]
    program = "10\tC 55°F\n20\tGOTO 10\n"
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "ppcl_format", "arguments": {"text": program}}},
    ]
    stdin = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env.update({k: expand(v) for k, v in server.get("env", {}).items()})
    done = subprocess.run(
        [sys.executable] + args, cwd=str(cwd), env=env, timeout=60,
        input=stdin.encode("utf-8"), capture_output=True,
    )
    replies = [json.loads(line) for line in done.stdout.decode().splitlines()
               if line.strip()]
    return replies, done.stderr.decode("utf-8", "replace")


def _decoy_project(tmp_path):
    project = tmp_path / "someone-elses-project"
    (project / "ppcl").mkdir(parents=True)
    (project / "ppcl" / "__init__.py").write_text("")
    (project / "ppcl" / "mcp_server.py").write_text(
        'raise SystemExit("the project folder\'s ppcl ran")\n')
    for name in ("json", "runpy"):
        (project / (name + ".py")).write_text(
            'raise SystemExit("the project folder\'s %s.py ran")\n' % name)
    return project


def test_a_plugin_server_ignores_the_project_folder(tmp_path):
    """Under python -m the current folder -- the user's project -- came
    first on sys.path, so a ppcl folder or a json.py there ran instead."""
    replies, err = _launch(_decoy_project(tmp_path), plugin_root=_ROOT)
    assert "ran" not in err, err
    assert [r["id"] for r in replies] == [1, 2], err
    assert replies[0]["result"]["tools"]


def test_the_repository_opened_as_a_project_still_starts_its_server():
    replies, err = _launch(_ROOT, plugin_root=None)
    assert [r["id"] for r in replies] == [1, 2], err


@pytest.mark.parametrize("plugin", [True, False])
def test_the_server_reads_utf8_without_pythonioencoding(tmp_path, plugin):
    """-E drops PYTHONIOENCODING; a degree sign must still arrive intact."""
    cwd = _decoy_project(tmp_path) if plugin else _ROOT
    replies, err = _launch(cwd, plugin_root=_ROOT if plugin else None)
    text = replies[1]["result"]["content"][0]["text"]
    assert "55°F" in text, (text, err)


def test_a_successful_renumber_still_reports_its_warnings(ws):
    """Only a refusal used to show warnings. A GOTO to a missing line is
    renumbered -- and the reader must be told it now names another line."""
    program = "10\tGOTO 25\n30\tSET(1.0,X)\n40\tGOTO 10\n"
    text, is_error = mcp_server.call_tool("ppcl_renumber", {"text": program}, ws)
    assert not is_error
    assert "reference(s) rewritten" in text
    assert "  ! line 10: GOTO 25 names a line that does not exist" in text


@pytest.mark.parametrize("tool", ["ppcl_simulate", "ppcl_bench"])
def test_a_program_that_does_not_parse_says_where(ws, tool):
    """Only "the program does not parse" came back; the API's list of
    which line and why was dropped."""
    bad = "10\tSET(1.0,X)\n20\tIF(X.GT.) THEN ON(FAN)\n30\tGOTO 10\n"
    text, is_error = mcp_server.call_tool(tool, {"text": bad}, ws)
    assert is_error
    assert "does not parse" in text
    assert "  line 20: " in text
