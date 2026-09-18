# PPCL Workbench — session state and roadmap

**Read this first in a fresh session.** It carries the context that would
otherwise be lost: what exists, what is verified versus assumed, which
decisions were deliberate, and what to build next.

Last updated: 2026-09-18.

---

## 0. This repository is public — read before committing anything

Published at **<https://github.com/jamesccupps/PPCL>**, MIT licensed.

That changes the rules for everything after it. Git history is permanent in
practice: a site name committed once and removed later still sits in the
history, and taking it out means a rewrite and a force-push. So the check
happens *before* the commit, every time.

**Four things never enter this repository:**

1. **Site-identifying strings.** No building name, no real point, panel, BLN or
   device name, no IP address, no floor or tenant name. Findings from real
   programs are described in general terms and any example is reproduced with
   the names changed — see `docs/RESEARCH-LOG.md`, which is written that way
   throughout. `samples/` is third-party and clean.
2. **The MEC100K listing** from A6V10374898 Appendix C — copyrighted Siemens
   code, used locally for interoperability testing only.
3. **Anything out of a local Siemens or site library.** This machine holds
   Siemens' shipped 84-program PPCL application library, an Insight database
   backup, 30 books of Insight compiled help and the reference site's own
   programs. All of it is licensed or site-confidential. It is research input;
   the extracted text lives in the scratchpad and nothing from it is copied
   here.
4. **Real program text**, redacted or otherwise, unless its owner has agreed.
   `ppcl redact` exists for sharing a program with a vendor, not for turning
   site code into public samples.

Before every commit, grep the tree. **Grep for more than names** -- the first
audit checked for the building and its panels and passed, while a test carried
a line copied verbatim out of the site's PPCL editor, complete with its real
point name and BACnet device instance. A round-looking number like `22222` is
not evidence that it is invented.

What to look for, at minimum:

- the site and building name, panel and node names, floor and tenant names
- **real point names**, including DEFINE prefixes like `%X%`
- **BACnet device instances** (`BAC_<n>_...`) and object identifiers
- IP addresses and device addresses
- any comment block lifted out of a real program

If an example needs to look real, invent one. Siemens' own manuals use
`BAC_12345_AO_67`, which is a fine model.

---

## 1. What this is

Tooling for Siemens PPCL (Powers/Proprietary Process Control Language) on
APOGEE and PXC field panels.

Development is driven by a **reference site**: a Class A office building of
roughly a quarter of a million square feet, thirteen air handlers, a Desigo CC
supervisor, and **APOGEE BACnet ALN** field panels — SCU, MBC and PXC-BACnet,
edited through Desigo CC's own PPCL Editor. Determined from the programs, not
assumed: no `GETVAL`/`SETVAL`, no `[Node]Point` references, 25 uses of `OIP`
(which a PXC.A rejects outright), and 52 distinct `BAC_<device>_<type>_<instance>`
encoded names. `A6V10324350` / 125-3020 is the manual that applies.

The site is deliberately not named, and no program text, point name or panel
name from it appears anywhere in this repository. Findings from it are
described in general terms in `docs/RESEARCH-LOG.md`.

The goal is a PPCL-specific IDE, bench and simulator that a working tech could
pick up from GitHub and use — not a personal script.

Python 3.10+, **standard library only, zero dependencies** — deliberate, so it
runs on any engineering workstation with no install. Windows is the primary
platform.

```bash
python -m pytest tests -q          # 447 tests, ~150s
python -m ppcl.cli serve           # the app
python -m ppcl.cli lint samples    # exercises the CLI on real programs
python -m ppcl.cli help            # the built-in documentation
```

---

## 2. Current state

**447 tests passing.** ~18,600 lines Python, ~4,900 lines UI, ~3,800 lines
tests. 79 lint rules, 65 commands, 99 BACnet properties, 30 block types,
16 CLI subcommands, 11 MCP tools, 12 help pages, 20 settings, 6 firmware
families.

