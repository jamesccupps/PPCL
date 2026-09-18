"""Command-line interface for the PPCL Workbench."""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap

from . import __version__, analyzer, formatter, generator, linter, parser as ppcl_parser
from . import report as report_mod
from . import spec
from .diagnostics import Severity

SEVERITY_ORDER = ["error", "warning", "info", "style"]


# --------------------------------------------------------------------------
# Input helpers
# --------------------------------------------------------------------------


def collect_files(paths, recursive: bool = True):
    """Expand paths into a sorted list of PPCL files."""
    out = []
    for path in paths:
        if os.path.isdir(path):
            for root, _dirs, names in os.walk(path):
                for name in sorted(names):
                    if name.lower().endswith((".ppcl", ".pcl", ".txt")):
                        out.append(os.path.join(root, name))
                if not recursive:
                    break
        else:
            out.append(path)
    return out


def load_point_types(path):
    """Load a point database: {"SFAN": "LDO", ...} or a CSV of name,type."""
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    if path.lower().endswith(".json"):
        data = json.loads(text)
        return {
            k.upper(): (v.get("type") if isinstance(v, dict) else v)
            for k, v in data.items()
        }
    out = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0] and not line.lstrip().startswith("#"):
            out[parts[0].upper()] = parts[1].upper()
    return out


def _min_rank(name):
    return SEVERITY_ORDER.index(name)


# --------------------------------------------------------------------------
# lint
# --------------------------------------------------------------------------


def cmd_lint(args):
    files = collect_files(args.paths)
    if not files:
        print("no PPCL files found", file=sys.stderr)
        return 2

    point_types = load_point_types(args.points)
    firmware = spec.Firmware(args.firmware)
    disabled = set(args.disable or [])

    panel_report = None
    if getattr(args, "report", None):
        panel_report = report_mod.parse_file(args.report)
        if not panel_report.states:
            print("no program lines found in %s -- is it a PPCL DISPLAY "
                  "REPORT?" % args.report, file=sys.stderr)
            return 2

    sizes = {}
    programs = {}
    for path in files:
        prog = ppcl_parser.parse_file(path)
        if panel_report is not None:
            report_mod.apply_to(panel_report, prog)
        programs[path] = prog
        sizes[path] = len(prog.executable_lines())

    all_diags = []
    threshold = _min_rank(args.min_severity)
    worst = 3

    for path in files:
        prog = programs[path]
        analysis = analyzer.analyze(prog)
        ctx_extra = {k: v for k, v in sizes.items() if k != path}
        diags = linter.lint(
            prog,
            firmware=firmware,
            point_types=point_types,
            disabled=disabled,
            analysis=analysis,
        )
        if panel_report is not None:
            diags = list(diags) + report_mod.diagnostics(panel_report, prog)
            diags = [d for d in diags if d.code not in disabled]
            diags.sort(key=lambda d: (d.line or 0, d.code))
        diags = [d for d in diags if _min_rank(d.severity.value) <= threshold]
        for d in diags:
            worst = min(worst, _min_rank(d.severity.value))
        all_diags.append((path, prog, analysis, diags))

    if args.format == "json":
        payload = [
            {
                "file": path,
                "program": prog.name,
                "executable_lines": len(prog.executable_lines()),
                "diagnostics": [d.to_dict() for d in diags],
            }
            for path, prog, _a, diags in all_diags
        ]
        print(json.dumps(payload, indent=2))
    else:
        for path, prog, analysis, diags in all_diags:
            if not diags and not args.verbose:
                continue
            print("\n%s" % path)
            print("-" * len(path))
            if args.verbose:
                print(
                    "  %d lines, %d executable, %d in the main loop, "
                    "%d run once at startup"
                    % (
                        len(prog.lines),
                        len(prog.executable_lines()),
                        len(analysis.steady_state),
                        len(analysis.one_shot),
                    )
                )
            for d in diags:
                print(d.format(path, color=args.color))
        totals = {s: 0 for s in SEVERITY_ORDER}
        for _p, _pr, _a, diags in all_diags:
            for d in diags:
                totals[d.severity.value] += 1
        print(
            "\n%d file(s): %d error, %d warning, %d info, %d style"
            % (
                len(files),
                totals["error"],
                totals["warning"],
                totals["info"],
                totals["style"],
            )
        )

    if args.strict:
        return 1 if worst <= _min_rank("warning") else 0
    return 1 if worst == 0 else 0


# --------------------------------------------------------------------------
# fmt / renumber
# --------------------------------------------------------------------------


def cmd_fmt(args):
    for path in collect_files(args.paths):
        prog = ppcl_parser.parse_file(path)
        text = formatter.format_text(
            prog, pad=args.pad, sort=not args.no_sort, normalize=args.normalize
        )
        _write_or_print(path, text, args)
    return 0


def cmd_renumber(args):
    rc = 0
    for path in collect_files(args.paths):
        prog = ppcl_parser.parse_file(path)
        result = formatter.renumber(
            prog,
            start=args.start,
            step=args.step,
            preserve_blocks=args.preserve_blocks,
            block_size=args.block_size,
            pad=args.pad,
            split_duplicates=args.split_duplicates,
            allow_duplicates=args.allow_duplicates,
        )
        for w in result.warnings:
            print("%s: %s" % (path, w), file=sys.stderr)
        if not result.text:
            rc = 1
            continue
        print(
            "%s: %d lines renumbered, %d line references rewritten"
            % (path, len(result.mapping), result.rewritten_references),
            file=sys.stderr,
        )
        _write_or_print(path, result.text, args)
    return rc


def _write_or_print(path, text, args):
    if args.in_place:
        if args.backup:
            with open(path + ".bak", "w", encoding="utf-8", newline="\n") as fh:
                with open(path, "r", encoding="utf-8", errors="replace") as src:
                    fh.write(src.read())
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print("wrote %s" % path, file=sys.stderr)
    elif getattr(args, "output", None):
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print("wrote %s" % args.output, file=sys.stderr)
    else:
        sys.stdout.write(text)


