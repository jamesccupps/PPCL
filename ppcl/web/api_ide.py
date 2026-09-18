"""IDE endpoints: blocks, debugger, points, settings, transforms, completion.

Split out from :mod:`ppcl.web.api` so the core -- parse, lint, compile,
simulate, files -- stays readable. Same contract: every handler is a plain
function taking a decoded request dict and returning JSON, so the whole
surface is testable without starting a server.

The debugger is the only stateful thing here. A session lives in this process
and is addressed by an opaque id; sessions are capped and the oldest is
dropped, because a browser tab that goes away cannot tell us it has.
"""

from __future__ import annotations

import itertools
import os
import threading

from .. import blocks, helpdocs, linter, points, settings, spec, transforms
from .. import parser as ppcl_parser
from ..debug import Breakpoint, DebugError, DebugSession
from .api import ApiError, _diag_payload, _firmware, _text

#: How many debugger sessions this process will keep. One browser needs one.
MAX_SESSIONS = 8

_sessions = {}
_session_order = []
_session_lock = threading.Lock()
_session_ids = itertools.count(1)


# --------------------------------------------------------------------------
# Blocks
# --------------------------------------------------------------------------


def api_blocks_catalog(body, ws=None):
    """The palette: every block type, its pins, params and help."""
    return blocks.catalog_payload()


def api_blocks_compile(body, ws=None):
    """Diagram -> PPCL, then lint it. The UI never shows unchecked code."""
    raw = body.get("diagram")
    if not isinstance(raw, dict):
        raise ApiError("a diagram object is required")
    try:
        diagram = blocks.from_dict(raw)
    except (ValueError, KeyError, TypeError) as exc:
        raise ApiError("that diagram cannot be read: %s" % exc)

    try:
        text, warnings, diagnostics = blocks.compile_and_lint(
            diagram, firmware=_firmware(body)
        )
    except blocks.BlockError as exc:
        return {
            "ok": False,
            "error": exc.message,
            "block": exc.block_id,
        }

    order, feedback, _deferred = blocks.order_blocks(diagram)
    return {
        "ok": True,
        "text": text,
        "warnings": warnings,
        "diagnostics": _diag_payload(diagnostics),
        "counts": linter.summarize(diagnostics),
        "order": [b.id for b in order],
        "feedback": [{"from": a, "to": b} for a, b in feedback],
    }


def api_blocks_new(body, ws=None):
    """A starter diagram, so the canvas is never blank on first open."""
    return {"ok": True, "diagram": blocks.to_dict(_starter_diagram())}


def _starter_diagram():
    """Freeze protection and a scheduled fan -- the smallest useful example.

    Chosen because it exercises the three things that make a diagram more than
    a drawing: a latch that holds state, an interlock released at the same
    priority it was commanded at, and a schedule that must be evaluated on
    every pass.
    """
    d = blocks.Diagram(name="AHU-1 fan and freeze protection", equipment="AHU1")

    def add(block_id, block_type, x, y, label="", note="", **params):
        d.add(blocks.Block(id=block_id, type=block_type, x=x, y=y,
                           label=label, note=note, params=params))

    add("mat", "point_in", 40, 60, point="MAT", kind="analog")
    add("lo", "constant", 40, 160, value=38)
    add("hi", "constant", 40, 250, value=45)
    add("trip", "compare", 230, 90, op="<")
    add("clear", "compare", 230, 220, op=">")
    add("freeze", "latch", 420, 140, label="FREEZE", priority="set",
        note="Freeze trip: latches low, clears only above the reset limit")
    add("sched", "schedule", 40, 380, label="OCC", on="6:00", off="18:00",
        mode=1)
    add("safe", "not", 620, 140)
    add("run", "and", 780, 300, label="RUN")
    add("fan", "command", 960, 300, point="SFAN", action="on",
        priority="@EMER", release_when_false="yes")

    d.connect("mat", "out", "trip", "a")
    d.connect("lo", "out", "trip", "b")
    d.connect("mat", "out", "clear", "a")
    d.connect("hi", "out", "clear", "b")
    d.connect("trip", "out", "freeze", "set")
    d.connect("clear", "out", "freeze", "reset")
    d.connect("freeze", "out", "safe", "in")
    d.connect("sched", "out", "run", "a")
    d.connect("safe", "out", "run", "b")
    d.connect("run", "out", "fan", "when")
    return d


# --------------------------------------------------------------------------
# Debugger
# --------------------------------------------------------------------------


def _session(body):
    session_id = str(body.get("session", ""))
    with _session_lock:
        session = _sessions.get(session_id)
    if session is None:
        raise ApiError(
            "that debug session has expired; start it again", 404
        )
    return session