| Layer | Module | State |
|---|---|---|
| Language spec | `ppcl/spec.py` | Complete, manual-derived, cited. Now also carries Siemens' own Command Assist categories |
| Lexer / parser | `ppcl/lexer.py`, `parser.py` | Solid. Parses every real program seen so far with zero errors |
| Analysis | `ppcl/analyzer.py` | Control flow with wrap edge + Tarjan SCC; point read/write tracking |
| Linter | `ppcl/linter.py`, `rules/` | 79 rules, each citing its source |
| Formatter | `ppcl/formatter.py` | Format + renumber with exact reference rewriting |
| Simulator | `ppcl/simulator.py` | Interpreter with real priority arbitration, panel-faithful line budget |
| **Debugger** | `ppcl/debug.py` | Line / write / condition breakpoints, step, step over, step out, run to cursor, watch with priority, mid-run override, coverage |
| Generator | `ppcl/generator.py` | `Builder` (symbolic labels) + 5 fixed templates |
| Sequence | `ppcl/sequence/` | Document model, text DSL, compiler with hard guarantees |
| **Blocks** | `ppcl/blocks/` | 30 block types; expression-first compiler with feedback handling |
| **Transforms** | `ppcl/transforms.py` | DEFINE expand/collapse, separator swap, clone with rename, enable/disable, comment |
| **Points** | `ppcl/points.py` | CSV/JSON import, alias column mapping, unresolved-reference check |
| **Panel report** | `ppcl/report.py` | Reads a `PPCL DISPLAY REPORT`: disabled lines, unresolved points, trace bits. Folded into `lint --report` |
| **Settings** | `ppcl/settings.py` | 20 declared settings with types, ranges, help |
| **Help** | `ppcl/helpdocs.py` | 11 pages, served to the UI and the CLI |
| Plant | `ppcl/plant/` | Equipment models + test bench with faults and checks |
| Web | `ppcl/web/` | `api.py` + `api_ide.py`, 34 endpoints; UI is 9 ES modules, no framework |
| **MCP** | `ppcl/mcp_server.py` | 11 tools over the same dispatch. Analysis only -- no file-writing endpoint is exposed |
| **Plugin** | `skills/`, `commands/`, `.claude-plugin/`, `.mcp.json` | The repo installs as a Claude Code plugin. See `PLUGIN.md` |

### CLI

`lint fmt renumber run bench new seq serve explain rules points graph redact
blocks transform db help`

### Slash commands (plugin)

`/ppcl-review` `/ppcl-explain` `/ppcl-bench` `/ppcl-new`

### UI panes

Editor · Builder · Blocks · Bench (Simulate / Debug) · Settings · Help

---

## 3. Decisions already made — do not relitigate

- **Zero dependencies.** bacpypes3 will be the one exception, and only for the
  future BACnet adapter, kept optional.
- **Read-only with respect to building systems.** No panel writes, no network
  client, no BACnet writes. The tool reads and writes *files*; the engineer
  loads them via Insight/Desigo CC.
- **Sequence and block compilation are one way.** A document or a diagram
  compiles to PPCL reliably; arbitrary hand-written PPCL does **not** lift
  back, because real GOTO structure carries intent neither representation can
  hold. Do not add a guessing lifter.
- **Refuse rather than guess.** Renumbering refuses on duplicate line numbers.
  The block compiler refuses a combinational cycle and names the blocks.
- **Every diagnostic cites its source.**
- **The UI never shows generated code it has not linted.**
- **Web server binds localhost and sandboxes file access.** On Windows it now
  refuses to start if the port is taken, instead of silently sharing it.

### UI decisions

- Local web app rather than Tkinter — chosen for the code editor, the canvas
  and the charts, acknowledged as a departure from the Tkinter preference in
  the user's global CLAUDE.md.
- **The UI mirrors the Desigo CC PPCL Editor wherever that editor has an
  equivalent** — keyboard shortcuts, quick numbering, Command Assist's three
  parts and its eight command categories, the red `U` for an unresolved point,
  enable/disable, the horizontal splitter. An engineer who knows Desigo should
  not have to relearn anything.
- Decision-table-first authoring in the Builder, because that is Siemens' own
  recommended design artifact.
- Block-diagram authoring in the Blocks pane, because a signal path is not a
  mode table and forcing one into the other helps nobody.

---

## 4. Research provenance

Four sources, in decreasing authority for a modern PXC:

1. **Desigo CC PPCL Editor Command Assist** — read from the user's live system
   via screenshots. The only source for `ADAPTM`, `ADAPTS`, `LSQ2`, `LSQDAT`
   signatures.
2. **Desigo CC engineering help** — the user supplied the full extract as
   `PPCL.md` (3,537 lines) at `C:\Users\JamesCupps\Downloads\PPCL.md`. This is
   the *compiler's* rulebook: operand and operator limits, the common compiler
   error list, point referencing, Cross Trunk, program naming, the subroutine
   benefit table, the keyboard shortcuts, and the **Command Assist category
   list** now transcribed verbatim into `spec.COMMAND_CATEGORIES`.
3. **APOGEE PPCL User's Manual 125-1896 Rev. 5 (10/00)** — the language itself.
4. **PXC.A Reference Manual A6V12954388.**

### Obtained 2026-09-18

