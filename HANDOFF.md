# PPCL Workbench — session state and roadmap

**Read this first in a fresh session.** It carries the context that would
otherwise be lost: what exists, what is verified versus assumed, which
decisions were deliberate, and what to build next.

Last updated: 2026-09-18. Fourteen research passes; see docs/RESEARCH-LOG.md.

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
   Siemens' shipped 42-program PPCL application library, an Insight database
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
python -m pytest tests -q          # 502 tests, ~150s
python -m ppcl.cli serve           # the app
python -m ppcl.cli lint samples    # exercises the CLI on real programs
python -m ppcl.cli help            # the built-in documentation
```

---

## 2. Current state

**502 tests passing.** ~21,000 lines Python, ~4,900 lines UI, ~4,700 lines
tests. 88 lint rules, 66 commands, 99 BACnet properties, 30 block types,
17 CLI subcommands, 11 MCP tools, 12 help pages, 20 settings, 6 firmware
families, 29 panel error codes.

| Layer | Module | State |
|---|---|---|
| Language spec | `ppcl/spec.py` | Complete, manual-derived, cited. Now also carries Siemens' own Command Assist categories |
| Lexer / parser | `ppcl/lexer.py`, `parser.py` | Solid. Parses every real program seen so far with zero errors |
| Analysis | `ppcl/analyzer.py` | Control flow with wrap edge + Tarjan SCC; point read/write tracking |
| Linter | `ppcl/linter.py`, `rules/` | 88 rules, each citing its source |
| Formatter | `ppcl/formatter.py` | Format + renumber with exact reference rewriting |
| Simulator | `ppcl/simulator.py` | Interpreter with real priority arbitration, panel-faithful line budget |
| **Debugger** | `ppcl/debug.py` | Line / write / condition breakpoints, step, step over, step out, run to cursor, watch with priority, mid-run override, coverage |
| Generator | `ppcl/generator.py` | `Builder` (symbolic labels) + 5 fixed templates |
| Sequence | `ppcl/sequence/` | Document model, text DSL, compiler with hard guarantees |
| **Blocks** | `ppcl/blocks/` | 30 block types; expression-first compiler with feedback handling |
| **Transforms** | `ppcl/transforms.py` | DEFINE expand/collapse, separator swap, clone with rename, enable/disable, comment |
| **Points** | `ppcl/points.py` | CSV/JSON import, alias column mapping, unresolved-reference check, slope/intercept for panel error E12 |
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

Everything below is on this machine or public. `docs/RESEARCH-LOG.md` has the
full sweep across fifteen passes, every source URL, and what is deliberately
not copied. **Nothing licensed or site-confidential is reproduced in this
repository** -- findings from those corpora are described, not quoted.

### The corpora, and what each is good for

| Source | Size | Verdict |
|---|---|---|
| **Insight 3.15 `Proged.chm`** | 736 pages | *Exhausted.* The deepest source on the language itself: a page per command with worked examples, the compiler error list, the decision-table method |
| **Insight 3.15 `Point.chm`** | 320 pages | *Exhausted.* Point-type taxonomy, bundled-point decomposition |
| **Desigo CC Engineering help** | 3,618 pages, 436 mentioning PPCL | Mined. Its PPCL section is a glossary and is thinner than Insight's |
| **Desigo CC Operating help** | 844 pages | *Strict subset of Engineering* -- 733 shared titles, zero unique, 702 identical. Nothing to do |
| **A6V10374898** PXC.A PPCL User Guide, rev `_j` | HTML, public | Mined across five passes. `GETVAL`/`SETVAL`, the property appendix, `[Node]Point`, the ten removed statements |
| **A6V10324350** BACnet ALN Field Panel, 125-3020 | 415 pages | Ch.10 and App.C mined. **The manual that applies to the reference site** |
| **A6V12893115** PXC.A Web Interface User Guide | HTML, public | The onboard editor PXC.A uses instead of Desigo CC. The `# ` disable syntax |
| **A6V12954388** PXC.A Reference | 112 pages | Workflow; points at A6V10374898 for the language |
| **A6V13998441** PXC.A Modernization, 2026-04 | 23 pages | Newest document here. Its cross-reference is what led to the removed-statements page |
| **Siemens' shipped application library** | 42 programs, 7,863 lines | *The regression corpus.* Found three parser bugs and one undocumented command. It ships in two product trees; the first collection of it took both copies, so every count taken from it before 2026-09-18 was exactly doubled |
| **The reference site's own programs** | 42 from Desigo, 22 older | What every severity decision is tuned against |
| **`PROTOCOL.md`, the P2 technical reference** | 11,216 lines | The other project's own document, read 2026-09-18. Its §14 is PPCL over the wire. Gave the 71-member `PPCL_statement_type` enum — five statement tokens no manual documents — and confirmed `report.py`'s five per-line flags against the controller's `PPCL_data` structure |
| **An independent P2 wire corpus** | 2,644 lines | *A cross-check, not a source.* Programs running on panels at a working site, recovered from upload responses by a separate project with no source in common with this one. The parser met all 2,644 without a failure. Nothing in it is reproducible from this repository, so its findings are tiered below the rest |

