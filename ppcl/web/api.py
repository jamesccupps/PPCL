"""JSON API for the workbench UI.

Every endpoint is a plain function taking a decoded request dict and returning
a JSON-serialisable dict, so the whole API is testable without starting a
server. :mod:`ppcl.web.server` is a thin HTTP shell over this.

Two safety rules hold throughout, because this process can write files:

* File operations are confined to a workspace root, and any path that escapes
  it is rejected rather than normalised.
* Nothing here executes a shell, opens a socket, or touches a building system.
  The workbench reads and writes files; the engineer loads them to the panel.
"""

from __future__ import annotations

import os
import traceback

from .. import analyzer, formatter, linter, sequence, spec
from .. import parser as ppcl_parser
from ..diagnostics import Severity

MAX_UPLOAD = 4 * 1024 * 1024  # a PPCL program is never megabytes

#: Ceiling on simulated statement evaluations for one bench request, so
#: the UI stays responsive. The CLI has no such limit.
BENCH_LINE_BUDGET = 1_500_000


class ApiError(Exception):
    """A request that cannot be served, with an HTTP-ish status."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


# --------------------------------------------------------------------------
# Workspace
# --------------------------------------------------------------------------


class Workspace:
    """A directory the UI may read and write, and nothing outside it."""

    #: Extensions the workbench will open or save.
    ALLOWED = (".ppcl", ".pcl", ".seq", ".json", ".txt", ".csv")

    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    def resolve(self, relative: str) -> str:
        """Resolve a client-supplied path inside the workspace.

        Rejects absolute paths, parent traversal, and anything that resolves
        outside the root even via a symlink.
        """
        if not relative or not isinstance(relative, str):
            raise ApiError("a file name is required")
        if os.path.isabs(relative) or relative.startswith("\\\\"):
            raise ApiError("absolute paths are not allowed")
        candidate = os.path.abspath(os.path.join(self.root, relative))
        root = os.path.realpath(self.root)
        real = os.path.realpath(candidate)
        if real != root and not real.startswith(root + os.sep):
            raise ApiError("that path is outside the workspace")
        ext = os.path.splitext(candidate)[1].lower()
        if ext and ext not in self.ALLOWED:
            raise ApiError(
                "%s files are not handled here; allowed: %s"
                % (ext, ", ".join(self.ALLOWED))
            )
        return candidate

    def listing(self) -> list:
        out = []
        for dirpath, dirnames, names in os.walk(self.root):
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d not in ("__pycache__", "node_modules")
            ]
            for name in sorted(names):
                lower = name.lower()
                if lower.endswith(".blocks.json"):
                    kind = "diagram"
                elif lower.endswith(".seq"):
                    kind = "sequence"
                elif os.path.splitext(lower)[1] in (".ppcl", ".pcl"):
                    kind = "ppcl"
                else:
                    continue
                full = os.path.join(dirpath, name)
                out.append(
                    {
                        "path": os.path.relpath(full, self.root).replace("\\", "/"),
                        "size": os.path.getsize(full),
                        "kind": kind,
                    }
                )
        return sorted(out, key=lambda f: f["path"])


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _text(body, key="text"):
    value = body.get(key)
    if not isinstance(value, str):
        raise ApiError("%r must be a string" % key)
    if len(value) > MAX_UPLOAD:
        raise ApiError("that is too large to process")
    return value


def _firmware(body):
    name = body.get("firmware", "apogee")
    try:
        return spec.Firmware(name)
    except ValueError:
        raise ApiError(
            "unknown firmware %r; expected one of %s"
            % (name, ", ".join(f.value for f in spec.Firmware))
        )


def _diag_payload(diags):
    return [d.to_dict() for d in diags]


# --------------------------------------------------------------------------
# Endpoints: PPCL
# --------------------------------------------------------------------------


def api_lint(body, ws=None):
    """Parse and lint a program. The editor calls this on every idle keystroke."""
    text = _text(body)
    prog = ppcl_parser.parse(text, name=body.get("name", "editor"))
    diags = linter.lint(
        prog,
        firmware=_firmware(body),
        point_types=body.get("point_types") or {},
        disabled=body.get("disabled") or (),
        options=body.get("options") or {},
    )
    a = analyzer.analyze(prog)
    counts = linter.summarize(diags)
    return {
        "diagnostics": _diag_payload(diags),
        "counts": counts,
        "stats": {
            "lines": len(prog.lines),
            "executable": len(prog.executable_lines()),
            "reachable": len(a.reachable),
            "steady_state": len(a.steady_state),
            "one_shot": len(a.one_shot),
            "loop_entry": a.loop_entry,
            "starved": sorted(set(a.numbers) - a.reachable)[:20],
        },
    }


def api_analyze(body, ws=None):
    """Control flow, subroutines and the point inventory, for the graph pane."""
    text = _text(body)
    prog = ppcl_parser.parse(text, name=body.get("name", "editor"))
    a = analyzer.analyze(prog)
    points = {}
    for use in a.uses:
        row = points.setdefault(
            use.name.upper(),
            {"name": use.name, "reads": 0, "writes": 0, "commands": [],
             "priorities": []},
        )
        if use.kind == "read":
            row["reads"] += 1
        elif use.kind == "write":
            row["writes"] += 1
        if use.context not in row["commands"]:
            row["commands"].append(use.context)
        if use.priority and use.priority not in row["priorities"]:
            row["priorities"].append(use.priority)
    return {
        "lines": [
            {
                "number": ln.number,
                "body": ln.body,
                "comment": ln.is_comment,
                "reachable": ln.number in a.reachable,
                "steady": ln.number in a.steady_state,
            }
            for ln in prog.lines
        ],
        "edges": [
            {"from": src, "to": dst, "kind": kind,
             "resolved": a.resolve_target(dst)}
            for src, dst, kind in a.edges
        ],
        "subroutines": [
            {
                "entry": entry,
                "returns": sub.returns,
                "callers": sorted(set(sub.callers)),
                "lines": len(sub.lines),
            }
            for entry, sub in sorted(a.subroutines.items())
        ],
        "points": sorted(points.values(), key=lambda p: p["name"]),
        "loop_entry": a.loop_entry,
    }


def api_renumber(body, ws=None):
    """Renumber, rewriting every line reference."""
    text = _text(body)
    prog = ppcl_parser.parse(text)
    result = formatter.renumber(
        prog,
        start=int(body.get("start", 10)),
        step=int(body.get("step", 10)),
        preserve_blocks=bool(body.get("preserve_blocks", False)),
        split_duplicates=bool(body.get("split_duplicates", False)),
        allow_duplicates=bool(body.get("allow_duplicates", False)),
    )
    return {
        "text": result.text,
        "ok": bool(result.text),
        "warnings": result.warnings,
        "rewritten": result.rewritten_references,
    }


#: Ceiling on simulated passes for one request. A program that branches in a
#: tight cycle would otherwise take the caller's patience with it.
RUN_MAX_PASSES = 2000


def api_run(body, ws=None):
    """Execute a program against a simulated panel and report what happened.

    The point of this over :func:`api_bench` is that it answers "did this
    statement fire, and did the write take" rather than "does the zone hold
    temperature". Priority arbitration is exact, so a command refused because
    the point is owned at a higher priority comes back named -- which is the
    answer to most "the schedule stopped working" questions.
    """
    from ..simulator import Clock, Panel, Simulator

    text = _text(body)
    prog = ppcl_parser.parse(text, name=body.get("name", "run"))
    if prog.errors:
        return {
            "ok": False,
            "error": "the program does not parse",
            "errors": [
                {"source_line": s, "line": l, "message": m}
                for s, l, m in prog.errors
            ],
        }

    panel = Panel()
    seed = body.get("points")
    if isinstance(seed, dict):
        try:
            panel.load(seed)
        except ValueError as exc:
            raise ApiError(str(exc))

    clock = Clock(hours=float(body.get("time", 8.0)),
                  day_of_week=int(body.get("day_of_week", 2)))
    sim = Simulator(prog, panel=panel, clock=clock,
                    firmware=_firmware(body))

    passes = int(body.get("passes", 10))
    passes = max(1, min(passes, RUN_MAX_PASSES))
    sim.run(passes=passes, seconds_per_pass=float(body.get("interval", 60.0)))

    return {
        "ok": True,
        "passes": passes,
        "points": {
            name: {
                "value": round(point.value, 4),
                "priority": point.priority,
                "runtime_s": round(point.runtime, 2),
            }
            for name, point in sorted(sim.panel.points.items())
        },
        "events": [
            {"time": round(e.time, 4), "line": e.line, "kind": e.kind,
             "text": e.text}
            for e in sim.events
        ],
        "blocked": [e.text for e in sim.events if e.kind == "blocked"],
        "warnings": sim.warnings,
        "starved": sim.starved_lines,
    }


def api_format(body, ws=None):
    text = _text(body)
    prog = ppcl_parser.parse(text)
    return {
        "text": formatter.format_text(
            prog, sort=bool(body.get("sort", True)),
            normalize=bool(body.get("normalize", False)),
        )
    }


def api_explain(body, ws=None):
    """Reference lookup for the editor's help panel."""
    topic = str(body.get("topic", "")).strip().upper()
    if not topic:
        raise ApiError("a topic is required")

    if topic in spec.ALL:
        cmd = spec.ALL[topic]
        return {
            "kind": "command",
            "name": cmd.name,
            "summary": cmd.summary,
            "signature": _signature(cmd),
            "params": [
                {"name": p.name, "kind": p.kind.value, "doc": p.doc,
                 "choices": list(p.choices)}
                for p in cmd.fixed
            ],
            "repeat": [
                {"name": p.name, "kind": p.kind.value, "doc": p.doc}
                for p in cmd.repeat
            ],
            "trailing": [
                {"name": p.name, "kind": p.kind.value, "doc": p.doc}
                for p in cmd.trailing
            ],
            "min_args": cmd.min_args(),
            "max_args": cmd.max_args(),
            "priority_arg": cmd.priority_arg,
            "time_based": cmd.time_based,
            "subroutine_safe": cmd.subroutine_safe,
            "if_target_safe": cmd.if_target_safe,
            "signature_known": cmd.signature_known,
            "point_types": sorted(cmd.point_types),
            "notes": list(cmd.notes),
            "see_also": list(cmd.see_also),
        }

    if topic in spec.POINT_TYPES:
        pt = spec.POINT_TYPES[topic]
        return {"kind": "point_type", "name": pt.name, "summary": pt.description,
                "detail": pt.kind, "addresses": list(pt.addresses)}

    key = topic if topic.startswith("@") else "@" + topic
    if key in spec.PRIORITY_RANK:
        return {
            "kind": "priority",
            "name": key,
            "summary": spec.PRIORITY_DESCRIPTIONS[key],
            "order": [
                {"name": n, "doc": spec.PRIORITY_DESCRIPTIONS[n]}
                for n in spec.PRIORITY_ORDER
            ],
        }

    if topic in spec.FUNCTIONS:
        return {"kind": "function", "name": topic,
                "summary": spec.FUNCTIONS[topic], "signature": "%s(value)" % topic}

    for r in linter.rule_catalog():
        if r.code == topic:
            return {
                "kind": "rule",
                "name": r.code,
                "summary": r.summary,
                "severity": r.default_severity.value,
                "detail": (r.func.__doc__ or "").strip(),
            }

    raise ApiError(
        "nothing known about %r. Try a command (ON, LOOP), a point type "
        "(LOOAP), a priority (EMER), or a rule code (E205)." % body.get("topic"),
        404,
    )