**`A6V10374898` "PXC.A PPCL User Guide"** — the document listed here for months
as needing a Siemens rep. It is **public** at
<https://sid.siemens.com/r/A6V10374898>, no login. Mined: it gave us `GETVAL`,
`SETVAL`, the 99-entry BACnet property appendix, the `[NodeName]PointName`
reference form, PXC.A program capacities, and the `DC` pattern table.

**`A6V12954388` PXC.A Reference Manual** — 112-page PDF, downloaded from
Siemens' public support cache. **Not yet read.**

See `docs/RESEARCH-LOG.md` for the full sweep, every source URL, and what is
deliberately *not* copied (Siemens' SSTO coefficient formulas, and the
copyrighted MEC100K listing in Appendix C).

### Discrepancies found, and how they were resolved

| Conflict | Resolution |
|---|---|
| Operand limit: 13 (Rev 5) vs 16 (Desigo) | Firmware-dependent. `E314` says which it used |
| Arc-tangent: `ATN` vs `ARC` | **ATN.** Confirmed twice from the same Desigo page — its command list says ATN while its precedence table says ARC. `E118` rejects ARC and says so |
| `DC` pattern: Table 4-1 vs the worked example | **Table 4-1** |
| Program name characters | The explicit exclusion list |
| `GOTO` to a missing line | Error on APOGEE, warning on older firmware |

### The single most valuable line found

> **backwards GOTO found.** With the exception of the last GOTO in the
> program, there was a GOTO found that refers to an earlier line number.

Rule `W203` implements exactly that split, and it is asserted as a property of
every program the sequence and block compilers produce.

### Competitive survey (done 2026-08-27)

| Tool | What it is |
|---|---|
| `mitchpaulus/vim-siemens-ppcl` | Vim syntax highlighting, plus a transcription of the manual |
| `mitchpaulus/siemens-ppcl-udl` | Notepad++ user-defined language |
| `mitchpaulus/siemens-ppcl-kde-syntax-highlighting` | Kate syntax |
| Sublime "PPCL Language Syntax and Editor" | The richest third-party tool: highlighting, comment toggle, renumber with GOTO rewriting, DEFINE toggle, separator swap, block duplication. ~2,000 installs, last touched ~6 years ago |
| `delphian/ppcl-library` | Example programs. Four are our `samples/` fixtures |
| Siemens PPCL Editor (Desigo CC / N4) | Create, modify, Command Assist, renumber, search, compile, enable/disable, trace bits, splitter |

**Nothing else has a linter, a simulator, a debugger, a test bench or a block
editor.** The Sublime package's feature set is now fully covered by
`ppcl/transforms.py`, plus the enable/disable and quick numbering from Desigo.

---

## 5. Verified versus approximated — be honest about this

| Claim | Status |
|---|---|
| Command signatures, point types, priorities, precedence, reserved words, Command Assist categories | **Manual-verified**, cited in `spec.py` |
| Parser | **Empirically tested** — 5 real programs, zero errors, plus unit tests |
| `TABLE`, `DBSWIT`, `MIN`/`MAX`, `TOD`, `WAIT`, `SAMPLE`, priority arbitration | **Manual-verified**, behaviour fully specified |
| `LOOP` **output values** | **APPROXIMATED.** Siemens does not publish the PID form. Timing/inputs/outputs are exact; the computed `cv` is indicative and says so at runtime. **Never present it as tuning guidance.** |
| Equipment models (`ppcl/plant/`) | **First-order lumped approximations** in IP units. Not a load calculation, no dehumidification |
| `SSTO`, `PDL*`, `OIP`, `DC`/`DCR` execution | **Not simulated** — traced as no-ops |

**Nothing has been validated against a live panel.**

### Open questions, each with the test that settles it

1. Exact `LOOP` PID form — drive a known `pv` step on an isolated PXC, log `cv`, fit.
2. Line-evaluation rate on current PXC hardware — counter program, timed, FLN count varied.
3. Does a bare `RELEAS` clear `@SMOKE`? Manual is silent.
4. ~~`DC` example vs Table 4-1~~ — **CLOSED 2026-09-18.** A6V10374898 Table 3-1
   confirms Table 4-1, with a worked example that agrees with it this time.
   `generator.duty_cycle_pattern` already matched; now pinned by tests.
5. Which operand limit the PXC.A compiler enforces, 13 or 16. **Now partly
   answerable without a lab**: the panel exposes `@MaxPPCLChs` (property 5165)
   as a readable property, so `GETVAL` can ask it directly.
6. ~~What A6V10374898 changed~~ — **CLOSED 2026-09-18.** Adds `GETVAL`,
   `SETVAL`, the property appendix, `[Node]Point`; drops `LSQ2`/`LSQDAT`.