# --------------------------------------------------------------------------
# run (simulate)
# --------------------------------------------------------------------------


def cmd_run(args):
    from .simulator import Clock, Panel, Simulator

    prog = ppcl_parser.parse_file(args.path)
    if prog.errors:
        for src, line, msg in prog.errors:
            print("parse error at source line %s: %s" % (src, msg), file=sys.stderr)

    panel = Panel()
    if args.scenario:
        with open(args.scenario, "r", encoding="utf-8") as fh:
            scenario = json.load(fh)
        panel.load(scenario.get("points", scenario))
        clock_cfg = scenario.get("clock", {})
    else:
        clock_cfg = {}

    for item in args.set or []:
        name, _, value = item.partition("=")
        panel.load({name.strip(): value.strip()})

    clock = Clock(
        hours=args.time if args.time is not None else clock_cfg.get("hours", 8.0),
        day_of_week=clock_cfg.get("day_of_week", 2),
        day_of_month=clock_cfg.get("day_of_month", 15),
        month=clock_cfg.get("month", 6),
    )

    sim = Simulator(prog, panel=panel, clock=clock)
    sim.run(passes=args.passes, seconds_per_pass=args.interval)

    if args.format == "json":
        print(
            json.dumps(
                {
                    "points": {
                        k: {"value": p.value, "priority": p.priority,
                            "runtime_s": round(p.runtime, 2)}
                        for k, p in sorted(sim.panel.points.items())
                    },
                    "events": [
                        {"time": round(e.time, 4), "line": e.line,
                         "kind": e.kind, "text": e.text}
                        for e in sim.events
                    ],
                    "warnings": sim.warnings,
                },
                indent=2,
            )
        )
        return 0

    if args.trace:
        print("Trace")
        print("-----")
        for e in sim.events:
            if args.trace == "writes" and e.kind not in ("write", "blocked"):
                continue
            print("  %05.2fh  line %-6s %-8s %s"
                  % (e.time, e.line, e.kind, e.text))
        print()

    print("Final point states")
    print("------------------")
    for name, pt in sorted(sim.panel.points.items()):
        flag = "" if pt.priority == "@NONE" else "   <-- held at %s" % pt.priority
        print("  %-28s %-10s %s%s"
              % (name, _fmt(pt.value), pt.priority, flag))

    blocked = [e for e in sim.events if e.kind == "blocked"]
    if blocked:
        print("\n%d command(s) were blocked by point priority:" % len(blocked))
        seen = set()
        for e in blocked:
            if e.text in seen:
                continue
            seen.add(e.text)
            print("  line %s: %s" % (e.line, e.text))

    if sim.warnings:
        print("\nSimulator notes")
        print("---------------")
        for w in sim.warnings:
            print("  - %s" % w)
    return 0


def _fmt(value):
    return str(int(value)) if value == int(value) else "%.4g" % value


# --------------------------------------------------------------------------
# bench (simulate against equipment)
# --------------------------------------------------------------------------


def cmd_bench(args):
    from .plant import (
        AHU_PRESETS,
        Check,
        Fault,
        FAULT_KINDS,
        TestBench,
        WEATHER_PRESETS,
        build_from_scenario,
        build_plant,
        default_bindings,
        default_checks,
        load_scenario,
    )

    if args.list_faults:
        print("Faults the bench can inject:")
        for kind in FAULT_KINDS:
            print("  %s" % kind)
        print("\nPlant presets: %s" % ", ".join(sorted(AHU_PRESETS)))
        print("Weather presets: %s" % ", ".join(sorted(WEATHER_PRESETS)))
        return 0

    prog = ppcl_parser.parse_file(args.path)
    if prog.errors:
        for src, line, msg in prog.errors:
            print("parse error at source line %s: %s" % (src, msg), file=sys.stderr)
        return 1

    # A program that does not lint is not worth benching.
    fatal = [d for d in linter.lint(prog) if d.severity is Severity.ERROR]
    if fatal and not args.force:
        print("%s has %d lint error(s); fix them or pass --force:"
              % (args.path, len(fatal)), file=sys.stderr)
        for d in fatal:
            print("  " + d.format(args.path).splitlines()[0], file=sys.stderr)
        return 1

    if args.scenario:
        bench = build_from_scenario(prog, load_scenario(args.scenario))
    else:
        plant = build_plant({
            "preset": args.preset,
            "weather": args.weather,
        })
        bench = TestBench(prog, plant, default_bindings())
        bench.sample_interval = args.sample
        for check in default_checks():
            bench.add_check(check)

    for item in args.set or []:
        name, _, value = item.partition("=")
        bench.panel.load({name.strip(): value.strip()})

    for item in args.fault or []:
        parts = [p.strip() for p in item.split(",")]
        if len(parts) < 3:
            print("--fault needs SYSTEM,KIND,AT[,VALUE] (got %r)" % item,
                  file=sys.stderr)
            return 2
        bench.add_fault(
            Fault(
                system=parts[0],
                kind=parts[1],
                at_seconds=float(parts[2]),
                value=float(parts[3]) if len(parts) > 3 else 0.0,
            )
        )

    result = bench.run(seconds=args.seconds, dt=args.dt)

    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2))
        return 0 if result.passed else 1

    if args.format == "csv":
        variables = args.chart or result.variables()
        print("elapsed,hours," + ",".join(variables))
        for sample in result.history:
            row = [
                "%.6g" % sample["values"].get(v, float("nan")) for v in variables
            ]
            print("%.0f,%.4f,%s" % (sample["elapsed"], sample["hours"],
                                    ",".join(row)))
        return 0 if result.passed else 1

    print("Bench: %s" % args.path)
    print("  %s plant, %s weather, %.0f minutes at %gs steps"
          % (args.preset, args.weather, args.seconds / 60.0, args.dt))
    if result.faults:
        print("\nFaults injected")
        print("---------------")
        for f in result.faults:
            print("  %s" % f)

    charted = args.chart or [
        "AHU1.actual_zone", "AHU1.actual_dat", "AHU1.coil_face",
        "AHU1.hw_position", "AHU1.cw_position",
    ]
    print("\nTrends")
    print("------")
    for var in charted:
        series = result.series(var)
        if not series:
            print("  %-24s (no data)" % var)
            continue
        print(_sparkline(var, series))

    if result.blocked:
        print("\nCommands blocked by point priority")
        print("----------------------------------")
        for b in result.blocked:
            print("  %s" % b)

    if result.checks:
        print("\nChecks")
        print("------")
        for c in result.checks:
            mark = "PASS" if c.passed else "FAIL"
            print("  [%s] %-38s %s" % (mark, c.name, c.detail))

    if result.warnings:
        print("\nNotes")
        print("-----")
        for w in result.warnings:
            print("  - %s" % w)

    print("\n%s" % ("PASSED" if result.passed else "FAILED"))
    return 0 if result.passed else 1


