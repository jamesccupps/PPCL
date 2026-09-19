# PPCL Reference

A condensed, verified reference for Siemens **PPCL**, assembled while building
this toolkit.

## Sources, and which one applies to you

| Source | Covers | Notes |
|---|---|---|
| *APOGEE **Powers** Process Control Language (PPCL) User's Manual*, **125-1896 Rev. 5** (10/00) | The language itself | The primary source for everything below. Rev. 6 (05/06) is the later edition of the same document. |
| ***Proprietary** Program Control Language (PPCL) User Manual*, **A6V10374898** | The PXC.A generation (PXC4/5/7.A) | The modern manual. Note the rename: *Powers* → *Proprietary*. Restructured into Program Methodology / Control Option Comparisons / Command Syntax, with appendices for reserved words, **property names**, and sample programs. Siemens directs you to "modify PPCL using new guidelines" from this document when converting a BACnet PXCC/PXCM to a PXC.A. **Public, no login**, at <https://sid.siemens.com/r/A6V10374898> — mined 2026-09-18 for `GETVAL`/`SETVAL`, the property appendix, `[NodeName]PointName`, PXC.A capacities and the `DC` pattern table. |
| *PXC.A Reference Manual*, **A6V12954388** (04/2025) | PXC.A hardware and workflow | Publicly available. Points at A6V10374898 for PPCL. |
| *Desigo PXC.A Web Interface User Guide*, **A6V12893115** | The onboard editor PXC.A sites use instead of Desigo CC | Public, no login, at <https://sid.siemens.com/r/A6V12893115>. Documents the `# ` disable syntax, the SAVE ERROR bar, the `UNKNOWN (...)` marker, per-program cycle-time metrics, and the SET-deadband idiom. |
| **Desigo CC engineering help**, "APOGEE PPCL Editor" topics | The compiler you are actually checked by | PPCL Editor Overview, PPCL Guidelines, PPCL Programs, PPCL Program Plans, Editor Workspace, Glossary. This is where the current operand/operator limits are published. |

> **On the name.** It is "PPCL", not "APOGEE PPCL". Even the expansion has
> changed twice — *Powers* Process Control Language in 125-1896, *Proprietary*
> Program Control Language in A6V10374898 — and "APOGEE PPCL Editor" is the
> name Desigo CC gives its *editor module*, not the language. What has not
> changed is the language or its reference: the 2025 *PXC.A Reference Manual*
> sends a Desigo PXC4/5/7.A engineer to 125-1896, and the 2026 modernization
> guide converts PPCL **forward** onto PXC.A from MBC/MEC and BACnet PXC
> panels. "APOGEE" here is a firmware family — which is how `spec.Firmware`
> treats it — not a qualifier on the language.

### Insight or Desigo — which source has more?

Asked properly: for each of the 66 commands, does 125-1896 Rev. 5 document it,
does the Insight Program Editor help, and does Desigo CC's Command Assist list
it? Answered by matching all three against `spec.ALL`:

| 125-1896 | Insight help | Desigo list | Count | Which |
|---|---|---|---|---|
| yes | yes | yes | **57** | |
| yes | **no** | yes | 2 | `DISCOV`, `ENCOV` |
| **no** | yes | yes | 4 | `ADAPTM`, `ADAPTS`, `LSQ2`, `LSQDAT` |
| — | — | **no** | 3 | `GETVAL`, `SETVAL`, `LSTSQR` |

**Nothing was dropped.** Every command Insight documents, Desigo lists. The
worry that the newer generation lost material does not survive the check.

**No single source is complete, and the gaps run both ways.** The Insight
Program Editor's 736 pages are not a superset of the 2000 manual: `DISCOV` and
`ENCOV` have a full Chapter 4 entry with syntax and a worked example there, and
no mention anywhere in the help. Conversely `ADAPTM`, `ADAPTS`, `LSQ2` and
`LSQDAT` postdate the manual and are documented only in the help and in
Command Assist.

The three outside the list are outside for three different reasons.
`GETVAL`/`SETVAL` are PXC.A additions documented in A6V10374898, a different
manual for a different generation. `LSTSQR` is in no Siemens documentation at
all and was recovered from Siemens' own shipped application library.

**What Insight has that Desigo does not** is depth rather than coverage: a page
per command with a worked example, an enumerated reserved-word list (Desigo
ships none), the compiler error list, the decision-table planning method, the
`LFMSSL`/`LFMSSP` definitions, the status-field invariant that a report row
always carries `E` or `D`, the Time of Day ↔ PPCL relative-time-point
interface, and the inbound BACnet priority banding.

**What Desigo has that Insight does not** is the current compiler's own limits
— 16 operands and 32 operators where 125-1896 says 13 — and, through
A6V10374898, the PXC.A generation: `GETVAL`/`SETVAL`, the BACnet property
appendix, the ten removed statements, and the `# ` disable syntax.