7. ~~BACnet **property** referencing syntax~~ — **CLOSED 2026-09-18.**
   `@ShortName` or a numeric property identifier, via `GETVAL`/`SETVAL`.
   Unresolved sub-point: Appendix B says the `@` is required, the Chapter 3
   examples omit it. Both accepted; `@` is emitted.
8. `LSQ2`'s `execution` parameter — what values it accepts.
9. **New:** whether a disabled statement has any representation in an exported
   text file. The workbench uses a `C [DISABLED] ` comment marker as its own
   convention and warns about it; if Desigo has a real one, adopt it.

---

## 6. What is genuinely thin — the honest gap list

The engine is deep and the UI now covers it. What remains:

### Blocks
- No copy/paste, no multi-select, no undo on the canvas.
- No sub-diagrams or reusable macro blocks, so a large plant is one big canvas.
- The 16-local ceiling is enforced with a clear message, but there is no
  "split this diagram for me".
- No alignment guides; `Tidy` is a crude left-to-right pass.

### Editor
- Undo is the browser textarea's own, so a transform is not undoable as one
  step. This is the most likely first complaint.
- No diff view against the file on disk.
- No column selection or multi-cursor.

### Bench
- Still one AHU preset family. No VAV boxes, no multi-zone, no central plant
  loop with several AHUs on it.
- No scenario save/load, one fault at a time, no comparing two runs.
- The debugger cannot run *backwards*, and cannot break on a point leaving a
  range (only on a write or a condition).

### Missing entirely
- **Control-flow graph pane.** The `graph` CLI exists and `/api/analyze`
  returns everything needed; nothing renders it.
- **Panel view** — several programs at once, shared-point conflicts, the
  round-robin evaluation budget the manual warns about.
- **BACnet live-point adapter** (read-only).
- **AI assistance** — the user has asked for this eventually. See §7.

---

## 7. Roadmap, in priority order

### Immediate — driven by real programs
**Keep running real site programs through `lint`, `bench` and `db --check`.**
Every pass so far has produced rule tuning — severity levels that overstated
what a compiler actually rejects, false positives on site idioms, and point
reference forms the parser had not seen. Do not tune rules speculatively;
tune them against programs that are running in a building.

### Tier 1 — the things a daily user hits first
1. **Undo/redo as an application concern**, so a transform, a renumber and a
   format are each one undo step. Currently a transform is unrecoverable
   except by reopening the file.
2. **Flow graph pane** — clickable, highlighting steady-state versus run-once
   lines, drawn from `/api/analyze`.
3. **Canvas copy/paste, multi-select and undo** in the Blocks pane.
4. **Diff view** against the file on disk, before saving.

### Tier 2 — depth
5. **Panel view** — several programs, cross-program point conflicts, the
   round-robin budget.
6. **Bench depth** — scenario save/load, several faults at once, compare two
   runs, VAV and multi-zone presets.
7. **Sub-diagrams / macro blocks**, which is also the answer to the 16-local
   ceiling.

### Tier 3 — integration
8. **BACnet point adapter.** Read live values via bacpypes3 (optional import)
   to seed a bench run from real building state. **Read-only.**
9. Import from a Desigo CC export / P2 database file.

### Done since this list was written (2026-09-18)

- **The plugin.** Skill + MCP server + slash commands; the repo is installable.
- **A6V10374898 mined** — `GETVAL`/`SETVAL`, the property appendix,
  `[Node]Point`, the `DC` pattern, SSTO, ADAPTM/ADAPTS, PXC.A rules.
- **PXC.A firmware** added, with its own limits and `E119` enforcing
  availability. `OIP` is rejected there and the linter now says so.
- Rules added: `W336` (SETVAL to a behaviour-changing property), `W337`
  (SSTO whose times nothing reads), `E119` (firmware availability).

### Tier 3.5 — the reference deliverable (the user has asked for this)

**A PPCL quick reference a working tech would actually carry**, and later a
quick-reference app. Not a rewrite of the manual: the thing you want at 6am in
a mechanical room with a laptop balanced on a fan housing.

**This is gated, deliberately.** It ships only once the language is known
completely and accurately, because a quick reference that is 95% right is worse
than none -- it gets trusted and then it is wrong about the one thing you
looked up. Every previous research pass has turned up a correction (`OIP` dead
on PXC.A, the line limit being 512 not 66, `LOCAL`'s sixteen being per
statement, the `W202` severity, this site not being PXC.A at all). Until a pass
produces no corrections, the language is not known well enough to condense.