**Not obtainable:** `A6V12954390`, "PPCL User Manual", named in Siemens' 2026
datasheets but 404 at every public URL. Either partner-restricted or a typo --
their own docs misprint document numbers. Do not spend more time on it.

### Original four, in decreasing authority for a modern PXC:

1. **Desigo CC PPCL Editor Command Assist** — read from the user's live system
   via screenshots. ~~The only source for `ADAPTM`, `ADAPTS`, `LSQ2`,
   `LSQDAT` signatures.~~ **Not true, corrected 2026-09-18:** the Insight
   Program Editor documents all four in full -- a command page, a Statement
   Arguments page and a worked example each. Command Assist was the first
   source read, not the only one.
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

**`A6V12954388` PXC.A Reference Manual** — read. It is a workflow document and
points at `A6V10374898` for the language.

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
| `GOTO` to a missing line | **Warning everywhere.** Was an error on the belief Desigo CC refuses to save it; 36 came out of a live Desigo CC in running programs |
| Integer where a decimal is documented | **Warning.** 125-1896 forbids it, later docs do not, and shipped code uses integers |
| `LOCAL`'s sixteen | **Per statement, not per program.** The compiler chunks declarations rather than refusing |
| Adaptive control on PXC.A | **Gone.** Inferred from a table's whitespace for four passes, then stated in words on the removed-statements page |
| Comment length | Counts the **comment text**, not the line number or the `C` |
| `NODE0` or `NODE1` — where the node range starts | **Zero.** The Program Editor's reserved-word page says `NODE1`; the dedicated page in the same book says "from 0 through 99" in prose and has `NODE0` in its title, as do that book's glossary, the Debugger help and Desigo CC. Four to one, and the one is contradicted by its own book. Second defect found in that one table |
| `EQUAL` / `LESS`: reserved words, or a misread description column? | **Both reserved.** Neither has an operator syntax, but both hold bare alphabetical cells in the Program Editor's enumerated list, which contains no description column at all. `EQUAL` is in 125-1896 Ch. 5 as well; `LESS` is not, and adding it closed the only real gap either published list had against `RESERVED_WORDS` |

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
| Parser | **Empirically tested at scale.** 42 programs from a live Desigo CC and 7,858 of 7,863 lines of Siemens' shipped library (the other five are that library's own syntax errors). Separately, an independent P2 wire corpus ran it over **2,644 lines recovered from programs running on panels: all 2,644 parsed** |
| `TABLE`, `DBSWIT`, `MIN`/`MAX`, `TOD`, `WAIT`, `SAMPLE`, priority arbitration | **Manual-verified**, behaviour fully specified |
| `LOOP` **output values** | **APPROXIMATED.** Siemens does not publish the PID form. Timing/inputs/outputs are exact; the computed `cv` is indicative and says so at runtime. **Never present it as tuning guidance.** |
| Equipment models (`ppcl/plant/`) | **First-order lumped approximations** in IP units. Not a load calculation, no dehumidification |
| `SSTO`, `PDL*`, `OIP`, `DC`/`DCR` execution | **Not simulated** — traced as no-ops |

**No statement's *behaviour* has been validated against a live panel.** The
grammar has now met code that panels are executing (above) and handled all of
it; nothing has watched a statement execute. Keep the two claims apart.

### Open questions, each with the test that settles it

1. Exact `LOOP` PID form — drive a known `pv` step on an isolated PXC, log `cv`, fit.
2. Line-evaluation rate on current PXC hardware — and **PXC.A already
   reports it**. The Web Interface exposes per-program cycle time (average over
   the last ten cycles, highest, lowest, in ms) and those metrics are mappable
   to virtual points and trendable. Cheapest open question here to close, and
   it needs no lab: map the three to virtual points on a panel in service.
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
10. **New:** does a panel absorb a dotted-operator segment into an *unquoted*
    point name? `AHU1.MIN.SP` is unambiguous — `.MIN.` is not an operator — but
    `AHU1.ROOT.SP` is not, and this lexer splits it. No corpus examined
    contains one, which is why it is a question and not a rule.

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

