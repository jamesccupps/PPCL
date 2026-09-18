---
name: ppcl
description: Read, review, debug, optimise and write Siemens PPCL for field panels, as edited through Desigo CC or Insight. Use whenever PPCL program text appears, when the user mentions PPCL, APOGEE, PXC, Insight or Desigo CC sequence programming, or asks to review, fix, renumber, simulate or author a control sequence. Also use for questions about PPCL commands, point priority, point types, or why a sequence misbehaves on a panel.
---

# PPCL

Siemens PPCL. Current, not legacy: Siemens' 2025 manual for its current panels
points at the same PPCL manual as the old ones. This skill pairs a
deterministic toolkit (`python -m ppcl.cli`) with the judgement calls the
toolkit cannot make.

**Division of labour: run the tool for anything mechanical.** Do not
hand-inspect a program for duplicate line numbers, unreachable code, argument
counts or reference integrity — `lint` does that exhaustively and cites the
manual. Spend your own effort on what the sequence is *supposed* to do.

## If the MCP tools are available, use them

When this is installed as a plugin you have `ppcl_*` tools and should reach for
them first — they are the same code as the CLI with no shell round trip:

| Tool | For |
|---|---|
| `ppcl_lint` | every defect check, with manual citations. **Run before calling any program correct** |
| `ppcl_explain` | a command, priority, point type, function or rule code |
| `ppcl_help` | priority, execution model, SSTO, decision tables, provenance |
| `ppcl_analyze` | main loop, run-once lines, subroutines, points and their priorities |
| `ppcl_simulate` | "did this statement fire, and did the write take" |
| `ppcl_bench` | "does this actually control", with faults |
| `ppcl_compile_sequence` | author from a decision table |
| `ppcl_format`, `ppcl_renumber`, `ppcl_transform` | exact source edits |
| `ppcl_commands` | find the command when you know the job but not the name |

**Never answer a question about a command signature from memory when
`ppcl_explain` is one call away.** The argument limits, the firmware
restrictions and the manual's cautions are exactly the things that are easy to
misremember.

Deliberately not exposed: anything that writes the engineer's files. The tools
analyse and generate text; saving is the person's decision.

Without the plugin, everything below is the same functionality through
`python -m ppcl.cli`.

## Firmware changes the answer

`pxc_a` is the current generation (PXC4/5/7.A) and is **not** just a newer
APOGEE:

- line limit **512** characters, against 66 on APOGEE
- **Ten statements were removed from the language** and the runtime treats
  them as invalid, with no replacements: `ADAPTM`, `ADAPTS`, `DISCOV`, `ENCOV`,
  `DPHONE`, `EPHONE`, `ALARM`, `NORMAL`, `OIP`, `ONPWRT`
- **No warmstart.** That is why `ONPWRT` is gone: a PXC.A program *always*
  resumes at its first line after a power failure, so there is nothing for it
  to do
- `GETVAL` and `SETVAL` exist for BACnet property access, and `LSQ2`/`LSQDAT`
  do not appear in its manual
- a backward `GOTO` marks the **end of the program cycle** and restarts the
  cycle-time calculation, so an inner one truncates every pass that takes it

If it matters to the answer and you do not know the panel generation, ask.

## Workflow

For anything hands-on, the UI is usually the fastest route -- tell the user to
run it rather than narrating CLI invocations:

```bash
python -m ppcl.cli serve --workspace .
```

Editor with live linting, the decision-table builder, and the bench with trend
charts, all in one place. It is localhost-only and confines file access to the
workspace.

Reviewing or debugging an existing program:

```bash
python -m ppcl.cli lint <path> --min-severity warning
python -m ppcl.cli graph <file>          # loop entry, subroutines, branches
python -m ppcl.cli points <path>         # what it touches, and at what priority
```

Then, when behaviour is in question, reproduce it:

```bash
python -m ppcl.cli run <file> --set MAT=35 --set OAT=20 --time 8.0 \
    --passes 20 --interval 30 --trace writes
```

The simulator shows priority-blocked commands explicitly. That is usually the
answer when someone says "the schedule stopped working".

Proving a sequence before it touches a panel -- prefer this over `run` whenever
the question is "does this control?" rather than "does this line fire?":

```bash
python -m ppcl.cli bench <file> --weather design_winter --seconds 10800
python -m ppcl.cli bench <file> --fault "AHU1,oa_damper_stuck,1800,100"
python -m ppcl.cli bench --list-faults
```

The bench runs the program against simulated equipment with sensor lag,
actuator stroke time and damper leakage, and reports pass/fail against checks
(freeze stat never trips, zone held in band, fan does not short cycle). Test
the failure modes, not just the happy path -- Siemens' own guidance says to
test boundary conditions and failure scenarios, and `--fault` is how.

Writing new PPCL. **Prefer a sequence document over hand-written PPCL** when
the user is describing behaviour rather than editing existing code:

```bash
python -m ppcl.cli seq new -o AHU1.seq
python -m ppcl.cli seq check AHU1.seq
python -m ppcl.cli seq compile AHU1.seq -o AHU1.ppcl
python -m ppcl.cli seq bench AHU1.seq --weather design_winter
```