def _signature(cmd):
    parts = [p.name for p in cmd.fixed]
    if cmd.repeat:
        parts.append(
            "%s1,...,%s%d"
            % (cmd.repeat[0].name, cmd.repeat[-1].name, cmd.max_repeat)
        )
    parts.extend(p.name for p in cmd.trailing)
    sig = "%s(%s)" % (cmd.name, ",".join(parts))
    if cmd.priority_arg:
        sig += "   or   %s(@prior,...)" % cmd.name
    return sig


def api_meta(body=None, ws=None):
    """Everything the UI needs to render menus, autocomplete and help."""
    from ..plant import AHU_PRESETS, FAULT_KINDS, WEATHER_PRESETS

    from .. import blocks, helpdocs

    return {
        "commands": sorted(spec.ALL),
        "command_summaries": {n: c.summary for n, c in spec.ALL.items()},
        # Siemens' own Command Assist grouping, so the command list filters
        # the way the Desigo CC editor's does.
        "command_categories": spec.COMMAND_CATEGORIES,
        "time_based": sorted(spec.TIME_BASED_COMMANDS),
        "subroutine_unsafe": sorted(spec.SUBROUTINE_UNSAFE),
        "if_target_unsafe": sorted(spec.IF_TARGET_UNSAFE),
        "functions": sorted(spec.FUNCTIONS),
        "operators": sorted(spec.DOTTED_OPS),
        "priorities": list(spec.PRIORITY_ORDER),
        "priority_help": spec.PRIORITY_DESCRIPTIONS,
        "point_types": sorted(spec.POINT_TYPES),
        "bacnet_object_types": spec.BACNET_OBJECT_TYPES,
        "status_indicators": sorted(spec.STATUS_INDICATORS),
        "resident_points": sorted(spec.RESIDENT_POINTS),
        "reserved": sorted(spec.RESERVED_WORDS),
        "firmwares": [f.value for f in spec.Firmware],
        "limits": {
            "line_min": spec.LINE_MIN,
            "line_max": spec.LINE_MAX,
            "operands": {k.value: v for k, v in spec.MAX_OPERANDS.items()},
            "operators": spec.MAX_OPERATORS,
            "mmi_line": {k.value: v for k, v in spec.MMI_LINE_LIMIT.items()},
            "locals": 16,
        },
        "rules": [
            {"code": r.code, "summary": r.summary,
             "severity": r.default_severity.value}
            for r in linter.rule_catalog()
        ],
        "cell_actions": sequence.CELL_ACTIONS,
        "weather": sorted(WEATHER_PRESETS),
        "plant_presets": sorted(AHU_PRESETS),
        "faults": list(FAULT_KINDS),
        "block_categories": blocks.CATEGORIES,
        "help_contents": helpdocs.contents(),
    }


