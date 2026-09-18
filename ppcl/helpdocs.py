"""In-app documentation.

Kept as data in a module rather than as files so it ships with the package,
needs no path resolution, and can be tested. The rendering is a deliberately
small markdown subset -- headings, paragraphs, lists, code blocks and tables --
because the alternative is a dependency.

Two rules for anything written here:

* **Say what is verified and what is not.** Where a statement rests on the
  manual, cite it. Where it rests on an approximation -- the LOOP output, the
  equipment models -- say so in the same breath, because someone will
  eventually make a tuning decision from this text.
* **Write for the person who has to fix it at 6am.** No preamble.
"""

from __future__ import annotations

PAGES = [

# --------------------------------------------------------------------------

{
    "id": "start",
    "title": "Getting started",
    "category": "Using the workbench",
    "summary": "What each pane is for and the order to use them in.",
    "body": """
# Getting started

The workbench has five panes. They are meant to be used left to right.

## Editor

A PPCL text editor that checks as you type. Everything Desigo's PPCL Editor
does to a program, this does to a file: go to a statement, find and replace,
renumber, enable and disable statements, and Command Assist while typing.
It adds what Desigo has no place for -- 72 lint rules, each citing the manual
page it comes from.

Nothing here talks to a panel. You edit a file and load it yourself.

## Builder

Author from a **decision table** -- equipment down the side, modes across the
top. This is Siemens' own recommended planning method, not an invention of
this tool: read the sequence, determine the modes of operation, identify the
controls, then organise them as a table.

The compiler guarantees things you would otherwise have to check by eye:
exactly one backward GOTO and it is the last statement, every interlock has a
matching RELEAS at the same priority, and no time-based command ends up inside
an IF or a subroutine.

## Blocks

Draw the control as blocks and wires, the way a function-block editor works,
and compile that to PPCL. Use it when the logic is a signal path rather than a
mode table -- an economizer, a reset chain, a staging sequence.

## Bench

Run a program against simulated equipment. Sensor lag, actuator stroke time,
damper leakage and coil capacity are modelled well enough to make a control
sequence behave the way it will on the roof. Inject a fault and see what the
program does about it.

The **debugger** lives here: breakpoints, single stepping, a watch window and
the ability to override a point mid-run.

## Settings

Firmware, editor behaviour, bench defaults, and the point database.

## The order that works

1. Write or open a program in the **Editor** until it lints clean.
2. Send it to the **Bench** and run it against the weather you care about.
3. Inject the fault you are worried about and run it again.
4. Only then load it to a panel.
""",
    "see_also": ["editor", "bench", "blocks", "safety"],
},

# --------------------------------------------------------------------------

{
    "id": "safety",
    "title": "What this tool will and will not do",
    "category": "Using the workbench",
    "summary": "Scope, and why it is drawn where it is.",
    "body": """
# What this tool will and will not do

## It will not touch a building system

There is no panel write, no network client, no BACnet or P2 connection. The
workbench reads and writes **files**. You load the result through Insight or
Desigo CC yourself, with whatever change control your site requires.

That is deliberate. A tool that can both generate a program and push it is one
misclick from an occupied floor with no supply fan.

## The web server is local

It binds `127.0.0.1` and has no authentication, because it does not need any
when only this machine can reach it. Every file path is resolved inside the
workspace directory; absolute paths and `..` are rejected rather than
normalised. Binding to another address requires an explicit flag and prints a
warning, because the API can write files.

## Simulation is a model, not the plant

The equipment models are first-order lumped approximations in IP units. They
are good for exercising control logic and worthless as a load calculation.
There is no dehumidification and no real psychrometrics.

**The LOOP output value is approximated.** Siemens does not publish the PID
form. The timing, the inputs, the output limits and the anti-windup behaviour
are exact; the number that comes out is indicative. Never take a tuning
constant from this bench to a real panel.

## Nothing here has been validated against a live panel

Every command signature traces to the manual or to Desigo CC's own Command
Assist. That is a much stronger claim than "tested", and it is the only claim
being made.

## Before anything leaves this machine

Run `ppcl redact` on it. Real point names carry building, floor and tenant
information. The redactor reports which generic terms it preserved, because
`OCC` is both "occupied" and a building abbreviation.
""",
    "see_also": ["start", "bench"],
},

# --------------------------------------------------------------------------

{
    "id": "editor",
    "title": "Editor",
    "category": "Using the workbench",
    "summary": "Keyboard, Command Assist, transforms, and the gutter marks.",
    "body": """
# Editor

## Keyboard

The shortcuts match the Desigo CC PPCL Editor where it has one, so muscle
memory transfers.

| Key | Does |
| --- | --- |
| `Ctrl` `G` | Go to a program statement by line number |
| `Ctrl` `F` | Find, and replace when the editor is focused |
| `Ctrl` `S` | Save |
| `Ctrl` `Z` / `Ctrl` `Y` | Undo / redo |
| `Ctrl` `/` | Comment or uncomment the selected statements |
| `Ctrl` `Space` | Command Assist |
| `Ctrl` wheel | Zoom the editor font |
| `F5` | Run in the bench |
| `F9` | Toggle a breakpoint on the current line |

## Quick numbering

With quick numbering on, pressing Enter at the end of a line starts the next
one already numbered, using the increment from Settings. Ten is the manual's
own recommendation: it leaves room to insert without renumbering.

## Gutter marks

| Mark | Means |
| --- | --- |
| red dot | an error -- the panel compiler will reject this |
| amber dot | a warning -- it will compile and probably misbehave |
| `U` | the point is not in the loaded point database |
| `B` | a breakpoint |
| grey text | the statement is disabled |

The `U` is the same finding the Desigo editor shows in its status column. It
only appears once a point database is loaded in Settings.

## Transforms

Under the Transform menu, each reports exactly what it changed:

* **Expand / collapse DEFINE** -- switch between `%A01%.RAF` and
  `Bld01.Ahu01.RAF`. Expand before searching for a point name; collapse before
  saving.
* **Point name separators** -- swap `.` and `_` inside names and nowhere else.
  This runs on tokens, so `80.0` and `.AND.` are untouched. That is the case a
  search-and-replace gets wrong.
* **Clone lines** -- copy a block of statements to the end of the program with
  point renames applied and line references retargeted. This is the manual's
  own advice about reusing code between similar devices, made mechanical.
* **Disable / enable** -- a disabled line is written as a comment carrying a
  marker. A text file has nowhere to keep a real disable flag, so this is a
  workbench convention: **re-enable before loading to a panel**, or the
  statement is simply gone.

## Renumbering

Renumbering rewrites every GOTO, GOSUB, ACT, DEACT, ENABLE, DISABL and ONPWRT
reference, and the trailing line arguments of LSQ2. It works at the token
level, so statement text survives byte for byte.

It **refuses** when the program has duplicate line numbers, because the two
possible repairs give different programs and only you know which is right:

* keep every line and shift the duplicates apart -- changes what the panel runs
* keep the panel's behaviour and drop the shadowed lines -- loses code
""",
    "see_also": ["start", "points", "rules"],
},

# --------------------------------------------------------------------------

{
    "id": "blocks",
    "title": "Block diagrams",
    "category": "Using the workbench",
    "summary": "Wiring blocks together, and what the compiler does with them.",
    "body": """
# Block diagrams

Drag a block from the palette, wire an output to an input, and the diagram
compiles to PPCL as you work.

## Rules of the canvas

* An output may feed **any number** of inputs. An input takes **exactly one**
  wire. That asymmetry is what makes the graph unambiguous.
* A loop of pure logic is **refused**, naming the blocks in it -- there is no
  value such a loop could settle on.
* A loop through a **Latch** or a **Delay** is allowed. Those hold state, so
  the loop reads the previous pass's value, which is what feedback means on a
  scanning controller. The generated program says so in a comment.

## What becomes a line, and what does not

Pure logic and arithmetic compile to **expressions**, not statements. An AND
of two comparisons is one PPCL expression, so a twelve-block diagram of
ordinary logic becomes a handful of statements that read like something an
engineer wrote.

A value condenses into a local variable when:

* it feeds more than one block;
* the block holds state, is timed, or writes into a point (LOOP, TABLE, MIN,
  MAX, DBSWIT, WAIT, TOD);
* the expression is about to exceed the 16-operand or 32-operator statement
  limit;
* **you gave the block a name.**

That last one is the point. A named block becomes a named local, so a wire on
the drawing has a name you can look up in the point list at the panel. Name
every value you would want to see while troubleshooting; leave the rest blank
and they cost nothing.

## The local variable ceiling

PPCL allows **16** local variables per program, and that is the real limit on
diagram size -- not the canvas. If you hit it, clear the names off blocks you
do not need to watch, or split the diagram into two programs.

## Blocks worth knowing

* **Deadband switch** rather than a bare Compare, for anything that starts and
  stops equipment. A comparison with no deadband short-cycles a compressor on
  sensor noise.
* **Latch** for a safety that must stay tripped. The reset input is required,
  because a latch with no reset path is the most common PPCL field defect.
* **Command point** with *Release when false* switched on, for any interlock.
  It writes the matching RELEAS at the same priority. Without it the point is
  owned forever.
* **Reset schedule** rather than Scale when the curve must flatten outside its
  range, or needs more than two breakpoints.
""",
    "see_also": ["priority", "builder", "start"],
},

# --------------------------------------------------------------------------

{
    "id": "builder",
    "title": "Decision tables",
    "category": "Using the workbench",
    "summary": "Modes, interlocks, resets, loops, and the order they compile in.",
    "body": """
# Decision tables

A decision table puts equipment down the side and modes of operation across
the top. Each cell says what that equipment does in that mode. It is Siemens'
own recommended way to organise a sequence before writing any code.

## Modes are ordered, and the last match wins

Modes are listed lowest priority first. Every mode whose condition holds sets
the mode number, so the **last** one that matches is the one that runs. That
makes "shutdown beats occupied" a matter of list order rather than of nested
conditions.

## Cell values

| Value | Emits |
| --- | --- |
| `on` `off` `auto` `fast` `slow` | the matching point command |
| `open` / `closed` | `SET(100.0, pt)` / `SET(0.0, pt)` |
| a number | `SET(value, pt)` |
| `modulate` | **nothing** -- the point is left to its control loop |
| `hold` | nothing -- no command is issued at all |
| `-` | nothing |

`modulate` emitting nothing is the important one. The decision table is
compiled **after** the control loops, so any cell that is not `modulate`
overrides the loop for that mode. That is how "closed on shutdown" beats
"modulate".

## Compilation order

This order is what makes the output correct, and it is not arbitrary:

1. Header, LOCAL declarations, ONPWRT
2. **Interlock latches** -- first, so a mode condition can test one
3. Mode determination -- one pass, last match wins
4. Reset schedules (TABLE)
5. Control loops (LOOP) -- unconditional, so their sample timing holds
6. The decision table -- after the loops, so a cell can override one
7. Free-form rules
8. **Interlock forces** -- last, so a safety overrides everything above it
9. The single closing GOTO

Because latches come before modes, you can write a mode as
`when Freeze is on` and drive an entire shutdown column of the table. That is
better than forcing points one at a time: the table then shows, in one place,
what every piece of equipment does during that shutdown.

## What compilation guarantees

* exactly one backward GOTO, and it is the last statement
* every interlock force has a matching RELEAS **at the same priority**
* no time-based command inside an IF or a subroutine

These are asserted as properties of every compiled program in the test suite,
not checked by string comparison. They will not quietly stop being true.

## One way only

A document compiles to PPCL. Hand-written PPCL does **not** lift back into a
document, and the workbench will not pretend otherwise -- the GOTO structure of
a real program carries intent a table cannot represent.
""",
    "see_also": ["blocks", "priority", "start"],
},

# --------------------------------------------------------------------------

{
    "id": "priority",
    "title": "Point priority",
    "category": "PPCL",
    "summary": "The single largest source of field defects, and how to avoid it.",
    "body": """
# Point priority

Every commandable point carries a priority. A command takes effect only when
its priority is **at least as high** as the point's current priority.

    @NONE  <  @PDL  <  @EMER  <  @SMOKE  <  @OPER

## The failure

A program commands a point at `@EMER` during a trip. The trip clears. The
program issues a bare `RELEAS`, which releases at `@NONE`.

`@NONE` is below `@EMER`, so **the release does nothing.** The point stays
commanded at `@EMER` forever. The fan never restarts, and nothing in the
program looks wrong.

## The rule

**Release at the same priority you commanded at.**

    IF("$FREEZE".EQ.1.0) THEN OFF(@EMER,SFAN)
    IF("$FREEZE".EQ.0.0) THEN RELEAS(@EMER,SFAN)

The workbench enforces this three ways: rules `W330` and `W331` find it in
hand-written code, the sequence compiler always emits the matching release,
and the Command block's *Release when false* option writes it for you.

## Reading a priority at the panel

An operator command at `@OPER` outranks everything a program can do. That is
intended -- it is how a technician takes manual control. It also means a point
someone commanded from a workstation last winter will ignore your program
until it is released.

If the bench says a command was **blocked by point priority**, this is what it
found.
""",
    "see_also": ["rules", "blocks", "builder"],
},

# --------------------------------------------------------------------------

{
    "id": "guidelines",
    "title": "Writing PPCL that works",
    "category": "PPCL",
    "summary": "Siemens' own guidelines, and what they mean in practice.",
    "body": """
# Writing PPCL that works

From the Desigo CC engineering help, with what each one costs when ignored.

## Time-based commands must be reached every pass

LOOP, SAMPLE, TOD, TODSET, TODMOD, WAIT, TIMAVG, SSTO and the PDL family
depend on wall-clock time. Put one behind an IF that is sometimes false, or
inside a subroutine that is not always called, and its timing is wrong in a
way that no single reading will show you.

Rule `E205` finds these.

## The first line must run on every pass

If execution is interrupted -- a power failure, an ENABLE -- the panel resumes
at the **first line of the program**. Anything that must be established at
startup belongs there, not after a branch.

## One backward GOTO, and it is the last statement

The Desigo compiler says it plainly:

> backwards GOTO found. With the exception of the last GOTO in the program,
> there was a GOTO found that refers to an earlier line number.

So the main-loop trampoline at the end is sanctioned, and **every other
backward branch is a compiler error**. An inner busy-wait loop will not load.

Rule `W203` implements exactly that split.

## Statement limits

* **16 operands** per statement on APOGEE firmware; 13 on older panels.
* **32 operators**, where every point reference and every value constant
  counts as an operator too.

Parentheses are free. When a statement gets close, split it with a local.

## Subroutines earn their place or they do not

From the manual's own table -- lines of code against calls per pass:

| | 1 line | 2 lines | 3 lines | 4+ lines |
| --- | --- | --- | --- | --- |
| **1 call** | No | No | No | No |
| **2 calls** | No | No | Even | Yes |
| **3 calls** | No | No | Yes | Yes |
| **4+ calls** | No | Yes | Yes | Yes |

A one-line subroutine is never worth it. Rule `P708` applies this table.

## Line numbering

Number in tens. It leaves room to insert without renumbering, and renumbering
is where GOTO targets get broken.

## Comment lines

`00035 C This section handles the freeze interlock.`

Comments are the only documentation that travels with the program to the
panel. Write them for the person who opens this at 6am, having never seen it.
""",
    "see_also": ["priority", "rules", "editor"],
},

# --------------------------------------------------------------------------

{
    "id": "rules",
    "title": "Lint rules",
    "category": "PPCL",
    "summary": "How the codes are grouped and how to argue with one.",
    "body": """
# Lint rules

| Prefix | Area |
| --- | --- |
| `E1xx` / `W1xx` | syntax and statement structure |
| `E2xx` / `W2xx` | control flow |
| `E3xx` / `W3xx` | semantics -- priority, types, limits |
| `S6xx` | style |
| `P7xx` | performance and panel load |

`E` is an error: the panel compiler will reject it, or it is certainly wrong.
`W` is a warning: it will compile and probably misbehave. `S` and `P` are
advisory.

## Every finding cites its source

A diagnostic carries the manual reference it rests on. That is there so you
can take a finding to a vendor or a controls contractor and point at the page.
A finding you cannot argue with is not worth emitting.

## Severity is graded on purpose

Several rules are not binary, because idiomatic PPCL would otherwise light up
in warnings and get the linter switched off:

* the main-loop `GOTO` trampoline is `INFO`; any other backward `GOTO` is an
  error
* a guarded override of a point is `INFO`; two unconditional writers is a
  warning
* a statement that reads the point it writes -- `MAX` to a floor then `MIN` to
  a ceiling, the standard clamp -- is not counted as a rival writer at all

## Turning one off

Settings has a disabled-rules list. Prefer fixing the code: a rule you always
ignore is worth arguing about instead of suppressing.

## Firmware changes results

The operand limit is 16 on APOGEE and 13 on older firmware, and a `GOTO` to a
missing line is an error on APOGEE but a silent redirect on older panels. Set
the firmware in Settings; diagnostics that depend on it say which one they
used.
""",
    "see_also": ["guidelines", "editor", "priority"],
},

# --------------------------------------------------------------------------

{
    "id": "bench",
    "title": "Bench and debugger",
    "category": "Using the workbench",
    "summary": "Running a program against simulated equipment, and stopping it.",
    "body": """
# Bench and debugger

## What is simulated

A single-zone air handler with mixing box, heating and cooling coils, a fan,
a zone with thermal mass, sensors with lag, and actuators with stroke time.
Weather drives the outside conditions.

Point priority arbitration is **exact** -- if a command is refused because the
point is owned at a higher priority, the bench reports it. That is the defect
this catches most often.

## What is not

* `SSTO`, the `PDL` family, `OIP`, `DC` and `DCR` are traced but not modelled.
* The `LOOP` output value is **approximated**. Timing, inputs and limits are
  exact; the number is indicative. Never tune from it.
* No dehumidification, no psychrometrics, no load calculation.

## Faults

Inject a stuck damper, a failed sensor, a coil with no water, a fan that does
not prove. The question a fault answers is not "does the model still run" but
"does the program do something sensible about it" -- and the answer is
regularly no.

## The debugger

| Control | Does |
| --- | --- |
| Step | one statement, into subroutines |
| Step over | one statement, running any GOSUB to completion |
| Step out | until the current subroutine returns |
| Run | until a breakpoint fires |
| Run to line | until that line is about to execute |

Three kinds of breakpoint:

* **Line** -- for "why does this branch never run".
* **Write to a point** -- stops on the statement that wrote it and names the
  line. For "something is commanding this and I cannot find what".
* **Condition** -- a PPCL expression, evaluated against live point values. For
  "stop when the discharge is below 45 while the fan is proved".

A breakpoint can skip a number of hits before stopping, which is what makes it
usable inside a main loop.

At a stop you can **override any point** and continue. That is the thing you
cannot do on a panel, and it is the reason to test here first.

## Coverage

The debugger tracks which lines have executed. Lines that never run are listed
as starved -- which is what a runaway inner loop actually looks like on a
panel: the program keeps running, but part of it is never reached again.

This is the offline equivalent of the trace bits in the Desigo editor.
""",
    "see_also": ["priority", "start", "safety"],
},

# --------------------------------------------------------------------------

{
    "id": "points",
    "title": "Point database",
    "category": "Using the workbench",
    "summary": "Importing an export, and what it turns on.",
    "body": """
# Point database

Import a CSV or JSON export from Desigo CC, Insight, or a spreadsheet from a
contractor. Column names are matched by alias rather than by position, and any
column that is not recognised is **reported** rather than guessed at.

Loading one turns on three things:

* **Unresolved references.** A point named in the program but not in the
  database is marked `U` in the gutter -- the same finding the Desigo PPCL
  Editor puts in its status column, produced offline before the program is
  loaded.
* **Type-aware rules.** `ON` against an LAO is a mistake the panel accepts and
  then ignores. Knowing each point's type turns a whole class of rules on.
* **Real names in Command Assist.** Type three characters, get the actual
  point.

## Name matching

A reference may be quoted, carry a `$` local sigil, be colon-qualified for an
FLN subpoint, or be a fully qualified hierarchical name. Lookup tries the
whole name first and then the segment after the last separator, so a program
written against `Bld01.Ahu01.RAF` still resolves against a database exported
with short names.

Locals, resident points and status indicators are never reported as
unresolved -- none of them live in the point database.

## Before sharing anything

Point names carry building, floor and tenant information. Run `ppcl redact`
before a program or an export leaves the machine.
""",
    "see_also": ["editor", "safety"],
},

# --------------------------------------------------------------------------

{
    "id": "ssto",
    "title": "Start/stop time optimization",
    "category": "PPCL",
    "summary": "How SSTO decides when to start, and what every coefficient means.",
    "body": """
# Start/stop time optimization

SSTO answers one question: *how early must this equipment start so the zone hits
its setpoint exactly at occupancy, and not before?* A fixed warm-up time is
always wrong — too early on a mild morning, too late after a cold weekend.

It takes **two commands**, and a third to actually do anything.

| Command | Role |
|---|---|
| `SSTOCO` | Describes the zone's thermal behaviour — its coefficients |
| `SSTO` | Calculates the start and stop times from those coefficients |
| `TOD` / `TODSET` | **Actually commands the equipment** |

## SSTO calculates. It does not command.

`SSTO` writes two virtual LAO points, `cst` and `csp` — calculated start time
and calculated stop time. That is all it does. If nothing reads those points,
the command runs every pass and changes nothing in the building.

That is the single most common way an SSTO installation does nothing, and it is
invisible: the command is there, it compiles, the calculated times are even
correct. Rule `W337` looks for it.

## SSTOCO — the zone's thermal personality

    SSTOCO(zone, season, intemp, outemp,
           ctemp, ccoef1, ccoef2, ccoef3, ccoef4,
           htemp, hcoef1, hcoef2, hcoef3, hcoef4)

`season` is read live: **2 = heating, 1 = cooling, 0 = disabled.** With
season 0, `SSTO` still runs but just hands `cst`/`csp` the latest allowed
times.

Four coefficients per season, and they mean different things:

| Coefficient | Meaning |
|---|---|
| `coef1` | **Pull-up / pull-down.** Hours to move the zone one degree with the equipment running, ignoring outside load |
| `coef2` | **Retention (drift).** Hours to lose one degree with equipment OFF and outside-air dampers OPEN |
| `coef3` | **Transfer.** Hours to move one degree with dampers CLOSED — envelope loss alone |
| `coef4` | **Auto-tune step.** Hours added to or taken off the adjustment each time the zone misses |

**Every one of them is in fractions of an hour, per degree F.** `0.1` is six
minutes. Entering minutes instead is the classic mistake and throws the
optimisation out by a factor of sixty.

**The two seasons use different reference conditions.** `coef2` and `coef3` are
defined against an outdoor temperature **10 °F above** the desired temperature
for cooling, but **25 °F below** it for heating. That asymmetry is why the
formulas below divide by 10 and 25 — they rescale the coefficient from its
reference delta to the actual one.

## SSTO — the schedule and the clamps

    SSTO(zone, mode, cst, csp,
         est, lst, ost,
         esp, lsp, osp,
         ast, asp)

`est`/`lst`/`ost` are the earliest, latest and occupancy **start** times;
`esp`/`lsp`/`osp` the same for **stop**. Earliest and latest are hard clamps —
whatever the arithmetic produces, SSTO will not schedule outside them.

`mode` follows the `TODMOD` scheme and the values may be summed: 1 normal,
2 extended, 4 shortened, 8 weekend, 16 holiday (only alongside `HOLIDA`).

`ast` and `asp` are the **self-tuning adjustments**, carried from day to day.
When the zone misses its target, SSTO moves them by `coef4`, so the
optimisation improves over a season. Pass `0` and the current adjustment is
shown whenever the command is displayed; pass a virtual LAO and an operator can
seed it.

## The arithmetic

With **d** = indoor - desired and **f** = outdoor - desired:

### Heating season

| Condition | Start time |
|---|---|
| indoor **below** desired | `OB + (d × hcoef1) - ((d × f × hcoef3) / 25) + AB` |
| indoor at or above desired | the latest start time |

| Condition | Stop time |
|---|---|
| indoor **below** desired | the latest stop time |
| indoor at or above desired | `OE - ((25 × hcoef2 × d) / f) + AE` |

### Cooling season

| Condition | Start time |
|---|---|
| indoor **below** desired | the latest start time |
| indoor at or above desired | `OB - (d × ccoef1) - (d × f × (ccoef3 / 10)) + AB` |

| Condition | Stop time |
|---|---|
| indoor **below** desired | `OE + ((10 × ccoef2 × d) / f) + AE` |
| indoor at or above desired | the latest stop time |

`OB`/`OE` are the occupancy begin and end times, `AB`/`AE` the current
adjustments.

**Reading these:** `d` is signed, and the "already at temperature" branch always
falls through to the latest allowed time — if the zone is where it needs to be,
the most economical thing is to start as late as the clamp permits. The `f`
term is the weather correction: the colder or hotter it is outside, the more the
envelope fights you, and the earlier the start moves.

**Note the division by `f`.** Both stop-time formulas divide by the outdoor
delta. As outside approaches the desired temperature, `f` approaches zero and
the coasting time grows without bound — which is why the latest-stop clamp
matters and should not be set loosely.

## Where SSTO belongs

Zones whose warm-up genuinely varies: exterior spaces, anything with real solar
gain, wind exposure, or auxiliary loads. An interior zone with a steady load
does not need it — a fixed schedule is simpler and does the same job.

Siemens' own example: a lobby that must be at 75 °F for an 08:00 open. From
72 °F it needs ten minutes, so SSTO starts at 07:50. From 69 °F it needs twenty,
so it starts at 07:40.

## Getting the coefficients right

They are measurements, not guesses. Take them from the building: time a
pull-up, time a drift with the equipment off. **Any example coefficients in
Siemens' documentation are illustrative and should not be entered without
calculating your own** — the manual says so explicitly.

This workbench does **not** simulate SSTO. The bench traces it and moves on.
Everything above is here so that a program containing `SSTO` can be read,
checked and explained; the numbers it would produce on your panel have to come
from your panel.
""",
    "see_also": ["guidelines", "rules", "bench"],
},

# --------------------------------------------------------------------------

{
    "id": "sources",
    "title": "Where the language rules come from",
    "category": "Reference",
    "summary": "Provenance, conflicts found, and what is still unknown.",
    "body": """
# Where the language rules come from

Four sources, in decreasing authority for a modern PXC.

1. **Desigo CC PPCL Editor Command Assist**, read from a live system. The only
   source for the `ADAPTM`, `ADAPTS`, `LSQ2` and `LSQDAT` signatures.
2. **Desigo CC engineering help.** The compiler's rulebook: operand and
   operator limits, the compiler error list, point referencing, Cross Trunk,
   program naming, the subroutine benefit table, and the Command Assist
   categories this workbench reuses.
3. **APOGEE PPCL User's Manual 125-1896 Rev. 5 (10/00).** The language itself.
4. **PXC.A Reference Manual A6V12954388.**

## Not obtained

**A6V10374898**, "Proprietary Program Control Language (PPCL) User Manual" --
the modern manual for the PXC.A line, which Siemens points to for "new
guidelines". It has a property-names appendix covering BACnet property
referencing that is not documented anywhere public. It needs a Siemens rep or
SID portal access.

## Conflicts found, and how each was settled

| Conflict | Settled as |
| --- | --- |
| Operand limit 13 (manual) or 16 (Desigo) | Firmware-dependent; the diagnostic says which it used |
| Arc-tangent `ATN` or `ARC` | **ATN.** Desigo's own command list says ATN while its precedence table says ARC |
| `DC` pattern: table or worked example | The table; the manual's example contradicts it |
| Program name characters | The explicit exclusion list; the help contradicts itself one sentence apart |
| `GOTO` to a missing line | Error on APOGEE, warning on older firmware |

## Still unknown

Each of these has a test that would settle it, and none has been run against a
live panel:

1. The exact `LOOP` PID form.
2. The line-evaluation rate on current PXC hardware.
3. Whether a bare `RELEAS` clears `@SMOKE`.
4. Which operand limit the PXC.A compiler actually enforces.
5. What A6V10374898 changed.
6. BACnet **property** referencing syntax. Object referencing is known:
   `BAC_<device>_<objtype>_<instance>`, for example `BAC_12345_AI_1`.
7. What values `LSQ2`'s execution parameter accepts.

## Other PPCL tooling

For syntax highlighting alone there are editor modes for Vim, Notepad++ and
Kate, and a Sublime Text package that adds line renumbering with GOTO
rewriting and DEFINE toggling. There is no other linter, simulator or block
editor for this language that I could find.
""",
    "see_also": ["rules", "guidelines", "safety"],
},

]

