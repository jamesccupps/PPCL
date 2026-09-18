"""Tests for the MCP server.

The protocol handling is small but it is the whole interface an agent sees, so
the tests cover the handshake, the tool contract, and the rendering -- a
renderer that raises would otherwise turn a good result into a silent failure
inside someone else's session.
"""

import json

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