def api_debug_start(body, ws=None):
    """Load a program into a fresh debug session."""
    text = _text(body)
    try:
        session = DebugSession(text, firmware=_firmware(body))
    except DebugError as exc:
        return {"ok": False, "error": str(exc)}

    seed = body.get("points")
    if isinstance(seed, dict):
        try:
            session.sim.panel.load(seed)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
    if body.get("start_hour") is not None:
        session.sim.clock.hours = float(body["start_hour"])
    for name in body.get("watch") or []:
        session.add_watch(str(name))

    session_id = "dbg%d" % next(_session_ids)
    with _session_lock:
        _sessions[session_id] = session
        _session_order.append(session_id)
        while len(_session_order) > MAX_SESSIONS:
            _sessions.pop(_session_order.pop(0), None)
    return {"ok": True, "session": session_id, "state": session.state()}


def api_debug_state(body, ws=None):
    return {"ok": True, "state": _session(body).state()}


def api_debug_step(body, ws=None):
    session = _session(body)
    mode = str(body.get("mode", "into"))
    try:
        if mode == "over":
            stop = session.step_over()
        elif mode == "out":
            stop = session.step_out()
        elif mode == "run":
            stop = session.run()
        elif mode == "to":
            stop = session.run_to(int(body.get("line", 0)))
        else:
            stop = session.step(int(body.get("count", 1)))
    except DebugError as exc:
        raise ApiError(str(exc))
    session.stopped = stop
    return {
        "ok": True,
        "stop": {"reason": stop.reason, "line": stop.line,
                 "detail": stop.detail},
        "state": session.state(),
    }


def api_debug_breakpoint(body, ws=None):
    session = _session(body)
    action = str(body.get("action", "add"))
    try:
        if action == "add":
            session.add_breakpoint(
                Breakpoint(
                    kind=str(body.get("kind", "line")),
                    line=int(body["line"]) if body.get("line") else None,
                    point=body.get("point") or None,
                    expression=body.get("expression") or None,
                    skip=int(body.get("skip", 0) or 0),
                    label=str(body.get("label", "")),
                )
            )
        elif action == "remove":
            session.remove_breakpoint(int(body.get("index", -1)))
        elif action == "clear":
            session.clear_breakpoints()
        elif action == "toggle":
            index = int(body.get("index", -1))
            if not 0 <= index < len(session.breakpoints):
                raise DebugError("no such breakpoint")
            bp = session.breakpoints[index]
            bp.enabled = not bp.enabled
        else:
            raise ApiError("unknown breakpoint action %r" % action)
    except DebugError as exc:
        return {"ok": False, "error": str(exc), "state": session.state()}
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "state": session.state()}
    return {"ok": True, "state": session.state()}


def api_debug_point(body, ws=None):
    """Read, override or watch a point mid-run."""
    session = _session(body)
    action = str(body.get("action", "set"))
    name = str(body.get("name", "")).strip()
    if not name and action != "list":
        raise ApiError("a point name is required")
    try:
        if action == "set":
            session.set_point(name, body.get("value", 0.0),
                              str(body.get("priority", "@NONE")))
        elif action == "watch":
            session.add_watch(name)
        elif action == "unwatch":
            session.remove_watch(name)
        elif action == "list":
            return {"ok": True, "points": session.points()}
        else:
            raise ApiError("unknown point action %r" % action)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "state": session.state()}
    return {"ok": True, "state": session.state()}


def api_debug_reset(body, ws=None):
    session = _session(body)
    session.reset(keep_points=bool(body.get("keep_points", False)))
    return {"ok": True, "state": session.state()}


def api_debug_stop(body, ws=None):
    session_id = str(body.get("session", ""))
    with _session_lock:
        _sessions.pop(session_id, None)
        if session_id in _session_order:
            _session_order.remove(session_id)
    return {"ok": True}


# --------------------------------------------------------------------------
# Point database
# --------------------------------------------------------------------------


def _database(ws):
    return getattr(ws, "point_db", None) if ws is not None else None


def api_points_import(body, ws=None):
    """Load a point export. CSV or JSON, decided from the content."""
    if ws is None:
        raise ApiError("no workspace is configured", 500)
    if body.get("path"):
        path = ws.resolve(body["path"])
        if not os.path.isfile(path):
            raise ApiError("no such file: %s" % body["path"], 404)
        try:
            db = points.load_file(path)
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        source = body["path"]
    else:
        text = _text(body)
        try:
            db = points.load(text, source=body.get("name", "pasted"))
        except (ValueError, Exception) as exc:
            return {"ok": False, "error": str(exc)}
        source = db.source

    ws.point_db = db
    return {
        "ok": True,
        "source": source,
        "count": len(db),
        "ignored_columns": db.ignored_columns,
        "problems": db.problems,
        "sample": [p.to_dict() for p in list(db.points.values())[:20]],
    }