# --------------------------------------------------------------------------
# Endpoints: sequence documents
# --------------------------------------------------------------------------


def api_seq_parse(body, ws=None):
    """Text form -> document. Errors carry the line number for the editor."""
    text = _text(body)
    try:
        seq = sequence.parse(text)
    except sequence.SequenceSyntaxError as exc:
        return {
            "ok": False,
            "error": exc.message,
            "line": exc.line_no,
            "source": exc.line,
        }
    return {"ok": True, "document": sequence.to_dict(seq)}


def api_seq_render(body, ws=None):
    """Document -> text form."""
    doc = body.get("document")
    if not isinstance(doc, dict):
        raise ApiError("a document object is required")
    seq = sequence.from_dict(doc)
    return {"ok": True, "text": sequence.render(seq)}


def api_seq_compile(body, ws=None):
    """Document or text -> PPCL, then lint the result.

    The UI never shows generated code that has not been checked.
    """
    if "document" in body:
        seq = sequence.from_dict(body["document"])
    else:
        try:
            seq = sequence.parse(_text(body))
        except sequence.SequenceSyntaxError as exc:
            return {"ok": False, "error": exc.message, "line": exc.line_no,
                    "source": exc.line}
    try:
        text, warnings = sequence.compile_sequence(seq)
    except sequence.CompileError as exc:
        return {"ok": False, "error": str(exc), "line": 0, "source": ""}

    prog = ppcl_parser.parse(text, name=seq.name)
    diags = linter.lint(prog, firmware=_firmware(body))
    return {
        "ok": True,
        "text": text,
        "warnings": warnings,
        "diagnostics": _diag_payload(diags),
        "counts": linter.summarize(diags),
        "document": sequence.to_dict(seq),
    }