### Immediate — two decisions that real corpora have now earned

Running real programs is no longer the pending item; it has happened, twice,
and it is what produced most of the recent work. What is pending is acting on
what it measured.

**1. Decide `W104`'s severity.** The 66-character MMI limit is now measured on
two independent corpora and dominates both:

| Corpus | `W104` share of all warnings |
|---|---|
| The reference site's 22 programs | 65% |
| Siemens' own 42-program library | **70%** (1,067 of 1,520) |

Seventy per cent of the linter's output on Siemens' reference code concerns a
port nobody enters programs through, and the rule's own detail text admits it
"only matters if the program is re-entered through the MMI port". A linter that
spends two thirds of its voice on that gets turned off. This is a judgement
call for the user, not a unilateral change — but it should be *made*, not left.

**2. ~~Check `W330`.~~ Closed 2026-09-18 — and it found a counting error
first.** The 340 was 170; the library ships in two product trees and the corpus
held both copies. Of the 170, **every one is `@OPER`**. Not a false positive
and not one finding: most are points *driven every pass*, which cannot strand
but does overwrite an operator's own command within a second or two, and the
rest are the strandable case the rule's text actually describes.

`W341` now carries the first — INFO, because a lamp test and a hard interlock
are both legitimate — and `W330` keeps the second as a WARNING. On the library
the 170 become **22 `W330` + 148 `W341`**; on the reference site, 4 + 7.

One consequence for decision 1: moving 148 findings out of WARNING raises
`W104`'s share of the library's warnings from 70% to **78%**. The case for
regrading it got stronger, not weaker.

### Keep doing — it has paid every time

Run real programs through `lint` before believing anything. Every pass has
produced a correction: severity levels that overstated what a compiler
rejects, false positives on site idioms, point reference forms the parser had
not seen, and three parser bugs that only Siemens' own code exercised. Do not
tune rules speculatively; tune them against programs that run in a building.

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

**Published.** <https://github.com/jamesccupps/PPCL>, MIT.

**The plugin.** Skill + MCP server + slash commands; the repo is installable.

**Read the panel, not just the file.** `ppcl/report.py` parses a `PPCL DISPLAY
REPORT` -- which lines are disabled, which have an unresolved point, which have
ever executed -- and `lint --report` folds it in. `Line.disabled` drops a
disabled line out of the control-flow graph exactly as the panel drops it.

**Parser fixes, all found by real code:**

- unquoted `%X%NAME` DEFINE substitutions (116 lines of Siemens' library)
- substitution tails starting with a digit
- `@NONE.AND.` -- the `@`-name branch was missing the `_DOTOPS` guard
- PXC.A's `# ` disable syntax, which used to be a parse error
- `[NodeName]PointName` references

**Corrections to things previously believed:**

- the reference site is **APOGEE BACnet ALN, not PXC.A** -- inferred from the
  supervisor and never checked against a program until the seventh pass
- `W202` is a warning, not an error
- `E113` became `W113`
- `LOCAL`'s sixteen is per statement
- comment length excludes the line number and the `C`
- PXC.A removes **ten** statements, not one

**Rules added:** `W113` (regraded), `W313` (PDL order), `W336`, `W337`, `W338`
(LSQ2 rows), `W339` (RELEAS storm), `W340` (device-local across the network),
`E119` (firmware availability), `E120` (parenthesis in a point name),
`R701`-`R704` (panel report findings).

**Cross-checked against a wire corpus (fifteenth pass).** An independent
project reading APOGEE P2 traffic ran this parser over 2,644 lines recovered
from running panels -- all 2,644 parsed, including two dozen carrying a PPCL
keyword as a dotted segment of a point name. It also proposed removing `EQUAL`
and `LESS` from `RESERVED_WORDS` on the theory that both came from misreading a
description column. Tested and excluded: the Program Editor's enumerated list
has no description column, and both words hold their own alphabetical cell.
`EQUAL` is confirmed in 125-1896 Ch. 5 too. **`LESS` was missing here and has
been added** -- the only real gap either published list had. Four tests came out
of the pass, and open question 10.

**Spec additions:** the ten PXC.A removals with Siemens' reason for each, 29
panel error codes (compiler `R` and runtime `E`, and the distinction between
them), the PDL command order and panel roles, bundled-point proof optionality,
slope/intercept on points, and `LSTSQR` -- a command that appears in no manual
anywhere, recovered from Siemens' own shipped library.

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

The material already exists and is cited -- `spec.py`, the 88 rules,
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
