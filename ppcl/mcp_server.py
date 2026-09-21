"""An MCP server exposing the PPCL toolkit to any agent that speaks it.

This is the piece that turns "Claude knows about PPCL" into "Claude can check
PPCL". A model asked whether a program is correct can guess; a model that can
call the linter, the analyzer and the simulator does not have to. The whole
value here is that the answers come from the same code the CLI and the UI use,
so there is one implementation and one set of citations.

JSON-RPC 2.0 over stdin/stdout, newline-delimited, implemented directly --
there is no MCP SDK dependency, in keeping with the rest of the project. The
protocol surface used is small: ``initialize``, ``tools/list``, ``tools/call``
and the ``notifications/*`` that need no reply.

**Read-only with respect to buildings, same as everything else here.** The
file-writing endpoints of the web API are deliberately NOT exposed: an agent
gets to analyse and explain, not to overwrite the engineer's files.
"""

from __future__ import annotations

import json
import sys
import traceback

from . import spec
from .web.api import Workspace, dispatch

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ppcl"


# --------------------------------------------------------------------------
# Tool definitions
# --------------------------------------------------------------------------

def _text(desc, required=True):
    return {"type": "string", "description": desc}


def _rule_count() -> int:
    """How many rules there actually are, asked rather than remembered.

    This number was written into the tool description by hand and went stale
    at 75 while the linter grew to 88 -- so every agent using this plugin was
    told a figure thirteen short. A count in prose decays silently; a count
    that is computed cannot.
    """
    from . import linter

    linter._load_rules()
    return len(linter.REGISTRY)