The document is a decision table of equipment against modes, plus interlocks,
resets and loops. The compiler guarantees one backward GOTO, a matching RELEAS
for every interlock, and no time-based command inside a conditional -- so the
defects the linter hunts for cannot be written in the first place. Modes are
listed lowest priority first and the last match wins.

Compilation is one way. If the user has existing hand-written PPCL, edit it
directly; do not attempt to reconstruct a document from it.

For a one-off program from a fixed template:

```bash
python -m ppcl.cli new ahu --author "..." -o AHU1.ppcl
python -m ppcl.cli new skeleton --title "..."
```

Templates: `skeleton`, `ahu`, `reset`, `schedule`, `leadlag`. For anything
bespoke use `ppcl.generator.Builder`, which takes symbolic labels instead of
raw line numbers so branches cannot go stale. **Always lint what you generate
before presenting it** — `new` does this automatically.

Renumbering: `python -m ppcl.cli renumber <file> --preserve-blocks`. It rewrites
every `GOTO`/`GOSUB`/`ACT`/`DEACT`/`ENABLE`/`DISABL`/`ONPWRT` reference,
including ones nested inside `IF` or behind `SAMPLE`. It **refuses** on
duplicate line numbers; surface that choice to the user rather than picking
`--split-duplicates` or `--allow-duplicates` for them, because the two produce
different running programs.

Reference lookup: `python -m ppcl.cli explain <COMMAND|POINTTYPE|@PRIORITY|RULE>`.
Prefer this over recalling a signature from memory — it prints the manual's own
argument limits and notes.

## What to reason about yourself

The linter checks conformance to the manual. It cannot tell you whether the
sequence is *right*. Focus your attention on:

- **Does the logic match the sequence of operations?** Ask for the SOO if you
  do not have it. Setpoints, interlocks and staging order are engineering
  decisions, not language questions.
- **Life safety and equipment protection.** Freeze protection, high static
  cutout, smoke control, low-limit interlocks. These must fail safe and must
  release cleanly. Never soften one to make a lint finding go away.
- **What happens on a power return.** Execution resumes at the program's first
  line. Latches, `WAIT` triggers and `SAMPLE` intervals all reset.
- **Interaction with other programs in the same panel** and with equipment
  scheduling in Desigo CC / Insight, which can command the same points.

## The failure that matters most

A point commanded above `@NONE` and never released. It looks correct and it is
silently, permanently broken:

```
00120	IF(TIME.GE.6:00.AND.TIME.LT.18:00) THEN ON(SFAN) ELSE OFF(SFAN)
00160	IF(MAT.LT.38.0) THEN OFF(@EMER,SFAN)
```

After one freeze event `SFAN` sits at `@EMER` forever. Line 120 keeps issuing
`ON(SFAN)` at `@NONE` and the panel discards it every pass, with no error
anywhere. The fix is a matched release at the same or higher priority:

```
00150	IF(MAT.GT.45.0) THEN RELEAS(@EMER,SFAN)
```

Priority, lowest to highest: `@NONE` < `@PDL` < `@EMER` < `@SMOKE` < `@OPER`.
A command takes effect only when its priority is at least the point's current
priority — and a `RELEAS` below the point's priority does nothing at all.

## Execution model, in brief

Lines run in ascending line-number order; the last line wraps to the first. Time-based
commands (`LOOP`, `SAMPLE`, `TOD`, `TODSET`, `TODMOD`, `WAIT`, `TIMAVG`,
`SSTO`, `PDL*`) must be evaluated on every pass — one placed outside the main
loop runs once at load and then never again. They must not appear inside a
`GOSUB` body, and `GOSUB` must not appear inside an `IF`.

Full detail, including the failure catalogue and where the manual contradicts
itself, is in `docs/PPCL-REFERENCE.md`. Read it before answering a question
about command semantics from memory.

## Honesty requirements

- **`LOOP` output values from the simulator are approximate.** Siemens does not
  publish the internal PID form. Say so whenever loop output is discussed; never
  present a simulated `cv` as tuning guidance.
- **The equipment models are first-order approximations**, good for exercising
  control logic. They are not a load calculation and not an energy model, and
  there is no dehumidification. A passing bench run means the logic behaves; it
  does not size equipment or predict energy.
- `SSTO`, `PDL*`, `OIP` and `DC`/`DCR` are **not simulated** — they are traced
  as no-ops. A clean simulation of a program using them proves nothing about
  those commands.
- Nothing in this toolkit has been validated against a live panel. Cite the
  manual, not the tool, when making a claim about the language.
- If a rule's finding looks like a false positive, check it against the manual
  citation the finding carries before dismissing it — and say which one it was.

## Site data

Real PPCL carries building, panel, floor and tenant names. Before any program
text goes into anything shared — a ticket, a vendor email, a public repo, an
artifact:

```bash
python -m ppcl.cli redact <path> --outdir <dir> --mapping local-map.json
```

Comment bodies are dropped entirely, because that is where tenant names and
room numbers actually live. The mapping file reverses it and must stay local.

Never write to a live panel from here. This toolkit is read-only with respect
to building systems by design: it analyses and generates files, and the
engineer loads them.