def api_points_search(body, ws=None):
    db = _database(ws)
    if db is None:
        return {"ok": True, "loaded": False, "points": [], "count": 0}
    found = db.search(body.get("query", ""), int(body.get("limit", 40)))
    return {
        "ok": True,
        "loaded": True,
        "count": len(db),
        "source": db.source,
        "points": [p.to_dict() for p in found],
    }


def api_points_clear(body, ws=None):
    if ws is not None:
        ws.point_db = None
    return {"ok": True}


def api_points_check(body, ws=None):
    """Unresolved point references -- the offline equivalent of Desigo's U."""
    db = _database(ws)
    if db is None:
        return {"ok": True, "loaded": False, "unresolved": []}
    program = ppcl_parser.parse(_text(body), name=body.get("name", "editor"))
    return {
        "ok": True,
        "loaded": True,
        "count": len(db),
        "unresolved": db.unresolved(program),
    }


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def _settings(ws):
    if ws is None:
        raise ApiError("no workspace is configured", 500)
    existing = getattr(ws, "settings", None)
    if existing is None:
        existing = settings.Settings(ws.root).load()
        ws.settings = existing
    return existing


def api_settings_get(body, ws=None):
    current = _settings(ws)
    return {
        "ok": True,
        "values": current.values,
        "schema": current.schema(),
        "groups": current.groups(),
        "problems": current.problems,
        "path": current.path,
    }


def api_settings_save(body, ws=None):
    current = _settings(ws)
    rejected = current.update(body.get("values") or {})
    if rejected:
        return {"ok": False, "rejected": rejected, "values": current.values}
    try:
        current.save()
    except OSError as exc:
        return {"ok": False, "rejected": ["could not write %s: %s"
                                          % (current.path, exc)],
                "values": current.values}
    return {"ok": True, "values": current.values, "path": current.path}


def api_settings_reset(body, ws=None):
    current = _settings(ws)
    current.values = settings.defaults()
    return {"ok": True, "values": current.values, "schema": current.schema()}


# --------------------------------------------------------------------------
# Source transforms
# --------------------------------------------------------------------------

TRANSFORMS = {
    "expand_defines": lambda text, body: transforms.expand_defines(text),
    "collapse_defines": lambda text, body: transforms.collapse_defines(text),
    "separators_dot": lambda text, body: transforms.swap_separators(text, "."),
    "separators_underscore":
        lambda text, body: transforms.swap_separators(text, "_"),
    "disable": lambda text, body: transforms.set_disabled(
        text, body.get("lines") or [], True),
    "enable": lambda text, body: transforms.set_disabled(
        text, body.get("lines") or [], False),
    "toggle_comment": lambda text, body: transforms.toggle_comments(
        text, body.get("lines") or []),
    "clone": lambda text, body: transforms.clone_lines(
        text,
        int(body.get("first", 0)),
        int(body.get("last", 0)),
        body.get("renames") or {},
        start=body.get("start") or None,
        step=body.get("step") or None,
    ),
}


def api_transform(body, ws=None):
    """Run one source transform and report exactly what it changed."""
    op = str(body.get("op", ""))
    handler = TRANSFORMS.get(op)
    if handler is None:
        raise ApiError(
            "unknown transform %r; expected one of %s"
            % (op, ", ".join(sorted(TRANSFORMS)))
        )
    text = _text(body)
    try:
        result = handler(text, body)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "text": result.text,
        "changed": result.changed,
        "notes": result.notes,
        "warnings": result.warnings,
        "defines": transforms.find_defines(result.text),
        "disabled": transforms.disabled_lines(result.text),
    }


# --------------------------------------------------------------------------
# Command Assist
# --------------------------------------------------------------------------