Where sources disagree, that is called out rather than smoothed over — see
[Known discrepancies](#known-discrepancies). Everything the toolkit enforces
carries a citation to whichever source it came from.

> **What is genuinely newer in the PXC.A era.** The command set is essentially
> stable. What changed is around it: BACnet-native point and **property**
> referencing, colon-qualified names for FLN subpoints
> (`Dev201:DAY_CLG_STPT`) and cross-program locals (`PROGRAM:name`), program
> names that must be unique across the *whole system* rather than per panel,
> and a compiler in the Desigo CC PPCL Editor that pre-checks syntax before
> download. If you are writing for newer BACnet equipment, A6V10374898 is the
> document to work from.

---

## 1. Execution model

This is the whole language in five statements, and nearly every real defect
follows from one of them:

1. Every statement carries a unique line number, **1 to 32,767**.
2. Lines execute in **ascending line-number order**, not file order.
3. When the last line is reached, control **wraps to the first line**. A PPCL
   program is an infinite loop by construction.
4. `GOTO` and `GOSUB` are the only branches. A `GOSUB` body runs until `RETURN`.
5. For every firmware except APOGEE, **the last line must execute on every
   pass**.

Consequences worth internalising:

- A backward `GOTO` that forms an inner loop starves everything outside it.
- Time-based commands (`LOOP`, `SAMPLE`, `TOD`, `TODSET`, `TODMOD`, `WAIT`,
  `TIMAVG`, `SSTO`, `PDL*`) must be evaluated on every pass. Put one outside the
  main cycle and it runs once at load, then never again — with no error.
- After a power failure the panel resumes at the program's **first line**, which
  is why `ONPWRT` should be the first command and why the first line should be
  unconditional.
- On an APOGEE panel, all enabled programs share the line-evaluation budget
  **one line at a time, round-robin**. A 10-line program completes five full
  passes for every one pass of a 50-line program. Keep programs in a panel
  roughly the same length.
- Rough throughput: ~350 lines/second on a Version 3.0 controller board, ~500 on
  a 4.0. The number of attached FLN devices is the single largest influence.

### Line format

```
00120	IF (TIME.GT.8:00.AND.TIME.LT.17:00) THEN ON(SFAN) ELSE OFF(SFAN)
```

- Number the lines in multiples of ten so lines can be inserted later.
- `C` in the first position makes the line a comment.
- A trailing `&` continues a statement onto the next line.
- MMI entry limits: **66 characters** per line including the line number on
  APOGEE (198 across three continued lines); **72** on other firmware (144
  across two).
- Program names on APOGEE: 1–30 characters from `A-Z a-z 0-9 space . , - _ '`.

### Point names

- Unquoted names must be **≤ 6 characters** and use only `A-Z` and `0-9`.
- Anything longer or containing a `.`, `_` etc. must be in double quotes:
  `ON("BUILDING1.AHU01.SFAN")`.
- A name beginning with a digit must be prefixed with `@`: `@1FAN`.
- `LOCAL("FLAG")` declares a program-local virtual point, referenced as
  `"$FLAG"`. Other programs reach it as `PROGRAM:FLAG`.
- `DEFINE(AHU,"BUILDING1.AHU01.")` then `ON("%AHU%SFAN")`.
- `PARAMETER DELAY = 15` defines a compile-time constant and needs no line
  number.

---

## 2. Point priority — the thing that bites

Priority is the single most common source of "the schedule stopped working"
calls. Lowest to highest:

| Priority | Meaning |
|---|---|
| `@NONE` | PPCL priority — where ordinary PPCL commands act |
| `@PDL` | Peak Demand Limiting |
| `@EMER` | Emergency |
| `@SMOKE` | Smoke control |
| `@OPER` | Operator — highest |

**The rule:** a point is commanded only when the operation's priority is **at
least as high** as the point's current priority.

So this program looks correct and is broken:

```
00120	IF(TIME.GE.6:00.AND.TIME.LT.18:00) THEN ON(SFAN) ELSE OFF(SFAN)
00160	IF(MAT.LT.38.0) THEN OFF(@EMER,SFAN)
```

Once the mixed-air temperature dips below 38 °F, `SFAN` sits at `@EMER` forever.
Line 120 keeps issuing `ON(SFAN)` at `@NONE` every pass and it is silently
discarded. The fan never restarts, and nothing in the panel says why.

The fix is a matched release at the **same or higher** priority:

```
00150	IF(MAT.GT.45.0) THEN RELEAS(@EMER,SFAN)
```

Releasing at a *lower* priority than the point holds does nothing at all. A
point commanded from the keyboard needs `@OPER` to release.

Comparing a point against a priority tests the **priority**, not the value:

```
00200	IF (SFAN.EQ.@EMER) THEN ON(HORN)
```

Older firmware notes: physical firmware has only `NONE` and `EMER`, set with
`EMON`/`EMOFF`. Logical revisions 5.0–9.1 have four priorities and cannot test
them directly — you need flag points. Revision 9.2+ adds `SMOKE` and direct
`@priority` testing.

### The same five priorities, seen from BACnet

BACnet specifies sixteen command priorities; APOGEE specifies five. Crossing
between them is **not one mapping**, it is two, and they do not agree. Getting
this wrong is how a command lands at a priority nobody intended.

**Outbound — PPCL, or a panel application, commanding a BACnet point.** Each
APOGEE priority has a default BACnet slot:

| APOGEE priority | Default BACnet slot |
|---|---|
| `@OPER` | `BN08` — Manual Operator |
| `@SMOKE` | `BN10` |
| `@EMER` | `BN12` |
| `@PDL` | `BN14` |

**These are defaults, and defaults are a site setting.** The workstation has a
BACnet Command Priority Array dialog holding six of them — Operator, Smoke,
Emergency, **Scheduler**, PDL and **PPCL Programs** — and it is global data,
"replicated automatically to all field panels on the BLN". In Siemens' own
words: *"unless you override the default priority, PPCL Programs will command
BACnet points at the priority specified in this dialog."* So read the site's
dialog before trusting the table above, and note that `Scheduler` and
`PPCL Programs` are priority *sources* with their own slots even though
neither is a PPCL `@` indicator.

Siemens' own caution: **do not assign BACnet levels 1 or 2** — the Life Safety
levels — to any of these defaults.

**Inbound — a BACnet workstation commanding an APOGEE point** through the
Insight BACnet Server. Completely different, and banded rather than one-to-one:

| BACnet level | Becomes |
|---|---|
| 1 | `OPERATOR` |
| 2–4 | `SMOKE` |
| 5–7 | `EMERGENCY` |
| 8–16 | `PDL` |
| NULL | `NONE` |

**Read those two tables together and the trap is obvious.** `BN08` is named
"Manual Operator" and is `@OPER`'s default slot going out — and a command
*arriving* at BACnet 8 becomes `@PDL`, which is the second *lowest* APOGEE
priority. The slot's name describes neither end reliably.

Commanding an APOGEE point from an Insight workstation also **clears the
priority array** and removes pending commands: an APOGEE point has one
priority, not a sixteen-deep stack, so the array ends up with zero entries at
`NONE` and exactly one in every other case.

**Relinquish Default is not a priority.** It is what a BACnet point's present
value falls back to when *every* slot has been relinquished. `@NONE` is PPCL's
own priority level, not a synonym for it.

Also worth knowing before assuming five priorities exist everywhere: the four
above `@NONE` **do not apply to Staefa points**, where only two exist — Manual,
which maps to `@OPER`, and Automatic, which maps to `@NONE`. And command
priority does not affect Fire points at all in a fire-alarm network integrated
through the Life Safety Option.

---

## 3. Point types

The 11 types every panel recognises (Table 3-2). The address columns matter:
commanding two digital outputs of a bundled point at once can damage equipment.

| Type | Address 1 | Address 2 | Address 3 | Address 4 |
|---|---|---|---|---|
| `LAI` | AI | | | |
| `LAO` | AO | | | |
| `LDI` | DI | | | |
| `LDO` | DO | | | |
| `LFSSL` | DO (OFF/FAST) | DO (OFF/SLOW) | DI (PROOF) | |
| `LFSSP` | DO (OFF) | DO (FAST) | DO (SLOW) | DI (PROOF) |
| `LOOAL` | DO (ON/OFF) | DO (AUTO) | DI (PROOF) | |
| `LOOAP` | DO (ON) | DO (OFF) | DO (AUTO) | DI (PROOF) |
| `LPACI` | DI (COUNT) | | | |
| `L2SL` | DO (ON/OFF) | DI (PROOF) | | |
| `L2SP` | DO (ON) | DO (OFF) | DI (PROOF) | |

`LCTLR` (logical controller) is referenced by `DAY`/`NIGHT` but is not in
Table 3-2.

Which commands accept which:

- `AUTO`, `EMAUTO` — `LOOAL`, `LOOAP` only.
- `FAST`, `SLOW`, `EMFAST`, `EMSLOW` — `LFSSL`, `LFSSP` only. `OFF` means STOP
  for these.
- `ON` — `LDO`, `L2SL`, `L2SP`, `LOOAL`, `LOOAP`.
- `OFF` — those plus `LDI`, `LFSSL`, `LFSSP`.
- `DAY`, `NIGHT` — `LCTLR`. Also called OCC/UNOCC on equipment controllers.

---

## 4. Operators and precedence

**Relational:** `.EQ.` `.NE.` `.GT.` `.GE.` `.LT.` `.LE.`
**Logical:** `.AND.` `.NAND.` `.OR.` `.XOR.`
**Arithmetic:** `+` `-` `*` `/` `.ROOT.`
**Functions (single argument):** `ATN` `COM` `COS` `EXP` `LOG` `SIN` `SQRT`
`TAN` `ALMPRI` `TOTAL`. Trigonometric values are in **degrees**.

Precedence, 1 = highest (Table 2-6):

| Level | Operators |
|---|---|
| 1 | parentheses |
| 2 | the functions above |
| 3 | `.ROOT.` |
| 4 | `*` `/` |
| 5 | `+` `-` |
| 6 | `.EQ.` `.NE.` `.GT.` `.GE.` `.LT.` `.LE.` |
| 7 | `.AND.` `.NAND.` |
| 8 | `.OR.` `.XOR.` |

Equal precedence evaluates left to right; nested parentheses innermost first.
An `IF` may test at most **13 operands**.

> **Do not compare an analog point for exact equality.** Analog inputs carry
> precise values, so `IF (RMTEMP.EQ.80.0)` is usually false even when the
> reading looks like 80. Use a band.

### Resident points and status words

`TIME` (military), `CRTIME` (decimal hours), `DAY`, `DAYOFM`, `MONTH`,
`SECNDS`, `SECND1`–`SECND7`, `ALMCNT`, `ALMCT2`, `LINK`, `NODE0`–`NODE99`,
`$BATT`, `$PDL`.

Status: `ON` `OFF` `AUTO` `FAST` `SLOW` `ALARM` `ALMACK` `DAYMOD` `NGTMOD`
`FAILED` `HAND` `PRFON` `OK` `LOW` `DEAD`.

Locals: `$ARG1`–`$ARG15` (GOSUB arguments), `$LOC1`–`$LOC15`.

---

## 5. Commands by purpose

**Commanding** `ON` `OFF` `AUTO` `FAST` `SLOW` `SET` `STATE` `RELEAS`
`DAY` `NIGHT` — most take up to 16 points, or 15 with an `@priority`, because
the priority occupies a parameter slot. `SET` takes a value first: `SET(@EMER,
75.0, PT1)`.

**Emergency priority** `EMON` `EMOFF` `EMAUTO` `EMFAST` `EMSLOW` `EMSET`.

**Alarming** `ALARM` `NORMAL` `DISALM` `ENALM` `HLIMIT` `LLIMIT`. `HLIMIT` and
`LLIMIT` **reject integers** — write `84.0`, not `84`.

**Flow** `GOTO` `GOSUB` `RETURN` `ONPWRT` `ACT` `DEACT` `ENABLE` `DISABL`.
`ACT`/`ENABLE` and `DEACT`/`DISABL` are interchangeable, name up to 16
individual lines (no ranges), and affect only the device they live in.

**Timing** `SAMPLE` `WAIT` `TIMAVG`.

**Control** `LOOP` `TABLE` `DBSWIT` `MAX` `MIN` `INITTO`.

**Scheduling** `TOD` `TODSET` `TODMOD` `HOLIDA` `SSTO` `SSTOCO`.

**Demand** `PDL` `PDLDAT` `PDLDPG` `PDLMTR` `PDLSET`.

**Adaptive control** `ADAPTM` `ADAPTS` — closed-loop control algorithms,
1 to 14 points each. **Firmware 2.7 or above only.** Not documented in
125-1896 Rev. 5; signatures come from the PPCL Editor's Command Assist.

**Curve fitting** `LSQ2(execution,pt1,..,pt6,startline#,endline#)` computes a
two-variable quadratic least-squares fit, with the data supplied by a block of
`LSQDAT(pt1,pt2,pt3)` statements between `startline#` and `endline#`. LSQ2
fails if any data line is unresolved or failed, or if a result is too small to
be usable.

> `LSQ2`'s trailing two arguments are **PPCL line numbers**, not values. Any
> tool that renumbers a program has to rewrite them, or the curve fit silently
> reads the wrong block. The `execution` parameter in front is a value and must
> not be touched.

**Other** `LOCAL` `DEFINE` `OIP` `DISCOV` `ENCOV` `DPHONE` `EPHONE`.

`python -m ppcl.cli explain <COMMAND>` prints the full signature, parameter
kinds, limits and the manual's notes for any of them.

### The ones with sharp edges

**`LOOP(type,pv,cv,sp,pg,ig,dg,st,bias,lo,hi,0)`** — exactly 12 arguments; the
last is always 0. `type` is `0` direct-acting or `128` reverse-acting.

- `pg = (full range of controlled device / throttling range) × 1000`
- `ig = pg × 0.02` as a starting point, or `0` for proportional-only
- `bias` must lie between `lo` and `hi`; for proportional-only it is the
  midpoint of the output span
- Anti-windup is automatic once a limit is reached

**`TABLE(input,output,x1,y1,...,x7,y7)`** — x values must **ascend**. Below x1
the output holds y1; above the last x it holds the last y; linear interpolation
between. Tables cascade through virtual points.

**`TOD` / `TODMOD` / `HOLIDA`** — modes are 1 normal, 2 extended, 4 shortened,
8 weekend, 16 holiday. `TOD` and `TODSET` take a **sum** of those. `TODMOD`
takes exactly one of 1/2/4/8 per day and **never 16** — a `HOLIDA` date sets
mode 16 automatically. `HOLIDA` and `TODMOD` **must precede** every `TOD` and
`TODSET`.

> If holidays are defined in *both* PPCL `HOLIDA` and the panel's TOD calendar
> and the lists differ, equipment runs the holiday schedule on the union of both
> — more days than intended.

**`WAIT(time,pt1,pt2,mode)`** — mode is two digits: first is the trigger edge,
second is the resulting state. `11` = on trigger ON, wait, turn pt2 ON. `10`,
`01`, `00` likewise. Physical firmware omits `mode`. After a power failure or
`ENABLE` the trigger must **toggle** before anything happens.

**`SAMPLE(sec) <statement>`** — the trailing statement must not have its own
timing function. Executes immediately after a power failure, an `ENABLE`, or the
first pass following a database load.

**`GOSUB`** — arguments arrive as `$ARG1`–`$ARG15`. The last line of the body
must be `RETURN`. **No time-based commands inside a subroutine.** **A `GOSUB`
cannot appear inside an `IF/THEN/ELSE`.** A `GOTO` inside a subroutine must not
leave it.

**`DC`** — four digits, one per 15-minute segment, **read right to left**. Each
digit encodes three 5-minute slots: first slot = 1, second = 2, third = 4.
`DC` and `DCR` command at `@NONE`, so guard them with `IF/THEN/ELSE` or they
fight other PPCL on the same point.

**`PDLDAT`** — `minon` and `minoff` below 546 minutes; `maxoff` at most
`minoff + 546`. Each `PDLDAT` must be referenced by exactly one `PDL`.

---

## 6. Failure catalogue

What actually goes wrong, and the rule that catches it.

| # | Failure | Symptom | Rule |
|---|---|---|---|
| 1 | Priority latch with no matching `RELEAS` | Equipment never restarts; schedule appears dead | `W330` |
| 2 | `RELEAS` at a lower priority than the latch | Release silently does nothing | `W331` |
| 3 | Time-based command outside the main loop | Works once at load, then stops | `E205` |
| 4 | Duplicate line number | A line silently vanishes on load | `E102` |
| 5 | Time-based command inside a `GOSUB` | Timing drifts; manual forbids it | `E213` |
| 6 | `GOSUB` inside an `IF` | Undefined behaviour | `E214` |
| 7 | Inner backward `GOTO` | Starves every line outside the inner loop | `W203` |
| 8 | Subroutine with no `RETURN` | Execution runs on into the next block | `E210` |
| 9 | Two unconditional writers on one point | Result depends on line order | `W332` |
| 10 | Analog `.EQ.` comparison | Test is almost never true | `W334` |
| 11 | Integer where the manual writes a decimal | Older firmware rejects the line; current firmware appears not to | `W113` |
| 12 | `HOLIDA`/`TODMOD` after `TOD` | Schedule modes do not apply | `W306` |
| 13 | `TABLE` x values out of order | Interpolation undefined | `E301` |
| 14 | Unquoted long point name | Panel rejects the line | `E105` |
| 15 | `ONPWRT` not first | Wrong resume point after a power failure | `W220` |

---

## 6a. Desigo CC-era guidance

From the Desigo CC engineering help, which reflects current practice rather
than the 2000 manual.

**Compiler limits.** A statement may use **16 operands and 32 operators**.
Priority indicators count toward a total of **16 parameters** per statement.
Cross-panel point references are rejected outright.

The counting rule is unusual and worth quoting exactly: *"An expression adds
one operator for each arithmetic operator, relational operator, logical
operator, **point reference, and value constant**."* Point names and literals
count against the operator budget as well as the operand budget, so 32
operators is reached sooner than it looks.

**The compiler's own error list** (the messages that block a save):

| Message | What it means |
|---|---|
| backwards GOTO found | A GOTO refers to an earlier line — **except the last GOTO in the program**, which is permitted |
| GOTO references a non-existent statement | The target line does not exist. Note 125-1896 says the panel redirects silently; the compiler refuses |
| GOTO or GOSUB line # is not an integer | Target must be an integer 1–32,767 |
| out of range. 0 < line # < 32767 | Bad line number |
| more than 16 operands needed | Operand limit |
| more than 32 operators needed | Operator limit, counted as above |
| mismatched parenthesis | Unbalanced parentheses |
| leading parenthesis not found on IF / control statement | Missing opening parenthesis |
| error found in conditional portion of IF statement | Malformed condition |
| invalid use of statement in nested function | Improper nesting of a controller or IF statement |
| point in another panel | Cross-panel reference |
| not enough operands for operation | A value is missing from a calculation |
| numeric constant out of range for function | Constant outside the permitted range |
| invalid SAMPLE statement / syntax error in sample statement | Malformed SAMPLE |
| syntax error in OIP statement, parenthesis or quotes | Malformed OIP |
| unknown operand type encountered | Unrecognised name — check the spelling |

That "backwards GOTO" line is the single most useful sentence in the document:
it makes the main-loop trampoline a *sanctioned* structure and every other
backward branch a hard error. Rule `W203` implements exactly that split.

**Point referencing.** Three forms:

- **P2:** `SystemName` or `SystemName:SubpointName`, e.g.
  `"Device10APP2025:Day CLG STPT"`.
- **BACnet by name:** the Management View *name* (not description), e.g.
  `"FLOOR_1_DO1"`; qualify with enough of the path to be unique when it is not,
  e.g. `"Device10.Local_IO:FLOOR_1_DO1"`. FLN subpoints must be qualified by
  the device because subpoint names repeat across instances:
  `"Dev201:DAY_CLG_STPT"`.
- **BACnet third-party:** `BAC_<device instance>_<object type>_<object
  instance>`, e.g. `BAC_10_MO_1`.

Supported BACnet object types: `AI` `AO` `AV` `BI` `BO` `BV` `MI` `MO` `MV`.
Rule `W117` validates the `BAC_` form and the object-type field.

**Cross Trunk.** Commanding a point on another BLN is supported only from
`EMON EMOFF EMFAST EMSLOW EMAUTO EMSET ON OFF STATE AUTO FAST SLOW SET DAY
NIGHT MAX MIN RELEAS` and `=`. Siemens: *"Although PPCL may allow a Cross
Trunk reference in other PPCL commands, it is strongly recommended not to do
this."* Cross Trunk also issues **at most one command per second per point** —
if several arrive within a second, only the last is sent. Commanding Fire and
Security points is not allowed. Rule `W335` flags qualified references used in
unsupported commands.

**Program naming.** Maximum 30 characters. The topic says "uppercase and
lowercase letters, numbers, periods and spaces" and then, one sentence later,
"any ASCII character except the following: `? * [ ] { } |`" — the two
sentences disagree; the toolkit uses the explicit exclusion list. **Two programs cannot
share a name anywhere in the building system, even in different field panels.**
Siemens suggests naming from global to specific — location, campus, building,
floor or area, equipment type.

**Point referencing.** An unresolved point name marks the line with a red `U`
in the editor. FLN subpoints are addressed as `device:subpoint`
(`Dev201:DAY_CLG_STPT`). Non-unique names are qualified with enough of the
Management View path to disambiguate, separated by a colon.

**When a subroutine is worth it.** Siemens publishes the break-even directly:

| Subroutine body | Pays off at |
|---|---|
| Calls per pass | 1 line | 2 lines | 3 lines | 4+ lines |
|---|---|---|---|---|
| 1 call | No | No | No | No |
| 2 calls | No | No | **Even** | Yes |
| 3 calls | No | No | Yes | Yes |
| 4+ calls | No | **Yes** | Yes | Yes |

Body length excludes the RETURN. The row that is easy to get wrong: a
**one-line subroutine is never worth it**, at any call count. Rule `P708`
applies this table verbatim.

**Comment convention.** A comment is a line number, then `C`, then at least one
tab or space. Siemens recommends a numbering scheme for comments — for example
giving every comment an odd line number.

**Debugging on the panel.** The editor supports **trace bits** (`T`) and
enabling or disabling individual statements. Clearing trace bits from a
previous iteration lets you follow which statements actually execute. Disabled
code renders grey, executed code black, syntax blue, comments green.

**Testing expectations.** Test each mode and code section systematically, over
the full range of expected inputs *plus boundary conditions*, and include
failure scenarios such as a failed sensor or motor. That is precisely what
`ppcl bench --fault` exists to automate.

---

## 7. Known discrepancies

Places where the manual disagrees with itself, or with another manual. Most
were found by testing this toolkit's implementation against them.

**`DC` pattern encoding — settled 2026-09-18.** Table 4-1 of the 2000 manual
gives the mapping — first 5 minutes = 1, second = 2, third = 4, so `OFF/ON/ON`
= 6 and `OFF/OFF/ON` = 4 — while the worked example a few paragraphs later
labels `OFF/ON/ON` as **3** and `OFF/OFF/ON` as **1**. This toolkit followed
the table and said so.

**The table was right.** A6V10374898 Chapter 3, "DC (Duty cycle)", reprints the
same mapping as Table 3-1 and this time its worked example agrees with it:

> first 15 min ON/OFF/OFF (1), second OFF/OFF/OFF (0), third OFF/OFF/OFF (0),
> fourth ON/ON/ON (7) → `DC(HFAN,7001)`

Four digits, one per 15-minute segment, **entered in reverse order** — the
right-most digit is the *first* segment of the hour. `duty_cycle_pattern`
reproduces `7001` and all eight codes; both are pinned by tests.

One constraint the 2000 manual does not state: **`DC` commands at `NONE`
priority**, so Siemens directs that it be guarded with `IF/THEN/ELSE` to avoid
fighting other `NONE`-priority writers — `TOD` on the same point being the case
they call out by name.

**`RELEAS` default priority.** The `@priority` section states "when the
`@priority` is not used, the default priority is EMER", while the `@EMER`
section states that for logical 9.2+, CM and APOGEE firmware, "if the
`@priority` indicator is not specified, the field panel will release points from
PDL or EMER priority to NONE". These describe different things — the default
priority of a *command* versus the reach of a bare `RELEAS` — but the wording
invites confusion. This toolkit treats a bare `RELEAS` as clearing any priority
it is capable of clearing, and warns when a program relies on it.

**`EQUAL` and `LESS` — are they reserved words or a misread description
column? Settled 2026-09-18.** Neither has a documented *syntax*: no manual
anywhere shows a statement using either as an operator, and outside the
reserved-word lists they occur only inside comment prose in worked examples
("`C IF THE ROOM TEMP IS LESS THAN 80,`"). They are reserved **names**.

There is an obvious way to invent them by accident, and it had to be ruled
out. Desigo CC's PPCL glossary titles its topics `Equal To - EQ` and `Less
Than - LT` — description first, token second. Read that column as tokens and
you manufacture `EQUAL` and `LESS` out of nothing.

Ruled out. The Program Editor's shipped reserved-word page is a two-column
table of bare tokens with **no description column anywhere in it** — the
strings "Equal To" and "Less Than" do not appear on the page — and both words
occupy their own alphabetical cell: `EQUAL` between `EQ` and `EXP`, `LESS`
between `LE` and `LINK`, in a column that runs `…INITTO, LE, LESS, LINK,
LLIMIT, LOC1 through LOC15, LOG, LOOP, LT…`.

They then part company:

| | Program Editor list | 125-1896 Rev. 5 Ch. 5 | Desigo CC |
|---|---|---|---|
| `EQUAL` | bare cell, between `EQ` and `EXP` | bare cell, same position | no enumerated list |
| `LESS` | bare cell, between `LE` and `LINK` | **absent** — `LE` to `LINK` | no enumerated list |

So `EQUAL` is confirmed twice and `LESS` once, with nothing contradicting
either.

That they are *names* and not operators is corpus-checked, not argued. Parsing
Siemens' 42-program shipped library finds 10 occurrences of the two words and
**all 20 are on `Comment` statements** — zero in executable code. The reference
site's own programs contain neither word at all. This toolkit reserves both. Over-reserving costs a `W107` on a point
name nobody chose; under-reserving means never flagging a name a panel may
refuse. Comparing the two enumerated lists word for word in both directions
found `LESS` to be their **only** difference that is not a page-layout
artifact — every other entry of either list was already reserved here.

**Two point types exist that Table 3-2 does not list — 2026-09-18.**
125-1896 Rev. 5 titles its table "The 11 Point Types" and names only `LFSSL`
and `LFSSP` wherever a speed type appears, including all three status
indicators. The Insight Program Editor help names **four** — `LFSSL`, `LFSSP`,
`LFMSSL`, `LFMSSP` — on every one of `FAST`, `SLOW`, `EMFAST` and `EMSLOW`, and
on the `FAST`, `SLOW` and `OFF` status-indicator pages. The Point Details help
carries a definition for `LFMSSL`: *four-state control (Fast/Medium/Slow/Stop)
of three-speed latched motor starters that provide proof indication*. The
controller's own point-type enum puts the pair at 22 and 23, after the original
block — which is what a later addition looks like.

`LFSSL`/`LFSSP` are the **two**-speed types; `LFMSSL`/`LFMSSP` are the
**three**-speed ones. Both pairs are now in `POINT_TYPES` and in `SPEED_TYPES`.
Their address organisation is **inferred** from their two-speed siblings and
says so in `spec.py`, because no published table covers them.

One wrinkle worth knowing if you read the same pages: the Commanding help's
`LFSSL` page describes *three* latched outputs including a Medium — which is
`LFMSSL`'s organisation, not `LFSSL`'s. Table 3-2 is unambiguous that `LFSSL`
has `DO(OFF/FAST)`, `DO(OFF/SLOW)`, `DI(PROOF)`, and that is what this toolkit
carries.

**Five statements exist that nobody can document — 2026-09-18.** The
controller's own `PPCL_statement_type` enum has 71 members. Sixty map onto
commands in `spec.ALL`; two are explicitly unnamed (`WHOPUNKNOWN1` and
`WHOPUNKNOWN2`, values 61 and 62 — the vendor's table admitting its own
incompleteness); the rest are `IF` parts, assignment, comment and
declarations. **Five are left over:**

| Token | Value | What the name suggests |
|---|---|---|
| `ONERR` | 21 | an error handler — nothing says what it traps or where control goes |
| `ENTHAL` | 48 | enthalpy, though the manuals build economizer logic out of ordinary arithmetic and never with a statement of this name |
| `MMI` | 49 | the man-machine interface port, which the manuals discuss constantly but never as a statement |
| `RELTCU` | 60 | "release TCU" — the Terminal Control Unit generation that predates the documentation on hand |
| `DIM` | 68 | a dimension or array declaration, which would be unlike anything else in the language |

Every manual here was searched: the 736-page Insight Program Editor help, the
Desigo CC engineering and operating help, 125-1896, A6V10374898, A6V12954388,
A6V10324350, A6V13998441. **None mentions any of the five.** Nor does any
program: zero occurrences across Siemens' shipped application library, the
reference site's programs, and 2,644 lines recovered from panels over the wire.

So the name is real and everything else is not established. They are kept out
of `spec.ALL` — a name whose arguments nobody can state must not be offered by
completion or emitted by the generator — and `E110` is taught not to call one
"not a PPCL command", which would be confidently wrong about the vendor's own
firmware. `W121` says the true thing: the line was not checked.

This is the second time absence from a vendor enum has proved to mean nothing.
`OIP` is used constantly in real programs and is *also* missing from that same
71-entry table. An enum is evidence of what exists, never of what does not.

**`NODE0` or `NODE1` — where the node range starts. Settled 2026-09-18.**
The Program Editor's reserved-word page prints `NODE1 through NODE99`. The
dedicated node-points page **in the same book** states the range in prose —
"acceptable node numbers for the NODE resident point range from 0 through 99",
and again "between 0 and 99" — and carries `NODE0` in its own title. So do
that book's glossary, the PPCL Debugger help, and Desigo CC's engineering
help.

Four sources say zero, one says one, and the one is contradicted by its own
book. Node 0 is a real drop address on an RS-485 BLN besides. `RESERVED_WORDS`
generates `NODE0`–`NODE99`, one hundred names, and a test says why.

This is the second defect found in that single table — it is also the only
place `LESS` appears. A page can be the sole source for one fact and wrong
about another; neither observation settles the other.

 125-1896 Rev. 5 states an `IF` may test a maximum
of **13 operands**. The Desigo CC PPCL Editor documentation states **16
operands and 32 operators** per statement. Both are published; neither
retracts the other. Since the Desigo editor is the compiler a modern PXC
program is actually checked by, the linter applies 16 to APOGEE firmware and
the conservative 13 to the older families. Rule `E314` says which figure it
used and why. If you are near either limit, split the statement — a 14-operand
`IF` is unreadable regardless of which number is right.

*Independently adopted.* A separate P2 wire-capture project had recorded only
the 16/32 figures, took the 13 from here, and applied it to the older families
— which is the generation its own corpus came from.

---

## 8. Verification status

Following the discipline of tagging each claim by how it was established:

| Claim | Status |
|---|---|
| Command signatures, argument limits, parameter kinds | **Manual-verified** — Chapter 4, transcribed into `ppcl/spec.py` |
| Point types and their addresses | **Manual-verified** — Table 3-2 |
| Priority hierarchy and arbitration rule | **Manual-verified** — Table 3-1, Chapter 3 |
| Operator precedence | **Manual-verified** — Table 2-6 |
| Reserved word list | **Manual-verified twice, then diffed.** 125-1896 Rev. 5 Chapter 5 and the Program Editor's shipped list, compared entry by entry in both directions. They differ by exactly one word — `LESS`, §7 — and every other entry of either list was already reserved here |
| Execution model, wrap, line limits | **Manual-verified** — Chapter 2 |
| Parser correctness | **Empirically tested at scale.** Parses 42 programs exported from a live Desigo CC with zero failures, and 7,858 of 7,863 lines of Siemens' own 42-program application library. The five remaining are genuine syntax errors in that library as shipped -- a stray parenthesis, three missing commas, a statement fragment. Three parser bugs were found and fixed this way and could not have been found any other way. Separately, an independent APOGEE P2 wire-capture corpus ran this parser over **2,644 PPCL lines recovered from programs running on panels at a working site: 2,644 parsed, none failed**, including 24 lines carrying a PPCL keyword as a dotted fragment of a point name. That evidence is not reproducible from this repository — see the note below the table |
| `TABLE`, `DBSWIT`, `MIN`/`MAX`, `TOD`, `WAIT`, `SAMPLE` simulation | **Manual-verified**, behaviour fully specified |
| Priority arbitration in the simulator | **Manual-verified** against the Chapter 3 rule |
| `LOOP` output values | **Approximated, NOT verified.** Siemens does not publish the internal PID form. Timing, inputs and outputs are exact; the computed value is indicative only. The gain scaling follows the manual's own `pg = (output span / throttling range) x 1000`, so `pg/1000` is the gain in percent per degree |
| Equipment models in `ppcl/plant` | **First-order lumped-parameter approximations.** Standard IP constants (1.08 BTU/h per CFM-F, 500 BTU/h per GPM-F). Good enough to exercise control logic; not a load calculation, not an energy model, no psychrometrics or dehumidification |
| `DC` pattern encoding | **Verified twice.** Table 4-1 (2000) and Table 3-1 (A6V10374898) agree; the newer manual's worked example `DC(HFAN,7001)` is reproduced exactly by `duty_cycle_pattern` and pinned by a test |
| BACnet property referencing | **Manual-verified** — A6V10374898 Appendix B, 99 properties, reached with `GETVAL`/`SETVAL` as `@ShortName` or a numeric id |
| `SSTO`, `PDL`, `OIP`, `DC`/`DCR` execution | **Not modelled** — traced as no-ops |
| PXC.A statement availability | **Manual-verified** — A6V10374898, "Obsolete PPCL Statements Removed from the Language". Ten statements, each with Siemens' stated reason |
| Panel error codes | **Manual-verified** — A6V10324350 Appendix C. R-codes are compiler refusals, E-codes are runtime failures on a line that loaded |
| Per-line state (`report.py`) | **Confirmed from the panel's own record.** The five flags the `PPCL DISPLAY REPORT` state column carries — enabled, traced, unresolved, failed, looped — are exactly the five booleans of the controller's `PPCL_data` structure as read off the wire by an independent project. The model is complete, not a guess from a column of letters |
| Five statement tokens the firmware names | **Known to exist, unknown in every other way.** `ONERR`, `ENTHAL`, `MMI`, `RELTCU`, `DIM` are members 21, 48, 49, 60 and 68 of the controller's `PPCL_statement_type` enum. No manual on hand documents any of them and no program in any corpus uses one. They are deliberately **not** in `spec.ALL`; `W121` reports a line using one as unchecked rather than wrong. See §7 |
| `LSTSQR` | **Inferred from code, not documented anywhere.** Recovered from Siemens' shipped chiller programs; argument order deduced from what those programs compute from the results. Argument count is deliberately NOT enforced |

**Program planning method.** Siemens' recommended process, which is also a
good specification for a sequence-authoring tool: (1) read the sequence of
operation; (2) **determine the modes of operation** — day, night, safety
shutdown, warm-up; (3) identify controls, looking for phrases like "must
control", "will perform", "cycle", "calculate"; (4) organise with a
**decision table**, a flowchart, or pseudocode.

The decision table is equipment against mode:

| Equipment | Shutdown | Day | Smoke | Warm-up |
|---|---|---|---|---|
| Supply Fan | Off | On | On | On |
| Return Fan | Off | On | On | On |
| Chilled Water Valve | Closed | Modulate | Modulate | Closed |
| Mixing Dampers | Closed | Modulate | Modulate | Closed |

### Still open

Questions this toolkit does not answer, each with the test that would settle it:

1. **Exact `LOOP` PID form.** *Test:* drive a known `pv` step on an isolated
   PXC with logged `cv`, and fit against candidate forms.
2. **Line-evaluation rate on current PXC hardware.** The manual's 350/500
   lines/sec figures predate the modular PXC line. *Test:* a counter program on
   an isolated panel, timed over a known interval, with FLN device count varied.
   Wire capture cannot substitute: a P2 timing census puts a panel's whole-
   program *upload* at about 10.5 ms, which bounds retrieval, not execution.
   **PXC.A already answers this and nobody has read it off.** Its Web
   Interface reports per-program cycle time — average over the last ten
   cycles, highest, lowest, in milliseconds — and those metrics map to virtual
   points and trend. No lab panel required; map them on a panel in service.
3. **Whether a bare `RELEAS` clears `@SMOKE`.** The manual is silent. *Test:*
   command to `@SMOKE`, issue a bare `RELEAS`, read the resulting priority.
4. ~~**`DC` example vs Table 4-1.**~~ **Closed 2026-09-18** from
   A6V10374898 Table 3-1, whose worked example agrees with the table. No panel
   test needed.
5. **Which operand limit the PXC.A compiler actually enforces**, 13 or 16.
   *Test:* compile a 15-operand `IF` in the Desigo CC PPCL Editor against a
   PXC.A and see whether it is rejected.
6. **What A6V10374898 changed.** The PXC.A reference manual says to use "new
   guidelines" but does not say what they are. *Test:* obtain A6V10374898 and
   diff its Program Methodology chapter against 125-1896 Rev. 5.
7. **BACnet property referencing syntax in PPCL on PXC.A.** A6V10374898 has a
   property-names appendix. The Desigo help documents object *referencing*
   (`BAC_10_MO_1`) but not how a *property* of an object is named. *Test:*
   read the appendix, then confirm against a PXC.A with a known BACnet object.
   The appendix has since been read — 99 properties, in the table above — so
   what is left is hardware confirmation, and nothing available here can give
   it: no PXC.A appears in any corpus examined, wire capture included.
8. ~~Signatures for ADAPTM, ADAPTS, LSQ2 and LSQDAT~~ — **CLOSED.**
   `ADAPTM(pt1,...,pt14)`, `ADAPTS(pt1,...,pt14)` (both firmware 2.7+),
   `LSQ2(execution,pt1,..,pt6,startline#,endline#)`, `LSQDAT(pt1,pt2,pt3)`.
   Both remaining sub-questions are answered, and by a **second independent
   source**: the Insight Program Editor carries a full page, a Statement
   Arguments page and a worked example for each of the four. `LSQ2`'s
   `execution` is *"the execution time in minutes"*, and `ADAPTM`/`ADAPTS`
   have a documented parameter-by-parameter procedure — sample time at most a
   third of the smallest time constant, each time constant at least three
   times the sample time, `Kc = 3`, a unique error point per statement, and
   the four preconditions a loop must meet (controllable, open-loop stable,
   consistently direct or reverse acting, modulating).
9. ~~ARC or ATN for arc-tangent~~ — **RESOLVED. It is `ATN`.** The PPCL
   Editor's Command Assist lists ATN and describes it as calculating the
   arc-tangent in degrees. The `ARC(value1)` in the Desigo precedence table is
   a documentation error. Rule `E118` rejects ARC and says so. *Independently
   confirmed* by a P2 wire-capture project that reached the same table from the
   other direction and found the same `ARC(value1)` in it.
10. **Does a panel absorb a dotted-operator segment into an unquoted point
    name?** Point names are dotted, and PPCL keywords turn up as segments of
    them — `AHU1.MIN.SP` is an ordinary name, because `.MIN.` is not an
    operator. But `AHU1.ROOT.SP` written bare is genuinely ambiguous, and this
    lexer reads it as `AHU1 .ROOT. SP`. If a panel absorbs it into the name
    instead, a program using such a name analyses wrongly here. *Test:* define
    a point whose name carries a dotted-operator segment, reference it without
    quotes in a loaded program, and see whether the line resolves.

    *Measured 2026-09-18.* A P2 wire corpus enumerated **3,025 distinct point
    names** across 960,469 occurrences: **none** contains a dotted-operator
    segment. The reserved words that do appear as dotted name segments —
    `MIN`, `ALARM`, `OFF` — are not dotted operators, so a tokenizer splitting
    on them leaves those names alone. One name carries `ROOT`, and `.ROOT.`
    *is* an operator; it survives only because `ROOT` is that name's **last**
    segment, so the trailing dot never appears. A name one component longer
    would be the ambiguous case.

    That is a negative from one site, not a proof, and the margin is a single
    naming decision. So it stays a documented hazard with a stated fix —
    **quote any name with a dotted-operator segment** — rather than a lint
    rule, which would fire zero times on every corpus anyone has.

**Nothing here has been validated against a live panel** — no statement's
*behaviour* has been observed executing. Everything is transcribed from the
manual or tested against real program text.

The one result that comes closer is the wire-capture line in the table above.
A separate project reading APOGEE P2 traffic recovered 2,644 lines of PPCL
from programs running on panels and ran this parser over all of them without a
failure. That is the strongest statement either project can make about the
other — a manual-derived parser handling code that panels are executing — and
neither could make it alone, because the two have no source in common: this
toolkit is built from published Siemens documentation, that corpus from bytes
on the wire.

It is recorded here at a lower tier than everything else in the table, for one
reason: **a reader of this repository cannot reproduce it.** The programs are
a working site's and stay there. Treat it as a claim with a named method and
an unpublishable input, not as a citation.

---

## 9. Sources

- *APOGEE Powers Process Control Language (PPCL) User's Manual*, 125-1896,
  Rev. 5 (10/00), Siemens Building Technologies — the primary source for
  everything above.
- `mitchpaulus/vim-siemens-ppcl` — a plain-text transcription of the same
  manual, used for cross-checking the command reference.
- `delphian/ppcl-library` — four real-world PPCL programs (MIT licensed), used
  as parser test fixtures. They contain genuine duplicate line numbers, which is
  how rule `E102` earned its place.
- *PXC.A Reference Manual*, A6V12954388_en--_e, 2025-04-10 — the pointer to
  A6V10374898 and the PXC.A engineering workflow.
- Desigo CC engineering help, "APOGEE PPCL Editor" topic tree — the current
  compiler limits, program-naming rules, subroutine break-even table and
  debugging features.
- The Desigo CC **PPCL Editor Command Assist**, read directly from a live
  installation — the only source found for the ADAPTM, ADAPTS, LSQ2 and
  LSQDAT signatures, and the authority that settles ATN over ARC.