BY_ID = {p["id"]: p for p in PAGES}


def contents():
    """The table of contents, grouped by category and in declaration order."""
    groups = []
    seen = {}
    for page in PAGES:
        category = page["category"]
        if category not in seen:
            seen[category] = {"category": category, "pages": []}
            groups.append(seen[category])
        seen[category]["pages"].append(
            {"id": page["id"], "title": page["title"],
             "summary": page["summary"]}
        )
    return groups


def page(topic):
    """One page, with its cross references resolved to titles."""
    found = BY_ID.get(str(topic).strip().lower())
    if found is None:
        return None
    return {
        "id": found["id"],
        "title": found["title"],
        "category": found["category"],
        "summary": found["summary"],
        "body": found["body"].strip("\n"),
        "see_also": [
            {"id": ref, "title": BY_ID[ref]["title"]}
            for ref in found.get("see_also", [])
            if ref in BY_ID
        ],
    }


def search(query, limit=20):
    """Plain substring search over titles and bodies, title matches first."""
    needle = str(query or "").strip().lower()
    if not needle:
        return []
    titles, bodies = [], []
    for entry in PAGES:
        if needle in entry["title"].lower() or needle in entry["summary"].lower():
            titles.append(entry)
        elif needle in entry["body"].lower():
            bodies.append(entry)
    out = []
    for entry in (titles + bodies)[:limit]:
        out.append({
            "id": entry["id"],
            "title": entry["title"],
            "category": entry["category"],
            "summary": entry["summary"],
            "excerpt": _excerpt(entry["body"], needle),
        })
    return out


def _excerpt(body, needle, width=160):
    lowered = body.lower()
    at = lowered.find(needle)
    if at < 0:
        return body.strip().split("\n")[0][:width]
    start = max(0, at - width // 2)
    piece = body[start:start + width].replace("\n", " ").strip()
    return ("..." if start else "") + piece + "..."