def api_complete(body, ws=None):
    """Completions for the caret position, plus the signature being filled.

    Mirrors the three parts of the Desigo CC Command Assist -- the filtered
    command list, the attribute editor showing statement syntax, and the quick
    information tool tip -- since that is the interaction an engineer coming
    from Desigo already knows.
    """
    text = _text(body)
    offset = int(body.get("offset", len(text)))
    offset = max(0, min(offset, len(text)))
    line_start = text.rfind("\n", 0, offset) + 1
    prefix = text[line_start:offset]

    word = ""
    for ch in reversed(prefix):
        if ch.isalnum() or ch in "_$@%.:":
            word = ch + word
        else:
            break

    signature = _open_call(prefix)
    db = _database(ws)
    items = []

    if word.startswith("@"):
        for name in spec.PRIORITY_ORDER:
            if name.upper().startswith(word.upper()):
                items.append({
                    "text": name, "kind": "priority",
                    "detail": spec.PRIORITY_DESCRIPTIONS[name],
                })
    else:
        upper = word.upper()
        for name, command in sorted(spec.ALL.items()):
            if upper and not name.startswith(upper):
                continue
            items.append({
                "text": name,
                "kind": "command",
                "category": spec.category_of(name) or "",
                "detail": command.summary,
                "signature": _signature_text(command),
                "time_based": command.time_based,
                "insert": "%s(" % name,
            })
        for name, doc in sorted(spec.FUNCTIONS.items()):
            if not upper or name.startswith(upper):
                items.append({"text": name, "kind": "function",
                              "category": "Arithmetic Function",
                              "detail": doc, "insert": "%s(" % name})
        for name in sorted(spec.RESIDENT_POINTS):
            if not upper or name.upper().startswith(upper):
                items.append({"text": name, "kind": "resident",
                              "detail": "resident point"})
        for name in sorted(spec.STATUS_INDICATORS):
            if upper and name.upper().startswith(upper):
                items.append({"text": name, "kind": "status",
                              "detail": "point status"})
        if db is not None and len(word) >= 2:
            for record in db.search(word, limit=25):
                items.append({
                    "text": record.name,
                    "kind": "point",
                    "detail": " ".join(
                        p for p in (record.ptype, record.description) if p
                    ) or "point",
                    "ptype": record.ptype,
                })

    return {
        "ok": True,
        "word": word,
        "replace_from": offset - len(word),
        "items": items[:60],
        "signature": signature,
        "categories": list(spec.COMMAND_CATEGORIES),
    }


def _open_call(prefix):
    """Which command the caret is inside, and which argument it is on."""
    depth = 0
    index = len(prefix) - 1
    arg = 0
    while index >= 0:
        ch = prefix[index]
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth == 0:
                name = ""
                j = index - 1
                while j >= 0 and (prefix[j].isalnum() or prefix[j] == "_"):
                    name = prefix[j] + name
                    j -= 1
                command = spec.ALL.get(name.upper())
                if command is None:
                    return None
                return {
                    "name": command.name,
                    "summary": command.summary,
                    "signature": _signature_text(command),
                    "argument": arg,
                    "params": _param_list(command),
                    "notes": list(command.notes),
                    "priority_arg": command.priority_arg,
                    "time_based": command.time_based,
                    "subroutine_safe": command.subroutine_safe,
                    "if_target_safe": command.if_target_safe,
                    "signature_known": command.signature_known,
                    "manual": getattr(command, "manual", ""),
                }
            depth -= 1
        elif ch == "," and depth == 0:
            arg += 1
        index -= 1
    return None


def _param_list(command):
    out = [
        {"name": p.name, "kind": p.kind.value, "doc": p.doc,
         "choices": list(p.choices), "repeat": False}
        for p in command.fixed
    ]
    for p in command.repeat:
        out.append({"name": p.name, "kind": p.kind.value, "doc": p.doc,
                    "choices": list(p.choices), "repeat": True,
                    "max": command.max_repeat})
    out.extend(
        {"name": p.name, "kind": p.kind.value, "doc": p.doc,
         "choices": list(p.choices), "repeat": False}
        for p in command.trailing
    )
    return out


def _signature_text(command):
    parts = [p.name for p in command.fixed]
    if command.repeat:
        parts.append("%s1,...,%s%d" % (command.repeat[0].name,
                                       command.repeat[-1].name,
                                       command.max_repeat))
    parts.extend(p.name for p in command.trailing)
    return "%s(%s)" % (command.name, ",".join(parts))


# --------------------------------------------------------------------------
# Help
# --------------------------------------------------------------------------


def api_help(body, ws=None):
    topic = str(body.get("topic", "")).strip()
    query = str(body.get("query", "")).strip()
    if query:
        return {"ok": True, "results": helpdocs.search(query)}
    if not topic:
        return {"ok": True, "contents": helpdocs.contents()}
    page = helpdocs.page(topic)
    if page is None:
        raise ApiError("no help topic called %r" % topic, 404)
    return {"ok": True, "page": page}


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

IDE_ROUTES = {
    "/api/blocks/catalog": api_blocks_catalog,
    "/api/blocks/compile": api_blocks_compile,
    "/api/blocks/new": api_blocks_new,
    "/api/debug/start": api_debug_start,
    "/api/debug/state": api_debug_state,
    "/api/debug/step": api_debug_step,
    "/api/debug/breakpoint": api_debug_breakpoint,
    "/api/debug/point": api_debug_point,
    "/api/debug/reset": api_debug_reset,
    "/api/debug/stop": api_debug_stop,
    "/api/points/import": api_points_import,
    "/api/points/search": api_points_search,
    "/api/points/clear": api_points_clear,
    "/api/points/check": api_points_check,
    "/api/settings": api_settings_get,
    "/api/settings/save": api_settings_save,
    "/api/settings/reset": api_settings_reset,
    "/api/transform": api_transform,
    "/api/complete": api_complete,
    "/api/help": api_help,
}