def _sparkline(name, series, width=56):
    """A single-line trend, enough to see shape and range in a terminal."""
    blocks = " .:-=+*#%@"
    values = [v for _t, v in series]
    lo, hi = min(values), max(values)
    span = hi - lo
    step = max(1, len(values) // width)
    sampled = values[::step][:width]
    if span < 1e-9:
        bar = blocks[0] * len(sampled)
    else:
        bar = "".join(
            blocks[min(len(blocks) - 1, int((v - lo) / span * (len(blocks) - 1)))]
            for v in sampled
        )
    return "  %-24s %8.1f |%s| %-8.1f  end %.1f" % (
        name[:24], lo, bar, hi, values[-1]
    )


# --------------------------------------------------------------------------
# new (generate)
# --------------------------------------------------------------------------


def cmd_new(args):
    kwargs = {}
    for item in args.option or []:
        key, _, value = item.partition("=")
        kwargs[key.strip()] = value.strip()

    if args.author:
        kwargs["author"] = args.author
    if args.organization:
        kwargs["organization"] = args.organization

    template = generator.TEMPLATES.get(args.template)
    if template is None:
        print(
            "unknown template %r; available: %s"
            % (args.template, ", ".join(sorted(generator.TEMPLATES))),
            file=sys.stderr,
        )
        return 2

    if args.template == "skeleton":
        text = template(args.title or "New program", **kwargs)
    elif args.template == "schedule":
        points = tuple(p.strip() for p in (args.points_list or "SFAN").split(","))
        text = template(points, title=args.title or "", **kwargs)
    elif args.template == "leadlag":
        pumps = tuple(p.strip() for p in (args.points_list or "PMP1,PMP2").split(","))
        text = template(pumps=pumps, title=args.title or "", **kwargs)
    else:
        if args.title:
            kwargs.setdefault("title", args.title)
        text = template(**kwargs)

    # Never hand back generated code without checking it against our own rules.
    prog = ppcl_parser.parse(text, name=args.title or args.template)
    diags = [
        d
        for d in linter.lint(prog)
        if d.severity in (Severity.ERROR, Severity.WARNING)
    ]

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print("wrote %s" % args.output, file=sys.stderr)
    else:
        sys.stdout.write(text)

    if diags:
        print("\ngenerated program has %d finding(s):" % len(diags), file=sys.stderr)
        for d in diags:
            print("  " + d.format().splitlines()[0], file=sys.stderr)
    else:
        print("generated program lints clean", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------
# seq (sequence documents)
# --------------------------------------------------------------------------

STARTER_SEQUENCE = '''sequence "New sequence"
  equipment AHU1
  author %(author)s
  description "Describe what this equipment is supposed to do."

points
  SFAN    digital output   supply fan
  OADPR   analog  output   outside air damper
  HVLV    analog  output   heating valve
  MAT     analog  input    mixed air temperature
  DAT     analog  input    discharge air temperature
  ZNT     analog  input    zone temperature
  DASP    analog  virtual  discharge air setpoint

modes
  Unoccupied  otherwise
  Occupied    when TIME between 6:00 and 18:00

table
              Unoccupied  Occupied
  SFAN        off         on
  OADPR       closed      20
  HVLV        closed      modulate

interlock Freeze
  description "Low mixed air temperature"
  trip when MAT < 38
  reset when MAT > 45
  force SFAN off at emer

reset DASP from ZNT
  68 -> 95
  74 -> 55

loop Discharge
  measure DAT
  output HVLV
  setpoint DASP
  acting reverse
  throttling 10
  range 0 to 100
'''


def _load_sequence(path):
    from ppcl import sequence

    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    if path.lower().endswith(".json"):
        return sequence.loads(text)
    return sequence.parse(text)


def cmd_seq(args):
    from ppcl import sequence
    from ppcl.sequence import CompileError, SequenceSyntaxError

    action = args.action

    if action == "new":
        text = STARTER_SEQUENCE % {"author": args.author or "your name"}
        if args.output:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            print("wrote %s" % args.output, file=sys.stderr)
        else:
            sys.stdout.write(text)
        return 0

    try:
        seq = _load_sequence(args.path)
    except SequenceSyntaxError as exc:
        print("%s: %s" % (args.path, exc), file=sys.stderr)
        return 1

    if action == "fmt":
        text = sequence.render(seq)
        if args.in_place:
            with open(args.path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            print("wrote %s" % args.path, file=sys.stderr)
        else:
            sys.stdout.write(text)
        return 0

    if action == "json":
        print(sequence.dumps(seq))
        return 0

    if action == "text":
        sys.stdout.write(sequence.render(seq))
        return 0

    # compile / check / bench all need the compiled program.
    try:
        text, warnings = sequence.compile_sequence(seq)
    except CompileError as exc:
        print("%s: cannot compile: %s" % (args.path, exc), file=sys.stderr)
        return 1

    prog = ppcl_parser.parse(text, name=seq.name)
    diags = linter.lint(prog, firmware=spec.Firmware(args.firmware))
    bad = [d for d in diags if d.severity in (Severity.ERROR, Severity.WARNING)]

    if action == "check":
        print("%s" % args.path)
        print("  %s" % seq.name)
        print("  %d point(s), %d mode(s), %d interlock(s), %d reset(s), "
              "%d loop(s), %d rule(s)"
              % (len(seq.points), len(seq.modes), len(seq.interlocks),
                 len(seq.resets), len(seq.loops), len(seq.rules)))
        print("  compiles to %d program lines"
              % len([l for l in text.splitlines() if l.strip()]))
        if warnings:
            print("\nDocument warnings")
            print("-----------------")
            for w in warnings:
                print("  - %s" % w)
        if bad:
            print("\nCompiled program findings")
            print("-------------------------")
            for d in bad:
                print(d.format(seq.name))
        else:
            print("\nThe compiled program lints clean.")
        return 1 if bad else 0

    if action == "compile":
        if args.output:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            print("wrote %s" % args.output, file=sys.stderr)
        else:
            sys.stdout.write(text)
        for w in warnings:
            print("warning: %s" % w, file=sys.stderr)
        for d in bad:
            print(d.format(seq.name), file=sys.stderr)
        return 1 if bad else 0

    if action == "bench":
        from .plant import (
            Fault,
            TestBench,
            build_plant,
            default_bindings,
            default_checks,
        )

        plant = build_plant({"preset": args.preset, "weather": args.weather})
        bench = TestBench(prog, plant, default_bindings())
        for check in default_checks():
            bench.add_check(check)
        for item in args.fault or []:
            parts = [p.strip() for p in item.split(",")]
            bench.add_fault(
                Fault(
                    system=parts[0], kind=parts[1], at_seconds=float(parts[2]),
                    value=float(parts[3]) if len(parts) > 3 else 0.0,
                )
            )
        result = bench.run(seconds=args.seconds, dt=5.0)
        print("%s on %s weather" % (seq.name, args.weather))
        for c in result.checks:
            print("  [%s] %-38s %s"
                  % ("PASS" if c.passed else "FAIL", c.name, c.detail))
        if result.blocked:
            print("\nBlocked by point priority:")
            for b in result.blocked:
                print("  %s" % b)
        print("\n%s" % ("PASSED" if result.passed else "FAILED"))
        return 0 if result.passed else 1

    return 2


# --------------------------------------------------------------------------
# serve
# --------------------------------------------------------------------------


def cmd_serve(args):
    from .web import serve

    return serve(
        host=args.host,
        port=args.port,
        workspace=args.workspace,
        open_browser=not args.no_browser,
        quiet=args.quiet,
    )


# --------------------------------------------------------------------------
# explain
# --------------------------------------------------------------------------


def cmd_explain(args):
    topic = args.topic.upper()

    # A panel error code, so that a code read off a panel or a Desigo output
    # pane can be looked up here. Checked before commands because no command
    # shares these spellings.
    found = spec.panel_error(topic)
    if found is not None:
        kind, text, why = found
        print("%s -- %s" % (topic, text))
        if kind == "compiler":
            print("  reported by: the PPCL compiler, when the line is entered "
                  "or downloaded")
            print("  meaning:     the line was REFUSED and is not in the panel")
        else:
            print("  reported by: the field panel at runtime")
            print("  meaning:     the line compiled and loaded; the panel "
                  "failed while carrying it out")
            hexcode = spec.PANEL_RUNTIME_ERRORS[topic][0]
            print("  hex:         %s" % hexcode)
        if why:
            print()
            for line in textwrap.wrap(why, 74):
                print("  " + line)
        print("\n  manual: APOGEE BACnet ALN Field Panel User's Manual "
              "(125-3020), Appendix C")
        return 0

    if topic in spec.ALL:
        cmd = spec.ALL[topic]
        print("%s -- %s\n" % (cmd.name, cmd.summary))
        params = list(cmd.fixed)
        if cmd.priority_arg:
            print("  optional first argument: @prior (an @-priority indicator)")
        for p in params:
            print("  %-10s %-9s %s" % (p.name, p.kind.value, p.doc))
            if p.choices:
                print("  %-10s %-9s valid values: %s"
                      % ("", "", ", ".join(p.choices)))
        if cmd.repeat:
            names = ", ".join("%s (%s)" % (p.name, p.kind.value) for p in cmd.repeat)
            print("  repeating: %s -- %d to %d time(s)"
                  % (names, cmd.min_repeat, cmd.max_repeat))
        for p in cmd.trailing:
            print("  %-10s %-9s %s  (after the repeating group)"
                  % (p.name, p.kind.value, p.doc))
        print("\n  arguments: %d to %d" % (cmd.min_args(), cmd.max_args()))
        if cmd.point_types:
            print("  point types: %s" % ", ".join(sorted(cmd.point_types)))
        if cmd.time_based:
            print("  time-based: must be evaluated on every program pass")
        if not cmd.subroutine_safe:
            print("  NOT allowed inside a GOSUB subroutine")
        if not cmd.if_target_safe:
            print("  NOT allowed as the THEN/ELSE action of an IF")
        if cmd.notes:
            print("\n  Notes:")
            for n in cmd.notes:
                print("    - %s" % n)
        if cmd.see_also:
            print("\n  See also: %s" % ", ".join(cmd.see_also))
        return 0

    if topic in spec.POINT_TYPES:
        pt = spec.POINT_TYPES[topic]
        print("%s -- %s (%s)" % (pt.name, pt.description, pt.kind))
        print("  addresses: %s" % ", ".join(pt.addresses))
        return 0

    if topic in spec.PRIORITY_RANK or "@" + topic in spec.PRIORITY_RANK:
        key = topic if topic.startswith("@") else "@" + topic
        print("%s -- %s" % (key, spec.PRIORITY_DESCRIPTIONS[key]))
        print("\n  Priority order, lowest to highest:")
        for i, name in enumerate(spec.PRIORITY_ORDER):
            mark = "  <--" if name == key else ""
            print("    %d. %-8s %s%s" % (i, name, spec.PRIORITY_DESCRIPTIONS[name], mark))
        print(
            "\n  A point is only commanded when the operation's priority is at "
            "least\n  as high as the point's current priority."
        )
        return 0

    if topic in spec.FUNCTIONS:
        print("%s(value) -- %s" % (topic, spec.FUNCTIONS[topic]))
        return 0

    for r in linter.rule_catalog():
        if r.code == topic:
            print("%s -- %s" % (r.code, r.summary))
            print("  default severity: %s" % r.default_severity.value)
            print("  implemented by: %s" % r.name)
            if r.func.__doc__:
                print()
                for line in r.func.__doc__.strip().splitlines():
                    print("  %s" % line.strip())
            return 0

    print("nothing known about %r." % args.topic, file=sys.stderr)
    print(
        "Try a command name (ON, LOOP, TABLE), a point type (LOOAP), an "
        "@priority (EMER), or a rule code (E205).",
        file=sys.stderr,
    )
    return 2


# --------------------------------------------------------------------------
# rules / points / graph
# --------------------------------------------------------------------------


def cmd_rules(args):
    catalog = linter.rule_catalog()
    if args.format == "json":
        print(json.dumps(
            [{"code": r.code, "summary": r.summary,
              "severity": r.default_severity.value} for r in catalog],
            indent=2,
        ))
        return 0
    current = None
    for r in catalog:
        group = r.code[0]
        if group != current:
            current = group
            label = {"E": "Errors", "W": "Warnings", "S": "Style",
                     "P": "Performance and optimisation"}.get(group, group)
            print("\n%s" % label)
            print("-" * len(label))
        print("  %-6s %-8s %s" % (r.code, r.default_severity.value, r.summary))
    print("\n%d rules" % len(catalog))
    return 0


def cmd_points(args):
    rows = {}
    for path in collect_files(args.paths):
        prog = ppcl_parser.parse_file(path)
        a = analyzer.analyze(prog)
        for use in a.uses:
            key = use.name.upper()
            row = rows.setdefault(
                key,
                {"name": use.name, "reads": 0, "writes": 0, "files": set(),
                 "commands": set(), "priorities": set()},
            )
            if use.kind == "read":
                row["reads"] += 1
            elif use.kind == "write":
                row["writes"] += 1
            row["files"].add(os.path.basename(path))
            row["commands"].add(use.context)
            if use.priority:
                row["priorities"].add(use.priority)

    if args.format == "json":
        print(json.dumps(
            {k: {**v, "files": sorted(v["files"]),
                 "commands": sorted(v["commands"]),
                 "priorities": sorted(v["priorities"])}
             for k, v in sorted(rows.items())},
            indent=2,
        ))
        return 0

    print("%-34s %6s %6s  %s" % ("POINT", "READS", "WRITES", "USED BY"))
    print("-" * 78)
    for key in sorted(rows):
        r = rows[key]
        prio = (" [%s]" % ",".join(sorted(r["priorities"]))) if r["priorities"] else ""
        print("%-34s %6d %6d  %s%s"
              % (r["name"][:34], r["reads"], r["writes"],
                 ",".join(sorted(r["files"])), prio))
    print("\n%d distinct point references" % len(rows))
    return 0


def cmd_graph(args):
    prog = ppcl_parser.parse_file(args.path)
    a = analyzer.analyze(prog)

    if args.format == "dot":
        print("digraph ppcl {")
        print('  rankdir=TB; node [shape=box, fontname="monospace"];')
        for n in a.numbers:
            ln = a.index[n]
            if ln.is_comment:
                continue
            attrs = []
            if n in a.steady_state:
                attrs.append('style=filled, fillcolor="#dfe9f5"')
            elif n not in a.reachable:
                attrs.append('style=filled, fillcolor="#f5dfdf"')
            label = ln.body.replace('"', "'")[:44]
            print('  n%d [label="%d  %s"%s];'
                  % (n, n, label, (", " + ", ".join(attrs)) if attrs else ""))
        for src, dst, kind in a.edges:
            target = a.resolve_target(dst)
            if target is None:
                continue
            style = {"goto": "solid", "gosub": "dashed", "ref": "dotted"}[kind]
            print('  n%d -> n%d [style=%s, label="%s"];' % (src, target, style, kind))
        print("}")
        return 0

    print("Program: %s" % (prog.name or args.path))
    print("  %d lines, %d executable" % (len(prog.lines), len(prog.executable_lines())))
    print("  reachable: %d   main loop: %d   run-once: %d"
          % (len(a.reachable), len(a.steady_state), len(a.one_shot)))
    if a.loop_entry is not None:
        print("  main loop entry: line %d" % a.loop_entry)
    print()

    if a.subroutines:
        print("Subroutines")
        print("-----------")
        for entry, sub in sorted(a.subroutines.items()):
            callers = ", ".join(str(c) for c in sorted(set(sub.callers)))
            state = "RETURN at %s" % ", ".join(str(r) for r in sub.returns) \
                if sub.returns else "NO RETURN"
            print("  line %-6d %-24s called from %s (%d lines)"
                  % (entry, state, callers, len(sub.lines)))
        print()

    print("Branches")
    print("--------")
    for src, dst, kind in sorted(a.edges):
        target = a.resolve_target(dst)
        note = ""
        if target is None:
            note = "  [TARGET MISSING]"
        elif target != dst:
            note = "  [redirected to %d]" % target
        elif target <= src and kind == "goto":
            note = "  [backward]"
        print("  %-6d --%-6s--> %-6d%s" % (src, kind, dst, note))
    return 0


# --------------------------------------------------------------------------
# redact
# --------------------------------------------------------------------------


def cmd_redact(args):
    from .redact import redact_files

    files = collect_files(args.paths)
    result = redact_files(files, keep_generic=not args.aggressive)

    if args.outdir:
        os.makedirs(args.outdir, exist_ok=True)
        for path, text in result.files.items():
            dest = os.path.join(args.outdir, os.path.basename(path))
            with open(dest, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            print("wrote %s" % dest, file=sys.stderr)
    else:
        for path, text in result.files.items():
            if len(result.files) > 1:
                print("===== %s =====" % path)
            sys.stdout.write(text)

    if args.mapping:
        result.save_mapping(args.mapping)
        print("mapping written to %s -- keep this local" % args.mapping,
              file=sys.stderr)
    print("%d name(s) redacted across %d file(s)"
          % (len(result.mapping), len(result.files)), file=sys.stderr)
    if result.preserved:
        print(
            "\nThese generic terms were left in place: %s"
            % ", ".join(sorted(result.preserved)),
            file=sys.stderr,
        )
        print(
            "Check that none of them is actually a site identifier. A site "
            "abbreviation can collide with\nthe HVAC vocabulary -- OCC is "
            "'occupied' here, but it is also a building name. Re-run\nwith "
            "--aggressive to replace these too.",
            file=sys.stderr,
        )
    return 0


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# blocks
# --------------------------------------------------------------------------


def cmd_blocks(args):
    """Compile a block diagram to PPCL, then lint the result."""
    from . import blocks

    with open(args.path, "r", encoding="utf-8") as fh:
        try:
            diagram = blocks.loads(fh.read())
        except (ValueError, KeyError, TypeError) as exc:
            print("%s is not a block diagram: %s" % (args.path, exc),
                  file=sys.stderr)
            return 2

    try:
        text, warnings, diags = blocks.compile_and_lint(
            diagram, firmware=spec.Firmware(args.firmware)
        )
    except blocks.BlockError as exc:
        where = (" (block %s)" % exc.block_id) if exc.block_id else ""
        print("cannot compile%s: %s" % (where, exc.message), file=sys.stderr)
        return 1

    for note in warnings:
        print("warning: %s" % note, file=sys.stderr)
    errors = [d for d in diags if d.severity is Severity.ERROR]
    for d in diags:
        if d.severity in (Severity.ERROR, Severity.WARNING):
            print("%s %s line %s: %s"
                  % (d.severity.value, d.code, d.line, d.message),
                  file=sys.stderr)
    _write_or_print(args.path, text, args)
    return 1 if errors else 0


def cmd_transform(args):
    """Apply one source transform, reporting exactly what changed."""
    from . import transforms

    with open(args.path, "r", encoding="utf-8") as fh:
        text = fh.read()

    lines = [int(n) for n in (args.lines or "").replace(",", " ").split()]
    renames = {}
    for pair in args.rename or []:
        if "=" not in pair:
            print("--rename takes OLD=NEW, got %r" % pair, file=sys.stderr)
            return 2
        old, _, new = pair.partition("=")
        renames[old.strip()] = new.strip()

    ops = {
        "expand-defines": lambda: transforms.expand_defines(text),
        "collapse-defines": lambda: transforms.collapse_defines(text),
        "dots": lambda: transforms.swap_separators(text, "."),
        "underscores": lambda: transforms.swap_separators(text, "_"),
        "disable": lambda: transforms.set_disabled(text, lines, True),
        "enable": lambda: transforms.set_disabled(text, lines, False),
        "comment": lambda: transforms.toggle_comments(text, lines),
        "clone": lambda: transforms.clone_lines(
            text, args.first, args.last, renames,
            start=args.start, step=args.step,
        ),
    }
    result = ops[args.op]()
    for note in result.notes:
        print(note, file=sys.stderr)
    for note in result.warnings:
        print("warning: %s" % note, file=sys.stderr)
    _write_or_print(args.path, result.text, args)
    return 0


def cmd_db(args):
    """Import a point export and check a program's references against it."""
    from . import points as pointsdb

    try:
        db = pointsdb.load_file(args.database)
    except (ValueError, OSError) as exc:
        print("cannot read %s: %s" % (args.database, exc), file=sys.stderr)
        return 2

    print("%d point(s) from %s" % (len(db), db.source))
    if db.ignored_columns:
        print("columns not recognised: %s" % ", ".join(db.ignored_columns))
    for problem in db.problems:
        print("  %s" % problem)

    if not args.check:
        for record in list(db.points.values())[: args.limit]:
            print("  %-28s %-8s %s"
                  % (record.name, record.ptype or "-", record.description))
        if len(db) > args.limit:
            print("  ... %d more" % (len(db) - args.limit))
        return 0

    status = 0
    for path in collect_files(args.check):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            program = ppcl_parser.parse(fh.read(), name=os.path.basename(path))
        missing = db.unresolved(program)
        print()
        print("%s: %d unresolved reference(s)" % (path, len(missing)))
        for row in missing:
            print("  U  %-28s lines %s"
                  % (row["name"],
                     ", ".join(str(n) for n in row["lines"][:8])))
        if missing:
            status = 1
    return status


def cmd_help(args):
    """Print the built-in documentation to the terminal."""
    from . import helpdocs

    if args.search:
        results = helpdocs.search(args.search)
        if not results:
            print("nothing found for %r" % args.search)
            return 1
        for hit in results:
            print("%-12s %s" % (hit["id"], hit["title"]))
            print("             %s" % hit["excerpt"])
        return 0

    if not args.topic:
        for group in helpdocs.contents():
            print(group["category"])
            for page in group["pages"]:
                print("  %-12s %s" % (page["id"], page["summary"]))
            print()
        return 0

    page = helpdocs.page(args.topic)
    if page is None:
        print("no help topic called %r; run 'ppcl help' for the list"
              % args.topic, file=sys.stderr)
        return 2
    print(page["body"])
    if page["see_also"]:
        print()
        print("See also: %s"
              % ", ".join(ref["id"] for ref in page["see_also"]))
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="ppcl",
        description="Read, inspect, lint, debug, optimise and write Siemens "
                    "APOGEE PPCL.",
    )
    p.add_argument("--version", action="version", version="ppcl %s" % __version__)
    sub = p.add_subparsers(dest="command", required=True)

    def add_common_output(sp):
        sp.add_argument("-i", "--in-place", action="store_true",
                        help="rewrite the file instead of printing")
        sp.add_argument("--backup", action="store_true",
                        help="with --in-place, keep a .bak copy")
        sp.add_argument("-o", "--output", help="write to this file")
        sp.add_argument("--pad", type=int, default=5,
                        help="zero-pad line numbers to this width (default 5)")

    # lint
    sp = sub.add_parser("lint", help="check programs against the PPCL rules")
    sp.add_argument("paths", nargs="+")
    sp.add_argument("--firmware", default="apogee",
                    choices=[f.value for f in spec.Firmware])
    sp.add_argument("--points", help="point database (JSON or name,type CSV)")
    sp.add_argument("--min-severity", default="style", choices=SEVERITY_ORDER)
    sp.add_argument("--disable", action="append", metavar="CODE",
                    help="suppress a rule code; repeatable")
    sp.add_argument("--report", metavar="FILE",
                    help="a PPCL DISPLAY REPORT from the panel. Supplies the "
                         "enable/disable state, unresolved points and trace "
                         "bits that exported program text does not carry")
    sp.add_argument("--format", default="text", choices=["text", "json"])
    sp.add_argument("--strict", action="store_true",
                    help="exit non-zero on warnings as well as errors")
    sp.add_argument("--color", action="store_true")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.set_defaults(func=cmd_lint)

    # fmt
    sp = sub.add_parser("fmt", help="sort by line number and normalise layout")
    sp.add_argument("paths", nargs="+")
    sp.add_argument("--no-sort", action="store_true")
    sp.add_argument("--normalize", action="store_true",
                    help="re-render statements from the parse tree")
    add_common_output(sp)
    sp.set_defaults(func=cmd_fmt)

    # renumber
    sp = sub.add_parser(
        "renumber",
        help="renumber lines and rewrite every GOTO/GOSUB/ACT reference",
    )
    sp.add_argument("paths", nargs="+")
    sp.add_argument("--start", type=int, default=10)
    sp.add_argument("--step", type=int, default=10)
    sp.add_argument("--preserve-blocks", action="store_true",
                    help="keep the thousands-block layout of subroutines")
    sp.add_argument("--block-size", type=int, default=1000)
    sp.add_argument("--split-duplicates", action="store_true",
                    help="give each duplicated line its own number, keeping "
                         "every line (changes what the panel runs)")
    sp.add_argument("--allow-duplicates", action="store_true",
                    help="keep only the first of each duplicated line, "
                         "matching what the panel runs today")
    add_common_output(sp)
    sp.set_defaults(func=cmd_renumber)

    # run
    sp = sub.add_parser("run", help="simulate a program against point values")
    sp.add_argument("path")
    sp.add_argument("--scenario", help="JSON file of starting point values")
    sp.add_argument("--set", action="append", metavar="POINT=VALUE",
                    help="set a point value; repeatable")
    sp.add_argument("--time", type=float, help="start time in decimal hours")
    sp.add_argument("--passes", type=int, default=10)
    sp.add_argument("--interval", type=float, default=1.0,
                    help="simulated seconds per pass (default 1)")
    sp.add_argument("--trace", nargs="?", const="all", choices=["all", "writes"],
                    help="print an execution trace")
    sp.add_argument("--format", default="text", choices=["text", "json"])
    sp.set_defaults(func=cmd_run)

    # bench
    sp = sub.add_parser(
        "bench",
        help="run a program against simulated equipment, with faults and checks",
    )
    sp.add_argument("path", nargs="?", default=None)
    sp.add_argument("--scenario", help="JSON scenario: plant, bindings, faults, checks")
    sp.add_argument("--preset", default="single_zone_ahu",
                    help="plant preset when no scenario is given")
    sp.add_argument("--weather", default="design_winter",
                    help="weather preset: design_winter, design_summer, "
                         "shoulder, mild, cold_day")
    sp.add_argument("--seconds", type=float, default=10800.0,
                    help="simulated seconds to run (default 3 hours)")
    sp.add_argument("--dt", type=float, default=5.0,
                    help="simulated seconds per step (default 5)")
    sp.add_argument("--sample", type=float, default=60.0,
                    help="seconds between recorded samples")
    sp.add_argument("--set", action="append", metavar="POINT=VALUE",
                    help="seed a point value; repeatable")
    sp.add_argument("--fault", action="append",
                    metavar="SYSTEM,KIND,AT_SECONDS[,VALUE]",
                    help="inject a fault; repeatable")
    sp.add_argument("--chart", action="append", metavar="VAR",
                    help="variable to trend; repeatable")
    sp.add_argument("--list-faults", action="store_true",
                    help="list fault kinds and presets, then exit")
    sp.add_argument("--force", action="store_true",
                    help="bench even if the program has lint errors")
    sp.add_argument("--format", default="text", choices=["text", "json", "csv"])
    sp.set_defaults(func=cmd_bench)

    # new
    sp = sub.add_parser("new", help="generate a program from a template")
    sp.add_argument("template", choices=sorted(generator.TEMPLATES))
    sp.add_argument("--title")
    sp.add_argument("--author")
    sp.add_argument("--organization")
    sp.add_argument("--points-list", metavar="A,B,C",
                    help="points for the schedule and leadlag templates")
    sp.add_argument("--option", action="append", metavar="KEY=VALUE",
                    help="template parameter; repeatable")
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_new)

    # seq
    sp = sub.add_parser(
        "seq",
        help="author PPCL from a sequence document instead of writing it by hand",
    )
    sp.add_argument(
        "action",
        choices=["new", "check", "compile", "bench", "fmt", "json", "text"],
        help="new: starter document; check: validate and lint the result; "
             "compile: emit PPCL; bench: compile and run against equipment; "
             "fmt: canonicalise the text form; json/text: convert between forms",
    )
    sp.add_argument("path", nargs="?", help="the .seq or .json document")
    sp.add_argument("-o", "--output")
    sp.add_argument("-i", "--in-place", action="store_true")
    sp.add_argument("--author")
    sp.add_argument("--firmware", default="apogee",
                    choices=[f.value for f in spec.Firmware])
    sp.add_argument("--preset", default="single_zone_ahu")
    sp.add_argument("--weather", default="design_winter")
    sp.add_argument("--seconds", type=float, default=10800.0)
    sp.add_argument("--fault", action="append",
                    metavar="SYSTEM,KIND,AT_SECONDS[,VALUE]")
    sp.set_defaults(func=cmd_seq)

    # serve
    sp = sub.add_parser(
        "serve",
        help="open the workbench UI in a browser (editor, builder, bench)",
    )
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--host", default="127.0.0.1",
                    help="bind address; anything but localhost is unauthenticated")
    sp.add_argument("-w", "--workspace", default=".",
                    help="directory the UI may open and save files in")
    sp.add_argument("--no-browser", action="store_true")
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(func=cmd_serve)

    # explain
    sp = sub.add_parser(
        "explain",
        help="explain a command, point type, @priority, function, or rule code",
    )
    sp.add_argument("topic")
    sp.set_defaults(func=cmd_explain)

    # rules
    sp = sub.add_parser("rules", help="list every lint rule")
    sp.add_argument("--format", default="text", choices=["text", "json"])
    sp.set_defaults(func=cmd_rules)

    # points
    sp = sub.add_parser("points", help="inventory every point a program touches")
    sp.add_argument("paths", nargs="+")
    sp.add_argument("--format", default="text", choices=["text", "json"])
    sp.set_defaults(func=cmd_points)

    # graph
    sp = sub.add_parser("graph", help="show control flow, subroutines, branches")
    sp.add_argument("path")
    sp.add_argument("--format", default="text", choices=["text", "dot"])
    sp.set_defaults(func=cmd_graph)

    # redact
    sp = sub.add_parser(
        "redact",
        help="anonymise site-identifying names so a program can be shared",
    )
    sp.add_argument("paths", nargs="+")
    sp.add_argument("--outdir", help="write redacted copies here")
    sp.add_argument("--mapping", help="write the reversal mapping to this file")
    sp.add_argument("--aggressive", action="store_true",
                    help="also replace generic HVAC vocabulary")
    sp.set_defaults(func=cmd_redact)

    # blocks
    sp = sub.add_parser(
        "blocks", help="compile a block diagram (.blocks.json) to PPCL"
    )
    sp.add_argument("path")
    sp.add_argument("--firmware", default="apogee",
                    choices=[f.value for f in spec.Firmware])
    sp.add_argument("-o", "--output", help="write here instead of stdout")
    sp.add_argument("--in-place", action="store_true",
                    help="write next to the diagram")
    sp.set_defaults(func=cmd_blocks)

    # transform
    sp = sub.add_parser(
        "transform",
        help="apply a source transform: DEFINE expansion, separator swap, "
             "clone, enable/disable, comment",
    )
    sp.add_argument("op", choices=["expand-defines", "collapse-defines",
                                   "dots", "underscores", "disable",
                                   "enable", "comment", "clone"])
    sp.add_argument("path")
    sp.add_argument("--lines", help="line numbers, comma or space separated")
    sp.add_argument("--first", type=int, default=0, help="clone: first line")
    sp.add_argument("--last", type=int, default=0, help="clone: last line")
    sp.add_argument("--start", type=int, help="clone: line number for the copy")
    sp.add_argument("--step", type=int, help="clone: increment for the copy")
    sp.add_argument("--rename", action="append",
                    help="clone: OLD=NEW, repeatable")
    sp.add_argument("-o", "--output")
    sp.add_argument("--in-place", action="store_true")
    sp.set_defaults(func=cmd_transform)

    # db
    sp = sub.add_parser(
        "db", help="import a point export and check references against it"
    )
    sp.add_argument("database", help="CSV or JSON export")
    sp.add_argument("--check", nargs="*",
                    help="programs to check for unresolved points")
    sp.add_argument("--limit", type=int, default=25)
    sp.set_defaults(func=cmd_db)

    # help
    sp = sub.add_parser("help", help="read the built-in documentation")
    sp.add_argument("topic", nargs="?")
    sp.add_argument("--search", help="search titles and bodies")
    sp.set_defaults(func=cmd_help)

    return p


def _make_output_encodable():
    """Stop a Windows console killing a command over one character.

    The default console encoding on Windows is cp1252, which cannot represent
    a degree sign, a multiplication sign, or the minus sign U+2212 -- all of
    which appear in the reference text. Without this, `ppcl help ssto` dies
    with a UnicodeEncodeError partway through printing, which looks like a
    crash in the tool rather than a limitation of the terminal.

    UTF-8 is tried first; if the stream will not take it, unencodable
    characters are replaced rather than raising.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


def main(argv=None):
    _make_output_encodable()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print("%s: %s" % (exc.strerror, exc.filename), file=sys.stderr)
        return 2
    except BrokenPipeError:  # piping into head, etc.
        return 0


if __name__ == "__main__":
    sys.exit(main())