The material already exists and is cited -- `spec.py`, the 79 rules,
`PPCL-REFERENCE.md`, `helpdocs.py`, the panel error tables. The work is
selection and layout, not discovery. Likely shape:

- the execution model in five statements, and the priority table
- the failure catalogue, each entry with its symptom in the building
- every command on one line: signature, limits, firmware availability
- the panel's own error codes, compiler and runtime, and what each means
- what differs by firmware family, in one table

Generate it from `spec.py` rather than writing it by hand, so it cannot drift
from what the linter enforces. An app is a later step over the same data; the
MCP server and `ppcl explain` already answer these questions programmatically.

### Tier 3.6 — Insight versus Desigo, deliberately compared

Both document sets are on this machine and both have been mined, but never
**against each other**. Two questions worth answering properly:

1. **What did Desigo update?** Where the two disagree, Desigo is the current
   authority for a BACnet ALN site and Insight is the historical one.
2. **What does Insight have that Desigo dropped?** The Insight Program Editor
   help is far deeper on the language itself -- 736 pages, a page per command
   with worked examples, the compiler error list, the decision-table and
   pseudocode method. The Desigo PPCL pages are a glossary by comparison. A
   fact being absent from Desigo does not make it untrue.

Already known to differ: `LSQ2`/`LSQDAT` are documented in Insight and absent
from the PXC.A manual; `GETVAL`/`SETVAL` are the reverse. The `ARC`/`ATN`
spelling conflict survives in all four document sets.

### Tier 4 — AI assistance (the user has asked for this)
The useful shape is narrow and grounded, not a chat box:
- **Explain this program** — summarise a real program's modes and interlocks
  against `analyzer` output, not from the raw text.
- **Sequence of operation → sequence document.** The Builder's document model
  is already the target; this is a translation task with a checkable result,
  because the compiler and linter verify the output.
- **Suggest a fix for a diagnostic**, constrained to the rule's own
  `suggestion` field and the spec.
Anything that writes PPCL must go through the existing compilers so the
guarantees still hold. Do not add a path that emits unchecked text.

---

## 8. Working notes for a fresh session

- **Use the Edit tool for structural changes.** Patch scripts with string
  replacement have silently no-opped in this project before. Where a script is
  unavoidable, `assert old in s` first — that caught two failures this session.
- **Heredocs with `\\n` inside a Python string get mangled.** One produced a
  literal newline inside a string literal and a syntax error. Prefer the Edit
  tool, or write the script to a file and run it.
- **The bench is the arbiter.** It has now caught defects three times that
  looked like modelling artifacts and were real. Investigate divergence, do not
  explain it away.
- **Drive the UI, do not assume it works.** Every UI defect found this session
  was found by running it in a browser: a 500 from an unpacking change, two
  servers sharing a port on Windows, a compile error hidden behind the wrong
  output tab. Screenshots may not be available; `javascript_tool` against the
  live page works well.
- **Windows `allow_reuse_address` means "hijack the port".** Two workbenches
  bound to one port and split requests at random, so an endpoint answered 404
  on one reload and 200 on the next. Fixed with `SO_EXCLUSIVEADDRUSE`; do not
  put it back.
- **Test durations matter.** The suite is ~105s, dominated by bench runs.
- `samples/` holds real third-party programs with genuine defects. They are
  regression fixtures — **do not "fix" them.**
- Site data: run `ppcl redact` before anything leaves the machine.

### Key invariants

Listed in `CLAUDE.md`. The load-bearing ones:

- `spec.py` is manual-derived; never invent a signature or limit.
- The sequence and block compilers' guarantees are asserted as **properties of
  every compiled program** in `tests/test_sequence.py` and
  `tests/test_blocks.py`, not as string comparisons. Do not weaken them.
- The block compiler's expression-first cost model is deliberate; do not
  simplify it to one-local-per-block.
- `ROUTES` is the routing table; `api_ide` merges into it rather than copying.
- The web `Workspace` sandbox is load-bearing; every path goes through
  `resolve`.

---

## 9. Files worth reading, in order

1. `CLAUDE.md` — project conventions and invariants
2. `docs/PPCL-REFERENCE.md` — the language, the failure catalogue, the
   discrepancies, the verification-status table
3. `ppcl/spec.py` — the language as data
4. `ppcl/blocks/compiler.py` — why a block becomes an expression, not a line
5. `ppcl/sequence/compiler.py` — the guarantees and why statement order matters
6. `ppcl/debug.py` — the debugger, and what each breakpoint kind is *for*
7. `ppcl/plant/bench.py` — how PPCL is coupled to simulated equipment
8. `ppcl/helpdocs.py` — the user-facing explanation of all of the above
9. `README.md` — user-facing usage