# --------------------------------------------------------------------------
# Endpoints: the test bench
# --------------------------------------------------------------------------


def api_bench(body, ws=None):
    """Run a program against simulated equipment and return the trends."""
    from ..plant import (
        Fault,
        TestBench,
        build_plant,
        default_bindings,
        default_checks,
    )

    text = _text(body)
    prog = ppcl_parser.parse(text, name=body.get("name", "bench"))
    if prog.errors:
        return {
            "ok": False,
            "error": "the program does not parse",
            "errors": [
                {"source_line": s, "line": l, "message": m}
                for s, l, m in prog.errors
            ],
        }

    seconds = float(body.get("seconds", 10800))
    seconds = max(60.0, min(seconds, 86400.0))
    dt = float(body.get("dt", 10.0))
    dt = max(1.0, min(dt, 60.0))

    plant = build_plant(
        {
            "preset": body.get("preset", "single_zone_ahu"),
            "weather": body.get("weather", "design_winter"),
        }
    )
    bench = TestBench(prog, plant, default_bindings())
    bench.sample_interval = max(10.0, seconds / 400.0)
    bench.clock.hours = float(body.get("start_hour", 5.0))

    # A request has to come back while someone is watching. The panel's real
    # rate is ~500 lines/second, and simulating a full day at that rate is tens
    # of millions of statement evaluations. Long runs get a lower rate, and the
    # response says so rather than quietly changing the model: the only thing
    # affected is how many times a non-time-gated statement repeats within a
    # timestep, which matters for accumulators and for nothing else.
    budget_warning = None
    full_rate = bench.lines_per_second
    if seconds * full_rate > BENCH_LINE_BUDGET:
        bench.lines_per_second = max(20.0, BENCH_LINE_BUDGET / seconds)
        budget_warning = (
            "This run is long, so the panel line rate was reduced from %d to "
            "%d lines/second to keep the request responsive. Timing-gated "
            "commands (LOOP, SAMPLE, TOD, WAIT) are unaffected; a statement "
            "that accumulates every pass will count fewer times. Use the CLI "
            "for a full-rate run."
            % (full_rate, bench.lines_per_second)
        )
    for check in default_checks():
        bench.add_check(check)
    for item in body.get("points", {}).items() if isinstance(
        body.get("points"), dict
    ) else []:
        bench.panel.load({item[0]: item[1]})
    for f in body.get("faults") or []:
        bench.add_fault(
            Fault(
                system=f.get("system", "AHU1"),
                kind=f.get("kind", ""),
                at_seconds=float(f.get("at", 0)),
                value=float(f.get("value", 0)),
            )
        )

    result = bench.run(seconds=seconds, dt=dt)
    variables = body.get("variables") or [
        "AHU1.actual_zone", "AHU1.actual_dat", "AHU1.coil_face",
        "AHU1.actual_mat", "AHU1.hw_position", "AHU1.cw_position",
        "AHU1.oa_position", "OUTSIDE",
    ]
    series = {}
    for var in variables:
        data = result.series(var)
        if data:
            series[var] = [[round(t, 1), round(v, 3)] for t, v in data]

    warnings = list(result.warnings)
    if budget_warning:
        warnings.insert(0, budget_warning)

    return {
        "ok": True,
        "passed": result.passed,
        "lines_per_second": bench.lines_per_second,
        "checks": [
            {"name": c.name, "passed": c.passed, "detail": c.detail}
            for c in result.checks
        ],
        "series": series,
        "available": result.variables(),
        "faults": result.faults,
        "warnings": warnings,
        "blocked": result.blocked,
        "duration": result.duration,
    }