#: Every tool maps to an endpoint in :mod:`ppcl.web.api`, so the MCP surface
#: cannot drift from the CLI and the UI. ``format`` names the renderer that
#: turns the JSON result into something worth putting in a model's context --
#: raw diagnostic objects are mostly punctuation.
TOOLS = [
    {
        "name": "ppcl_lint",
        "description": (
            "Check a PPCL program against %d rules and report every finding "
            % _rule_count() +
            "with the manual reference it comes from. Use this instead of "
            "reading a program for defects by eye -- it catches duplicate "
            "line numbers, unreleased priorities, time-based commands outside "
            "the main loop, argument-count errors and reference integrity "
            "exhaustively. Always run this before telling anyone a program "
            "looks correct."
        ),
        "endpoint": "/api/lint",
        "format": "lint",
        "schema": {
            "type": "object",
            "properties": {
                "text": _text("The full PPCL program text, with line numbers."),
                "firmware": {
                    "type": "string",
                    "enum": [f.value for f in spec.Firmware],
                    "description": (
                        "Target panel generation. This changes results: pxc_a "
                        "allows 512-character lines and rejects OIP outright, "
                        "apogee allows 66 and 16 operands, older families 13. "
                        "Ask which panel if it matters and you do not know."
                    ),
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "ppcl_explain",
        "description": (
            "Look up a PPCL command, point type, @priority, function or lint "
            "rule code and get its signature, every parameter, the manual's "
            "own notes and its restrictions. Use this rather than recalling a "
            "signature from memory -- argument limits and firmware "
            "restrictions are easy to misremember and expensive to get wrong."
        ),
        "endpoint": "/api/explain",
        "format": "explain",
        "schema": {
            "type": "object",
            "properties": {
                "topic": _text(
                    "A command (LOOP, SETVAL), a point type (LOOAP), a "
                    "priority (EMER), a function (SQRT) or a rule code (E205)."
                ),
            },
            "required": ["topic"],
        },
    },
    {
        "name": "ppcl_help",
        "description": (
            "Read the built-in PPCL documentation: point priority, the "
            "execution model, decision tables, block diagrams, SSTO, the "
            "source provenance, and what is verified versus approximated. "
            "Pass a topic id to read a page, or a query to search."
        ),
        "endpoint": "/api/help",
        "format": "help",
        "schema": {
            "type": "object",
            "properties": {
                "topic": _text(
                    "Page id: start, safety, editor, blocks, builder, "
                    "priority, guidelines, rules, bench, points, ssto, "
                    "sources. Omit to list the contents."
                ),
                "query": _text("Search text, instead of a topic."),
            },
        },
    },
    {
        "name": "ppcl_analyze",
        "description": (
            "Control-flow and point analysis: which lines run every pass, "
            "which run once, where the main loop starts, what the subroutines "
            "are, and every point the program touches with the priorities it "
            "is commanded at. Use this to answer 'what does this program "
            "actually do' before reading it line by line."
        ),
        "endpoint": "/api/analyze",
        "format": "analyze",
        "schema": {
            "type": "object",
            "properties": {"text": _text("The full PPCL program text.")},
            "required": ["text"],
        },
    },
    {
        "name": "ppcl_simulate",
        "description": (
            "Execute a program against a simulated panel with real point "
            "priority arbitration, and report what it wrote and what was "
            "refused. This is how you answer 'why did the schedule stop "
            "working' -- a command blocked because the point is held at a "
            "higher priority is reported explicitly."
        ),
        "endpoint": "/api/run",
        "format": "simulate",
        "schema": {
            "type": "object",
            "properties": {
                "text": _text("The full PPCL program text."),
                "points": {
                    "type": "object",
                    "description": (
                        "Starting point values, e.g. {\"MAT\": 35, \"SFAN\": "
                        "\"ON\"}. Values may be numbers, ON/OFF, or HH:MM."
                    ),
                },
                "time": {
                    "type": "number",
                    "description": "Start time in decimal hours, e.g. 8.5.",
                },
                "passes": {
                    "type": "integer",
                    "description": "Program passes to run. Default 10.",
                },
                "firmware": {
                    "type": "string",
                    "enum": [f.value for f in spec.Firmware],
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "ppcl_bench",
        "description": (
            "Run a program against simulated HVAC equipment -- mixing box, "
            "coils, fan, zone with thermal mass, sensor lag, actuator stroke "
            "time -- and report trends and pass/fail checks. Use this when "
            "the question is 'does this control?' rather than 'does this line "
            "fire?'. Optionally inject a fault to test the failure path. "
            "Note that LOOP output values are approximated and must never be "
            "presented as tuning guidance."
        ),
        "endpoint": "/api/bench",
        "format": "bench",
        "schema": {
            "type": "object",
            "properties": {
                "text": _text("The full PPCL program text."),
                "weather": _text("Weather preset, e.g. design_winter."),
                "seconds": {
                    "type": "number",
                    "description": "Simulated duration. Default 10800 (3 h).",
                },
                "start_hour": {
                    "type": "number",
                    "description": "Time of day the run begins.",
                },
                "faults": {
                    "type": "array",
                    "description": (
                        "Faults to inject, e.g. [{\"kind\": "
                        "\"oa_damper_stuck\", \"at\": 1800, \"value\": 100}]."
                    ),
                    "items": {"type": "object"},
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "ppcl_format",
        "description": (
            "Sort a program by line number and normalise its layout. Returns "
            "the rewritten text."
        ),
        "endpoint": "/api/format",
        "format": "text",
        "schema": {
            "type": "object",
            "properties": {"text": _text("The full PPCL program text.")},
            "required": ["text"],
        },
    },
    {
        "name": "ppcl_renumber",
        "description": (
            "Renumber a program, rewriting every GOTO, GOSUB, ACT, DEACT, "
            "ENABLE, DISABL and ONPWRT reference exactly. Refuses when the "
            "program has duplicate line numbers, because the two possible "
            "repairs produce different running programs -- surface that "
            "choice to the user rather than picking one."
        ),
        "endpoint": "/api/renumber",
        "format": "renumber",
        "schema": {
            "type": "object",
            "properties": {
                "text": _text("The full PPCL program text."),
                "start": {"type": "integer", "description": "First line number."},
                "step": {"type": "integer", "description": "Increment."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "ppcl_transform",
        "description": (
            "Apply an exact source transform: expand or collapse DEFINE "
            "abbreviations, swap point-name separators between periods and "
            "underscores, or clone a block of lines with point renames and "
            "retargeted branches. These work on a real parse, so a decimal "
            "point or a .AND. operator is never mistaken for a separator."
        ),
        "endpoint": "/api/transform",
        "format": "transform",
        "schema": {
            "type": "object",
            "properties": {
                "text": _text("The full PPCL program text."),
                "op": {
                    "type": "string",
                    "enum": [
                        "expand_defines", "collapse_defines",
                        "separators_dot", "separators_underscore",
                        "toggle_comment", "disable", "enable", "clone",
                    ],
                },
                "lines": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Line numbers, for the line-scoped ops.",
                },
                "first": {"type": "integer", "description": "clone: first line."},
                "last": {"type": "integer", "description": "clone: last line."},
                "renames": {
                    "type": "object",
                    "description": "clone: {\"OLD\": \"NEW\"} point renames.",
                },
            },
            "required": ["text", "op"],
        },
    },
    {
        "name": "ppcl_compile_sequence",
        "description": (
            "Compile a sequence document -- a decision table of equipment "
            "against modes, plus interlocks, resets and loops -- into PPCL, "
            "and lint the result. Prefer this over writing PPCL by hand when "
            "someone is describing behaviour rather than editing code: the "
            "compiler guarantees one backward GOTO, a matching RELEAS at the "
            "same priority for every interlock, and no time-based command "
            "inside a conditional."
        ),
        "endpoint": "/api/seq/compile",
        "format": "compile",
        "schema": {
            "type": "object",
            "properties": {
                "text": _text("The sequence document in its text form."),
                "firmware": {
                    "type": "string",
                    "enum": [f.value for f in spec.Firmware],
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "ppcl_commands",
        "description": (
            "List every PPCL command with its summary, grouped by Siemens' "
            "own Command Assist categories. Use it to find the right command "
            "when you know what you want to do but not what it is called."
        ),
        "endpoint": "/api/meta",
        "format": "commands",
        "schema": {"type": "object", "properties": {}},
    },
]

BY_NAME = {tool["name"]: tool for tool in TOOLS}


# --------------------------------------------------------------------------
# Result rendering
# --------------------------------------------------------------------------
#
# A model pays for every token of a tool result, and raw diagnostic objects are
# mostly field names. These renderers produce the same information the CLI
# prints, which is what a person would want to read anyway.


def _render_lint(result):
    counts = result.get("counts", {})
    stats = result.get("stats", {})
    lines = [
        "%d error, %d warning, %d info, %d style"
        % (counts.get("error", 0), counts.get("warning", 0),
           counts.get("info", 0), counts.get("style", 0)),
        "%d executable lines, %d in the main loop, %d run once"
        % (stats.get("executable", 0), stats.get("steady_state", 0),
           stats.get("one_shot", 0)),
        "",
    ]
    diags = result.get("diagnostics", [])
    if not diags:
        lines.append("No findings.")
    for d in diags:
        where = "line %s" % d["line"] if d.get("line") else "program"
        lines.append("%-8s %-6s %-12s %s"
                     % (d["severity"], d["code"], where, d["message"]))
        for key, label in (("detail", "  "), ("suggestion", "  fix: "),
                           ("manual", "  manual: ")):
            if d.get(key):
                lines.append("%s%s" % (label, d[key]))
    return "\n".join(lines)


def _render_explain(result):
    lines = ["%s -- %s" % (result.get("name"), result.get("summary", ""))]
    if result.get("signature"):
        lines.append(result["signature"])
    lines.append("")
    params = (result.get("params") or []) + (result.get("repeat") or []) \
        + (result.get("trailing") or [])
    for p in params:
        choices = (" (one of: %s)" % ", ".join(p["choices"])) if p.get("choices") else ""
        lines.append("  %-12s %-9s %s%s"
                     % (p["name"], p["kind"], p.get("doc", ""), choices))
    flags = []
    if result.get("time_based"):
        flags.append("time-based: must be evaluated on every program pass")
    if result.get("subroutine_safe") is False:
        flags.append("NOT allowed inside a GOSUB subroutine")
    if result.get("if_target_safe") is False:
        flags.append("NOT allowed as the THEN or ELSE clause of an IF")
    if result.get("signature_known") is False:
        flags.append("signature not published; treat with care")
    if flags:
        lines.append("")
        lines.extend("  ! " + f for f in flags)
    if result.get("notes"):
        lines.append("")
        lines.extend("  - " + n for n in result["notes"])
    if result.get("order"):
        lines.append("")
        lines.extend("  %s  %s" % (o["name"], o["doc"]) for o in result["order"])
    if result.get("detail"):
        lines.extend(["", result["detail"]])
    if result.get("see_also"):
        lines.append("")
        lines.append("See also: " + ", ".join(result["see_also"]))
    return "\n".join(lines)


def _render_help(result):
    if result.get("page"):
        return result["page"]["body"]
    if result.get("results") is not None:
        hits = result["results"]
        if not hits:
            return "Nothing found."
        return "\n".join(
            "%-12s %s\n             %s" % (h["id"], h["title"], h["excerpt"])
            for h in hits
        )
    out = []
    for group in result.get("contents", []):
        out.append(group["category"])
        out.extend("  %-12s %s" % (p["id"], p["summary"])
                   for p in group["pages"])
        out.append("")
    return "\n".join(out)


def _render_analyze(result):
    lines = ["Main loop starts at line %s" % result.get("loop_entry")]
    subs = result.get("subroutines", [])
    if subs:
        lines.append("")
        lines.append("Subroutines:")
        for s in subs:
            lines.append("  entry %-6s %d lines, %d return(s), called from %s"
                         % (s["entry"], s["lines"], s["returns"],
                            ", ".join(str(c) for c in s["callers"]) or "nothing"))
    lines.append("")
    lines.append("Points:")
    for p in result.get("points", []):
        priorities = (" at " + ", ".join(p["priorities"])) if p["priorities"] else ""
        lines.append("  %-24s %d read, %d write  [%s]%s"
                     % (p["name"], p["reads"], p["writes"],
                        ", ".join(p["commands"]), priorities))
    return "\n".join(lines)


def _render_simulate(result):
    lines = []
    if result.get("blocked"):
        lines.append("BLOCKED BY POINT PRIORITY:")
        lines.extend("  " + b for b in result["blocked"])
        lines.append("")
    if result.get("warnings"):
        lines.append("Warnings:")
        lines.extend("  " + w for w in result["warnings"])
        lines.append("")
    if result.get("points"):
        lines.append("Final point values:")
        for name, info in sorted(result["points"].items()):
            if isinstance(info, dict):
                lines.append("  %-20s %-10s %s"
                             % (name, info.get("value"), info.get("priority", "")))
            else:
                lines.append("  %-20s %s" % (name, info))
    if result.get("events"):
        lines.append("")
        lines.append("Trace (last 40):")
        for e in result["events"][-40:]:
            lines.append("  line %-6s %-8s %s"
                         % (e.get("line"), e.get("kind"), e.get("text")))
    return "\n".join(lines) or json.dumps(result, indent=1)[:4000]


def _render_bench(result):
    if not result.get("ok"):
        return "The run failed: %s" % result.get("error", "unknown")
    lines = ["%s (%.0f s simulated)"
             % ("PASSED" if result.get("passed") else "CHECKS FAILED",
                result.get("duration", 0))]
    for c in result.get("checks", []):
        lines.append("  %-4s %s -- %s"
                     % ("PASS" if c["passed"] else "FAIL", c["name"], c["detail"]))
    if result.get("blocked"):
        lines.append("")
        lines.append("Blocked by point priority:")
        lines.extend("  " + b for b in result["blocked"])
    if result.get("warnings"):
        lines.append("")
        lines.extend("  ! " + w for w in result["warnings"])
    series = result.get("series", {})
    if series:
        lines.append("")
        lines.append("Trends (min / max / final):")
        for name, data in series.items():
            values = [v for _t, v in data]
            if values:
                lines.append("  %-22s %7.2f %7.2f %7.2f"
                             % (name, min(values), max(values), values[-1]))
    return "\n".join(lines)


def _render_compile(result):
    if not result.get("ok"):
        return "Compilation failed: %s" % result.get("error", "unknown")
    lines = [result["text"], ""]
    for w in result.get("warnings", []):
        lines.append("! " + w)
    counts = result.get("counts", {})
    lines.append("Lint: %d error, %d warning"
                 % (counts.get("error", 0), counts.get("warning", 0)))
    for d in result.get("diagnostics", []):
        if d["severity"] in ("error", "warning"):
            lines.append("  %-8s %-6s line %-6s %s"
                         % (d["severity"], d["code"], d["line"], d["message"]))
    return "\n".join(lines)


def _render_renumber(result):
    if not result.get("ok"):
        return "Renumbering refused:\n" + "\n".join(
            "  " + w for w in result.get("warnings", [])
        )
    return "%s\n\n%d reference(s) rewritten." % (
        result["text"], result.get("rewritten", 0))


def _render_transform(result):
    if not result.get("ok"):
        return "Transform failed: %s" % result.get("error", "unknown")
    lines = [result["text"], ""]
    lines.extend("  " + n for n in result.get("notes", []))
    lines.extend("  ! " + w for w in result.get("warnings", []))
    return "\n".join(lines)


def _render_commands(result):
    lines = []
    summaries = result.get("command_summaries", {})
    for category, names in result.get("command_categories", {}).items():
        lines.append(category)
        for name in names:
            lines.append("  %-9s %s" % (name, summaries.get(name, "")))
        lines.append("")
    return "\n".join(lines)


def _render_text(result):
    return result.get("text", json.dumps(result, indent=1))


RENDERERS = {
    "lint": _render_lint,
    "explain": _render_explain,
    "help": _render_help,
    "analyze": _render_analyze,
    "simulate": _render_simulate,
    "bench": _render_bench,
    "compile": _render_compile,
    "renumber": _render_renumber,
    "transform": _render_transform,
    "commands": _render_commands,
    "text": _render_text,
}


# --------------------------------------------------------------------------
# Protocol
# --------------------------------------------------------------------------


def call_tool(name, arguments, workspace=None):
    """Run one tool. Returns ``(text, is_error)``."""
    tool = BY_NAME.get(name)
    if tool is None:
        return "No such tool: %s" % name, True
    status, payload = dispatch(tool["endpoint"], dict(arguments or {}),
                               workspace)
    if status != 200 or "error" in payload:
        return "%s failed: %s" % (name, payload.get("error", status)), True
    render = RENDERERS.get(tool["format"], _render_text)
    try:
        return render(payload), False
    except Exception as exc:  # a renderer bug must not lose the result
        return "%s\n\n(could not format: %s)" % (
            json.dumps(payload, indent=1)[:6000], exc), False


def handle(message, workspace=None):
    """Handle one JSON-RPC message. Returns a response dict, or None."""
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        return _ok(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": _version()},
            "instructions": (
                "PPCL tooling for Siemens APOGEE and PXC field panels. Run "
                "ppcl_lint before judging any program correct -- it checks 75 "
                "rules and cites the manual for each. Use ppcl_explain rather "
                "than recalling a command signature. Nothing here touches a "
                "building system; it reads and writes text."
            ),
        })

    if method in ("notifications/initialized", "initialized", "ping"):
        return _ok(request_id, {}) if request_id is not None else None

    if method == "tools/list":
        return _ok(request_id, {
            "tools": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": t["schema"],
                }
                for t in TOOLS
            ]
        })

    if method == "tools/call":
        params = message.get("params") or {}
        text, is_error = call_tool(
            params.get("name"), params.get("arguments"), workspace
        )
        return _ok(request_id, {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        })

    if request_id is None:
        return None  # an unknown notification needs no reply
    return _err(request_id, -32601, "method not found: %s" % method)


def _ok(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _err(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def _version():
    from . import __version__

    return __version__


def serve(workspace="."):
    """Read newline-delimited JSON-RPC from stdin until EOF."""
    ws = Workspace(workspace)
    # stdout is the protocol channel; anything printed to it corrupts the
    # stream, so every diagnostic goes to stderr.
    out = sys.stdout
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            sys.stderr.write("ppcl-mcp: bad JSON: %s\n" % exc)
            continue
        try:
            response = handle(message, ws)
        except Exception:
            sys.stderr.write("ppcl-mcp: %s\n" % traceback.format_exc())
            response = _err(message.get("id"), -32603, "internal error")
        if response is None:
            continue
        out.write(json.dumps(response) + "\n")
        out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(serve())