# --------------------------------------------------------------------------
# Endpoints: files
# --------------------------------------------------------------------------


def api_files(body, ws=None):
    if ws is None:
        raise ApiError("no workspace is configured", 500)
    return {"root": ws.root, "files": ws.listing()}


def api_open(body, ws=None):
    if ws is None:
        raise ApiError("no workspace is configured", 500)
    path = ws.resolve(body.get("path"))
    if not os.path.isfile(path):
        raise ApiError("no such file: %s" % body.get("path"), 404)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return {"path": body.get("path"), "text": fh.read()}


def api_save(body, ws=None):
    if ws is None:
        raise ApiError("no workspace is configured", 500)
    path = ws.resolve(body.get("path"))
    text = _text(body)
    os.makedirs(os.path.dirname(path) or ws.root, exist_ok=True)
    if os.path.exists(path) and body.get("backup", True):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as src:
                previous = src.read()
            with open(path + ".bak", "w", encoding="utf-8", newline="\n") as dst:
                dst.write(previous)
        except OSError:
            pass  # a missing backup must not block the save
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return {"ok": True, "path": body.get("path"), "bytes": len(text)}


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------

ROUTES = {
    "/api/meta": api_meta,
    "/api/lint": api_lint,
    "/api/analyze": api_analyze,
    "/api/run": api_run,
    "/api/renumber": api_renumber,
    "/api/format": api_format,
    "/api/explain": api_explain,
    "/api/seq/parse": api_seq_parse,
    "/api/seq/render": api_seq_render,
    "/api/seq/compile": api_seq_compile,
    "/api/bench": api_bench,
    "/api/files": api_files,
    "/api/open": api_open,
    "/api/save": api_save,
}


_ide_loaded = False


def _ensure_ide_routes():
    """Merge the IDE endpoints into ROUTES, once.

    The import is deferred because :mod:`ppcl.web.api_ide` imports helpers
    from here, so importing it at module scope would be a cycle.

    They are merged **into** ``ROUTES`` rather than into a private copy.
    A copy would make ``ROUTES`` no longer the routing table -- anything that
    replaces a handler afterwards, including a test substituting a failing one,
    would be silently ignored.
    """
    global _ide_loaded
    if _ide_loaded:
        return
    _ide_loaded = True
    from .api_ide import IDE_ROUTES

    for path, handler in IDE_ROUTES.items():
        ROUTES.setdefault(path, handler)


def dispatch(path: str, body: dict, ws: Workspace = None):
    """Route a request. Returns ``(status, payload)``."""
    _ensure_ide_routes()
    handler = ROUTES.get(path)
    if handler is None:
        return 404, {"error": "no such endpoint: %s" % path}
    try:
        return 200, handler(body or {}, ws)
    except ApiError as exc:
        return exc.status, {"error": exc.message}
    except Exception as exc:  # a bug here must not take the server down
        return 500, {
            "error": "%s: %s" % (type(exc).__name__, exc),
            "traceback": traceback.format_exc(limit=6),
        }
