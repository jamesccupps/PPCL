# PPCL research log

What has been searched, what was found, and what is still missing. Append to
this rather than re-deriving it — the point of the file is that a fresh session
does not repeat a sweep that has already been done.

Each entry records **where it came from**, so a claim can be traced or argued.

---

## 2026-09-18 — the PXC.A PPCL User Guide, found and mined

### The headline: A6V10374898 is public after all

We had this listed for months as "the highest-value missing document — needs a
Siemens rep or SID portal access." **That was wrong.** It is on Siemens' own
Smart Information Delivery site at internet access level, no login:

> https://sid.siemens.com/r/A6V10374898

Title: **PXC.A PPCL User Guide**. Document No. 125-1896 (the same number as the
2000 APOGEE manual — Siemens reused it). Covers PXC5.E24.A, PXC7.E400.A and
PXC4.E16.A. HTML, hyperlinked, three chapters plus three appendices.

Chapter and appendix URLs (stable as of this date):

| Section | URL |
|---|---|
| Ch.1 Program Methodology | `…/21253010315_23094716171__en-US_21253085451` |
| Ch.2 Control Option Comparisons | `…/21253010315_23094716171__en-US_21253312395` |
| Ch.3 Command Syntax | `…/21253010315_23094716171__en-US_21253013515` |
| App.A Reserved Word List | `…/21253010315_23094716171__en-US_18722097931` |
| App.B Property Short Names | `…/21253010315_23094716171__en-US_22684468619` |
| App.C Program for MEC100K | `…/21253010315_23094716171__en-US_23020095115` |

All prefixed `https://sid.siemens.com/r/A6V10374898/`.

Also downloaded: **A6V12954388**, the PXC.A Reference Manual, 112-page PDF,
directly from Siemens' public support cache:
`https://cache.industry.siemens.com/dl/files/578/109963578/att_1276831/v2/A6V12954388.pdf`

---

### Open question 7 — BACnet property referencing — **CLOSED**

Properties are referenced by an **`@` short name** or by a **numeric BACnet
property identifier**, and are read and written with two commands the 2000
manual does not have.

> "All short names in this list must be preceded by @ (for example, @RefVal)."
> — A6V10374898 Appendix B

Appendix B carries ~100 properties. The ones most likely to matter in a
sequence:

| No. | Short name | Meaning |
|---|---|---|
| 85 | `@PrVal` | Present value |
| 86 | `@Prio` | Priority |
| 87 | `@PrioArr` | Priority array |
| 81 | `@OoServe` | Out of service |
| 103 / 5053 | `@Rlb` | Reliability |
| 36 | `@EvtSta` | Event state |
| 40 | `@FbVal` | Feedback value |
| 45 / 59 | `@HiLm` / `@LoLm` | High / low limit |
| 104 | `@DefCmd` | Default command |
| 117 | `@Un` | Units |
| 5093 | `@PrPrio` | Present priority |
| 5127 | `@TotlizdVal` | Totalized value |
| 66 / 67 | `@TiOffMin` / `@TiOnMin` | Minimum off / on time |
| 5165 | `@MaxPPCLChs` | Max. PPCL line characters |
| 5167 | `@PPCLPgmPrio` | PPCL program priority |

The last two are worth noting: the panel exposes its *own* PPCL line-character
limit and program priority as readable properties. That is a way to settle open
question 5 (which operand/character limit the PXC.A actually enforces) from a
live panel without guessing.

**Conflict, unresolved.** Appendix B says short names "must be preceded by @",
but the GETVAL examples in Chapter 3 show them bare (`Rlb`, `PrioArr`). Same
document, two spellings — the same shape as the ATN/ARC conflict. Until a live
panel settles it, accept both and prefer `@` on output.

---

### Two commands missing from `spec.py`

`spec.py` had 63 commands; so does A6V10374898 Chapter 3. But the sets differ:

- **In the new manual, not in spec: `GETVAL`, `SETVAL`.**
- **In spec, not in the manual: `LSQ2`, `LSQDAT`** — these came from Desigo CC's
  Command Assist (the screenshots) and are a Desigo-era addition.

Both sets are real, from different authorities. The union is 65 commands.

#### GETVAL — read a property

Reads a property from a source object into a target object.

```
GETVAL(targObjRef, srcObjRef, srcPropSpec [, srcIndex])
```

| Parameter | Meaning |
|---|---|
| `targObjRef` | Object receiving the value, e.g. `BAC_7_AO_11`, `KwMeter` |
| `srcObjRef` | Object to read from |
| `srcPropSpec` | Numeric BACnetPropertyIdentifier (standard or proprietary range) **or** an abbreviated property name |
| `srcIndex` | Optional array index, for an array-valued property |

Examples as printed:

```
10 GetVal(TheRoomTemp, [Room101]RoomTemp), Rlb
10 GetVal(TheLockVal, [Room101]RoomTemp, PrioArr, 5
```

Both examples are malformed as printed — the first has a stray `)` before
`Rlb`, the second never closes its parenthesis. Treat the parameter table as
authoritative and the examples as typos.

#### SETVAL — write a property

```
SETVAL(srcValSpec, targPropSpec, targObjRef1 [, … targObjRef14])
```

| Parameter | Meaning |
|---|---|
| `srcValSpec` | A number, a reserved word for an enumerated value, or an object reference |
| `targPropSpec` | Numeric property identifier or abbreviated name |
| `targObjRefN` | **1 to 14** target objects |

Examples as printed:

```
10 SETVAL (1, 81, "Room101:ROOM TEMP 4", "Room102:ROOM TEMP 4", "Room103:ROOM TEMP4")
10 SETVAL (50.0, 104, "BAC_12345_AO_67")
```

The first writes `1` to property 81 (`OoServe`, out of service) on three
points; the second writes `50.0` to property 104 (`DefCmd`).

**Restriction, stated for both:** "Only properties with numeric data types or
arrays of numeric data types can be read or written in PPCL programs." The PPCL
engine converts between Real, Unsigned and Integer.

**Safety note for this project.** `SETVAL` can write `OoServe` and `PrioArr` —
it can take a point out of service and manipulate the BACnet priority array
directly. Anything the workbench generates that touches those needs at least
the same care as an `@EMER` command, arguably more, because a point left out of
service does not look commanded at all.

---

### A third point-reference form: `[NodeName]PointName`

Chapter 1 "BACnet Point Naming (Encoded names)" documents three forms:

| Form | When |
|---|---|
| `BAC_<device>_<type>_<instance>` e.g. `BAC_15_AI_10` | Third-party or external network objects |
| `[NodeName]PointSystemName` e.g. `[AdminBldg1]ReturnWaterTemp` | PXC.A devices on the same ALN |
| Bare BACnet object name | Local objects, or same-ALN references |

**The bracket form is new to us.** `spec.BACNET_REFERENCE` only knows the
`BAC_…` regex, and the lexer has never been tested against `[Node]Point`. This
is a concrete gap — see "still to do" below.

---

### PXC.A program capacity

From Chapter 1, "PPCL Rules for PXC.A Controllers":

| Controller | Programs | Sizing |
|---|---|---|
| **PXC7.A** | up to 50 | one very large (<10,000 lines), or one large (<5,000), or a few medium (<1,000), or several small (<400) |
| **PXC4.A** | up to 10 | one large (<5,000), or a few medium (<1,000), or several small (<400) |

Relevant to the "panel view" roadmap item: the round-robin budget is shared
across up to 50 programs on a PXC7.

---

### Appendix C — a complete Siemens-authored program

Appendix C is **MEC100K**, a real, published, ~60-line program that converts a
100K thermistor voltage to a temperature through ten chained `TABLE`
statements. It is the only full program we have that Siemens itself wrote, which
makes it an idiom reference rather than just an example.

**It is copyrighted** — the header reads "OWNED AND MAINTAINED BY SIEMENS
BUILDING TECHNOLOGIES INC… ALL RIGHTS RESERVED. MODIFICATIONS WITHOUT EXPRESS,
WRITTEN CONSENT… MAY VOID WARRANTY." **Do not vendor it into this repo or any
public artifact.** It was used locally for interoperability testing only. Fetch
it from Appendix C when needed.

Patterns it demonstrates, all of which our tooling now handles:

- `SAMPLE(1) GOTO 1040` — SAMPLE gating a branch, once per second
- a subroutine taking `$ARG1`/`$ARG2`, with `TABLE` writing its result into `$ARG2`
- exactly one backward `GOTO` (`02200 GOTO 1020`) and it is the last executable
  line — our `W203` split holds on Siemens' own code
- `TABLE` used at its full 7 breakpoints
- `IF … THEN TABLE(…) ELSE GOTO …` — a command in THEN and a branch in ELSE
- lines of 160–200 characters, far over the 66-char MMI limit, in shipped code

#### What testing against it found — two real linter bugs

Running it through `ppcl lint` gave **1 error and 22 warnings on Siemens'
own reference program**. Both of the big contributors were our bugs:

| Was | Now | Why |
|---|---|---|
| `W107` fired 10× — "assigning to $ARG2, which is on the reserved word list" | suppressed for `$ARGn`/`$LOCn` | Those are on the reserved list so nothing *else* may be named that. Writing to them is the entire subroutine-argument mechanism. The rule conflated "do not name a point this" with "do not write to this". |
| `E212` fired as **ERROR** — "RETURN not inside any subroutine" | `WARNING` when the program contains no `GOSUB` at all | MEC100K ships as a template: the subroutine is there and a comment says where to add your `GOSUB`. It loads and runs. ERROR means "the compiler will reject this", which is false. A program that *does* use `GOSUB` and still strands a `RETURN` keeps ERROR. |

Result: **0 errors, 13 warnings, every one of them true** (the long lines, the
`GOTO` into a comment line, and the genuinely dormant subroutine). Two
regression tests added in `tests/test_rules.py`.

This is the first time the linter has been run against code Siemens wrote, and
it is the strongest validation the rule set has had.

---

### Corpus sweep — what exists publicly

Searched GitHub, HVAC-Talk, control.com, Package Control, archive/manual
mirrors.

| Source | Result |
|---|---|
| `delphian/ppcl-library` | 4 programs — **already our `samples/`**. Repo is exhausted. |
| `mitchpaulus/*` (vim, Notepad++, Kate) | Syntax highlighting only, no programs |
| Sublime "PPCL Language Syntax and Editor" | Tooling, no programs |
| HVAC-Talk threads 299252, 1922001, 67999 | Contain real posted snippets, but the site now sits behind a **Tollbit paywall** — `HTTP 402 Payment Required` on fetch. Not retrievable by tool. |
| A6V10374898 Appendix C | **1 new complete program** (MEC100K), copyrighted |

**Honest conclusion: the public PPCL corpus is essentially five programs.**
There is no large body of PPCL to mine, and there never will be — it is a
proprietary language whose code lives inside panels at individual sites.

The consequence for this project is the one already in HANDOFF §7: **your own
site's own programs are the corpus.** Nothing found in this sweep changes that, and
nothing found here substitutes for it.

---

### Still to do

1. **Fold `GETVAL`/`SETVAL` into `spec.py`**, with the `@propname` and numeric
   property forms, the 1–14 target limit, and the numeric-types-only
   restriction. Add the Appendix B property table as data.
2. **Teach the lexer/parser `[NodeName]PointName`.** Untested today; likely
   fails. Needs a rule for an unknown node name once a point database is loaded.
3. **A rule for `SETVAL` writing `@OoServe` or `@PrioArr`** — the same class of
   finding as `W330`, and arguably more dangerous.
4. **Mine A6V12954388** (downloaded PDF, 112 pages) for anything the HTML guide
   omits.
5. **Chapter 1 sections not yet read**: max characters per program line, max
   characters per comment line, design guidelines, special functions, object
   status indicators. Each has a URL above.
6. **Chapter 2** (duty cycling, economizer, PDL, SSTO, TOD comparisons) — likely
   the best source for *idiomatic* sequences, which is what the block catalog
   and the sequence templates should be modelled on.

### Open questions now closed

- ~~7. BACnet property referencing syntax~~ — **closed**, `@ShortName` or number,
  via `GETVAL`/`SETVAL`. See above.
- ~~6. What A6V10374898 changed~~ — **partially closed**: it adds `GETVAL`,
  `SETVAL`, the property appendix, the `[Node]Point` reference form and PXC.A
  program capacities; it drops `LSQ2`/`LSQDAT`.

---

## 2026-09-18 (continued) — findings folded in, and Chapter 2 mined

### What was added to the toolkit

| Change | Where |
|---|---|
| `GETVAL`, `SETVAL` command signatures | `spec.py` — 63 → **65 commands** |
| `BACNET_PROPERTIES`, 99 entries from Appendix B | `spec.py` |
| `PROPERTY_NUMBERS`, `property_number()`, `property_name()` | `spec.py` — resolves `@PrVal`, `PrVal`, `85` and `"85"` alike |
| `DANGEROUS_PROPERTIES` | `spec.py` |
| Category **"Property Access"** | `spec.py` — kept *separate* from the seven Siemens Command Assist categories, which stay a verbatim transcription. `SIEMENS_COMMAND_CATEGORIES` records which is which |
| `[NodeName]PointName` lexing | `lexer.py` — new `_NODEREF_RE` branch |
| Property specs no longer counted as point uses | `analyzer.py` — `@PrVal` was landing in the point inventory and would have been marked unresolved `U` against a point database |
| Rule **W336** — SETVAL writes a behaviour-changing property | `rules/semantics.py` |

`[Node]Point` genuinely did not lex before this — `LexError: unexpected character '['`. Any PXC.A program using same-ALN references would have failed to parse outright.

### Open question 4 — the DC pattern — **CLOSED, and we had it right**

The 2000 manual's Table 4-1 and the worked example beside it disagreed. We
implemented the table and recorded the gamble. A6V10374898 Ch.3 "DC (Duty
cycle)" settles it — its Table 3-1 matches Table 4-1 exactly, and this time the
worked example agrees with the table:

| First 5 min | Second 5 min | Third 5 min | Code |
|---|---|---|---|
| OFF | OFF | OFF | 0 |
| ON | OFF | OFF | 1 |
| OFF | ON | OFF | 2 |
| ON | ON | OFF | 3 |
| OFF | OFF | ON | 4 |
| ON | OFF | ON | 5 |
| OFF | ON | ON | 6 |
| ON | ON | ON | 7 |

Four digits, one per 15-minute segment, **entered in reverse order** — the
right-most digit is the *first* segment of the hour.

The manual's own example: first 15 min ON/OFF/OFF (1), second OFF/OFF/OFF (0),
third OFF/OFF/OFF (0), fourth ON/ON/ON (7) → `DC(HFAN,7001)`.

`generator.duty_cycle_pattern` reproduces `7001` exactly and matches all eight
Table 3-1 codes. Both are now pinned by tests in `tests/test_tools.py`.

### New constraint: DC runs at NONE priority

> "The DC command has a priority of NONE. Therefore, the PPCL program must be
> structured with IF/THEN/ELSE commands to prevent conflicts between DC and
> other commands with the same priority."

And separately, under "Where to Use TOD":

> "If objects used for TOD are also commanded by another application, such as
> Duty Cycling (DC), one program may interfere with the operation of the
> other if both functions are trying to control a object during the same time
> period."

Our `W332` (same point commanded from several places) already covers the shape.
Siemens naming the TOD/DC pair explicitly is good corroboration, and a targeted
rule for *unguarded* `DC` alongside another NONE-priority writer is a
reasonable future addition.

Also from the same page: `DC` takes **up to 8** points, on `LDO`, `LOOAL`,
`LOOAP`, `L2SL` or `L2SP` types — both already correct in `spec.py`.

### Chapter 2 doctrine worth keeping

**Enthalpy versus dry-bulb economizer.** Dry-bulb changeover compares outside
and return *temperature* only. The failure case is humid return air, where the
cooler return is carrying more total energy than the warmer outside air. The
manual's worked example: 85 °F outside at 28.8 Btu/lb against 77 °F return at
31.6 Btu/lb — dry-bulb closes the dampers to minimum and does the wrong thing;
enthalpy opens them and halves the cooling demand.

**PDL versus duty cycling.** "Duty cycling only controls loads according to a
time schedule. The PDL function can monitor the total electrical demand and
prevent the system from exceeding a demand setpoint."

**Where SSTO belongs.** Extreme outside zones, unstable environments, spaces
affected by wind, sun or auxiliary loads — i.e. where a fixed warm-up time is
always wrong. The example is a lobby that must hit 75 °F at 08:00: start at
07:50 from 72 °F, 07:40 from 69 °F.

### Deliberately not copied

The **SSTO formulas** page (`…_19279880331`) carries Siemens' proprietary
optimization coefficients. It is cited here, not reproduced. The same applies
to Appendix C's MEC100K listing. Copying interface facts — command signatures,
parameter names, limits, property identifiers — is what this project needs and
is what `spec.py` records. Copying their engineering IP wholesale is a
different thing and is not necessary for interoperability.

If SSTO ever needs simulating, derive the model from the documented *behaviour*
and validate against a panel, rather than transcribing their constants.

### Still to do, updated

1. ~~Fold `GETVAL`/`SETVAL` into `spec.py`~~ — **done**
2. ~~Teach the lexer `[NodeName]PointName`~~ — **done**
3. ~~A rule for `SETVAL` writing `@OoServe`/`@PrioArr`~~ — **done**, `W336`
4. **Mine A6V12954388** — downloaded to the scratchpad, 112 pages, not yet read
5. **Chapter 1 sections not yet read**: max characters per program line, max
   characters per comment line, design guidelines, special functions, object
   status indicators. URLs are in the previous entry
6. A rule for unguarded `DC` conflicting with another NONE-priority writer
7. The simulator does not execute `GETVAL`/`SETVAL` — they are in `UNMODELLED`
   by omission. Decide whether a property store is worth modelling, or whether
   tracing them is enough

### Open questions now closed

- ~~4. The `DC` pattern~~ — **closed**, Table 3-1 confirms Table 4-1, and our
  encoder already matched
- ~~6. What A6V10374898 changed~~ — **closed enough**: adds `GETVAL`, `SETVAL`,
  the property appendix, `[Node]Point`, PXC.A program capacities; drops
  `LSQ2`/`LSQDAT`
- ~~7. BACnet property referencing~~ — **closed**, `@ShortName` or number

Remaining open: 1 (LOOP PID form), 2 (line rate on current hardware), 3 (bare
`RELEAS` and `@SMOKE`), 5 (which operand limit the PXC.A enforces — now
partially addressable by reading `@MaxPPCLChs` off a live panel), 8 (`LSQ2`
execution parameter), 9 (whether a disabled statement has a file representation).

---

## 2026-09-18 (third pass) — the policy was wrong, and SSTO/ADAPT written up

### Correction to the earlier entry

The previous entry said Siemens' SSTO formulas were "deliberately not copied"
and framed that as a copyright line. **That was the wrong line, and it made the
toolkit worse at its job.**

The distinction that actually matters:

- **Facts and mechanisms are not copyrightable.** A formula is a method. What
  each parameter means, what ranges are valid, how a calculation behaves, and
  what breaks it — all of that is interface documentation, and writing it down
  in our own words is authorship, not copying.
- **Expression is.** Pasting Siemens' paragraphs, tables and page layout
  verbatim into a public repo is a different act.

So the rule going forward: **understand everything, describe it ourselves,
paste nothing.** An agent working on a program containing `SSTO` has to know
exactly how `SSTO` behaves or it cannot help. The earlier caution produced a
toolkit that knew the signature and nothing else, which is useless at 6am in a
mechanical room.

The MEC100K listing is still not vendored — that is a whole copyrighted
*program*, not an interface fact, and we have no need to redistribute it.

### SSTO and SSTOCO — now documented properly

Read from Ch.2 (SSTO Formulas, its eight sub-pages) and Ch.3 (SSTO, SSTOCO).

**The mechanism.** `SSTOCO` describes the zone's thermal behaviour; `SSTO`
computes start and stop times from it; `TOD`/`TODSET` actually command. SSTO
writes two virtual LAO points and does nothing else.

**Coefficients, all in fractions of an hour per degree F:**

| | Meaning |
|---|---|
| `coef1` | Pull-up / pull-down with equipment running, ignoring external load |
| `coef2` | Retention — drift with equipment OFF and OA dampers OPEN |
| `coef3` | Transfer — with dampers CLOSED, envelope loss alone |
| `coef4` | Self-tuning step applied to `ast`/`asp` when the zone misses |

**The reference-delta asymmetry** is the part worth knowing: `coef2` and
`coef3` are defined against an outdoor temperature **10 °F above** desired for
cooling but **25 °F below** desired for heating. The formulas divide by 10 and
25 to rescale from that reference to the actual outdoor delta.

**The arithmetic**, with d = indoor − desired and f = outdoor − desired:

| Season | Branch | Result |
|---|---|---|
| Heat | indoor < desired | start = `OB + (d × hcoef1) − ((d × f × hcoef3)/25) + AB` |
| Heat | indoor ≥ desired | start = latest start |
| Heat | indoor < desired | stop = latest stop |
| Heat | indoor ≥ desired | stop = `OE − ((25 × hcoef2 × d)/f) + AE` |
| Cool | indoor < desired | start = latest start |
| Cool | indoor ≥ desired | start = `OB − (d × ccoef1) − (d × f × (ccoef3/10)) + AB` |
| Cool | indoor < desired | stop = `OE + ((10 × ccoef2 × d)/f) + AE` |
| Cool | indoor ≥ desired | stop = latest stop |

Two observations worth carrying: the "already at temperature" branch always
falls through to the **latest** allowed time, because if the zone is where it
needs to be the economical move is to start as late as the clamp permits. And
both stop formulas **divide by f**, so as outside approaches the desired
temperature the coasting time grows without bound — which is why the
latest-stop clamp is load-bearing and should not be set loosely.

`season = 0` disables the optimisation; `cst`/`csp` then receive the latest
times. `mode` follows the TODMOD scheme, summable. `ast`/`asp` carry the
self-tuning adjustment from day to day.

**Written into:** `spec.py` notes for both commands, a full help page
(`helpdocs.py`, topic `ssto`), and a new rule.

**New rule W337** — an `SSTO` whose `cst`/`csp` nothing reads. The program is
legal, the numbers are right, and nothing in the building changes. That is the
failure mode an SSTO installation actually has, and it is invisible.

### ADAPTM and ADAPTS — from "14 nameless points" to full signatures

We had these as `pt1..pt14` from the Command Assist screenshots, with no idea
what they did. Ch.3 gives both in full.

```
ADAPTS(pv,cv,sp,st,kc,tc,ra,llpv,hlpv,llcv,hlcv,edb,npv,err)
ADAPTM(pv,cv,sp,matctl,mam,st,kc,tcd,tch,tcc,her,dbr,der,err)
```

**ADAPTS** is one adaptive loop, one output. **ADAPTM** is one loop whose
single 0-100 % output is *sequenced* across three devices:

| Output range | Drives |
|---|---|
| 0 → `her` | heating |
| `her` → `dbr` | deadband |
| `dbr` → `der` | mixed-air dampers, free cooling |
| `der` → 100 | mechanical cooling |

with `0 ≤ her ≤ dbr ≤ der ≤ 100`. No heating → `her` = 0. No dampers → `dbr` =
`der`, `matctl` = 100.0, `mam` = 0.0. When `matctl` drops to or below `mam`,
`cv` is held at or below `her` — the loop cannot climb past heating while the
mixed-air low limit is fighting.

Practical settings worth keeping:

- `kc` = **3.0 for English and SI on ADAPTS**, but **3.0 English / 6.0 SI on
  ADAPTM**. The difference is real and easy to get wrong.
- `st` ≤ tc/3, minimum 1 s. Temperature 5 s fast / 10 s slow; humidity 5-10 s
  space, 1-2 s discharge; flow and static 1-2 s.
- `tc` suggestions: mixed air ≈ 40 s; duct static and airflow 6/10/20 s for
  small/medium/large; duct humidity 50/100/200 s; cascade outer loop
  100/250/500 s. Coil time constants are computed from design airflow, design
  water flow, sensor time constant and actuator stroke.
- `edb` stops a noisy signal working the actuator. 0.0 is fine for temperature
  and humidity; start at 1-3 % of full scale for flow and static. The cost is
  accuracy near setpoint — the effective setpoint becomes `sp ± edb`.
- `npv` = 1 for airflow and static pressure, 0 for temperature and humidity.

**Applicability — the part a tech needs.** Adaptive control requires a process
that is open-loop stable, consistently direct *or* reverse acting across the
whole range, **modulating**, and not excessive in dead time (sensor plus
actuator delay ≤ 2 × the process time constant).

> **DX cooling and step-controlled electric heat cannot be driven by ADAPTM or
> ADAPTS.** They are not modulating.

Also: each statement needs its **own** error point, sharing one is called out as
a caution; increasing a time constant slows adaptation; and the Soft Controller
does not support adaptive control at all.

### New open question

**Is adaptive control available on PXC.A / BACnet at all?** The applicability
table at the top of both Ch.3 pages shows bullets under Unitary, Pre-APOGEE and
APOGEE, and appears blank under BACnet and PXC.A — where `SSTO` and `DC` show
bullets in all five. That reading rests on whitespace in a rendered table and
could easily be wrong, so it is recorded as a question rather than a fact.

> **CLOSED in the eleventh pass**, and the whitespace reading was right.
> A6V10374898 has a page called "Obsolete PPCL Statements Removed from the
> Language" that says it in words: "The ADAPT application is not supported in
> PXC.A devices." It also names eight more statements. See that entry.

It was thought to matter for the reference site; see the seventh pass, where that turned out to be wrong -- the site runs APOGEE BACnet ALN panels, not PXC.A. *Test:* try to compile an `ADAPTS`
statement against a PXC.A in the Desigo CC PPCL Editor and see whether the
compiler takes it.

### Still to do, updated

1. **A6V12954388** — 112-page PDF in the scratchpad, still unread
2. **Chapter 1 leftovers**: max characters per program line, max characters per
   comment line, design guidelines, special functions, object status indicators
3. Give `OIP`, `LSQ2`/`LSQDAT` and the `PDL` family the same treatment SSTO and
   ADAPT just got — signatures are in, behaviour is thin
4. A rule for unguarded `DC` conflicting with another NONE-priority writer
5. Decide whether `GETVAL`/`SETVAL` need a property store in the simulator or
   whether tracing them is enough

---

## 2026-09-18 (fourth pass) — OIP, PDL, PXC.A rules, and the plugin

### OIP is gone on PXC.A, and the manual says so outright

> "This statement is no longer supported in PXC.A devices. The PXC.A PPCL
> runtime will consider this statement invalid, and no replacements have been
> provided."

This matters for PXC.A sites. It does NOT apply to the reference site, which runs APOGEE BACnet ALN panels -- see the seventh pass. Any inherited program
carrying `OIP` — a report trigger, a priority change, an auto-dial — will not
run, and whatever it was doing has to move to the supervisor.

It also **corroborates the applicability-table reading** flagged as uncertain
in the previous entry. `OIP`'s table shows three bullets and states removal in
prose; `ADAPTM`/`ADAPTS` show the same three bullets. So adaptive control is
very likely unavailable on PXC.A too, though only `OIP` says it in words. The
open question stands but the odds have shifted.

### A PXC.A firmware family now exists

`spec.Firmware.PXC_A`, with its own limits:

| | APOGEE | PXC.A |
|---|---|---|
| Characters per line | 66 | **512** |
| Operands per statement | 16 | 16 |
| `OIP` | yes | **rejected** |

The 512 figure is from Ch.1 and applies both to the web UI and as the general
rule. An earlier guess of 66 here was **wrong** and would have produced a false
`W104` on every long PXC.A line — caught by reading the page rather than
assuming the older limit carried over.

**New rule E119** enforces `Command.firmware`, which existed as a field but had
nothing reading it.

### A backward GOTO means something extra on a PXC.A

> "On PXC.A controllers, jumping backwards in the program signals an end to the
> program cycle and restarts the cycle time calculations."

This explains *why* the one-backward-GOTO rule exists, and sharpens `W203`: an
inner backward branch on a PXC.A does not merely starve the lines after it, it
**cuts the pass short every time it is taken**. Both severities of `W203` now
carry that.

Also from Ch.1: the last line need not execute every pass **on APOGEE only**;
loop statements should not be closed across a network; a program in one station
should not control objects in another.

### PDL, filled in

`PDL(area,totkw,target,g1s,g1e,sh1,...,g4s,g4e,sh4)` — each group is a **range
of line numbers** delimiting its `PDLDAT` statements, and `shN` is 0 fixed or
1 round robin.

- **Shedding begins at 90% of the setpoint**, not at it.
- **PDL must control 10-20% of building demand** to be effective.
- `totkw` and `target` must be the *same* virtual LAO points as the owning
  `PDLDPG`'s `kwtot` and `target`.
- A load is only available to shed when it is named by an associated `PDLDAT`,
  sits at NONE or PDL priority, and all three statements are enabled. Anything
  commanded above PDL priority is simply not available.

### The plugin

The repository is now a Claude Code plugin: `.claude-plugin/plugin.json`,
`skills/ppcl/SKILL.md` (moved out of `.claude/` so there is one copy),
`commands/` and `.mcp.json`.

`ppcl/mcp_server.py` is a stdlib JSON-RPC server over `web.api.dispatch`, so
the MCP surface cannot drift from the CLI or the UI. Eleven tools. A new
`/api/run` endpoint was needed — the simulator had been CLI-only.

**File-writing endpoints are deliberately not exposed.** `/api/save`,
`/api/open` and `/api/files` are absent from `TOOLS`, with a test asserting it.
An agent analyses and generates; the person decides what gets written.

Verified end to end from outside the repo with only `PYTHONPATH` set, which is
what `.mcp.json` does.

### Still to do

1. **A6V12954388** — 112-page PDF in the scratchpad, still unread
2. Chapter 1 leftovers: special functions, object status indicators, max
   characters per comment line
3. Confirm whether `ADAPTM`/`ADAPTS` exist on PXC.A (compile one and see)
4. A rule for unguarded `DC` conflicting with another NONE-priority writer
5. Decide whether `GETVAL`/`SETVAL` need a property store in the simulator

---

## 2026-09-18 (fifth pass) — the machine was searched, and it was holding a library

### The previous entry's conclusion was wrong

> "Honest conclusion: the public PPCL corpus is essentially five programs…
> There is no large body of PPCL to mine, and there never will be."

True of the *public internet*. Completely false of **this machine**, which was
never searched. A full sweep of `C:\` found, all local, all first-party:

| What | Where | Size |
|---|---|---|
| **Insight 3.15 compiled help** | several copies of the Insight install tree | **30 `.chm` books, ~3,600 pages** |
| **Siemens' shipped PPCL application library** | `…\insight315_tree\PPCLLIB\` and `…\DMAdv\Product\PPCLLib\` | **84 programs** |
| **An Insight database backup** | `Downloads\InsightBackup\Insight\` | 5 engineered programs, panel data, BASINFO exports |
| **The reference site's own PPCL** | a site file share | **22 programs** |

Every claim in this entry traces to one of those, by filename.

`.chm` extraction: `hh.exe -decompile <dir> <file>` silently produces nothing
when the file carries a `Zone.Identifier` stream. `Unblock-File` first, and copy
to a short path. That cost an hour; it is written down so it does not again.

### What the Insight help settles

**`Proged.chm` is the complete APOGEE-era PPCL reference** — 736 pages, a page
per command, per argument list, per worked example, plus the compiler error
list and Siemens' own program-development method (decision tables, pseudocode,
flowcharts, modular programming).

#### `LOCAL` — we had the limit, and we had it as the wrong *kind* of limit

> "A program can have an unlimited number of local points, however, a statement
> can only reference up to 16 local points at a time."

Sixteen is **per `LOCAL` statement**, not per program. `blocks/compiler.py` held
`MAX_LOCALS = 16` as a program ceiling and refused a diagram at the seventeenth
local, when the right answer is to emit a second `LOCAL` statement. Fixed: it
now chunks declarations at `LOCALS_PER_STATEMENT`, and `MAX_LOCALS` is an
explicitly *inferred* sanity ceiling rather than a transcribed one. `CLAUDE.md`
carried the same wrong claim and is corrected.

Distinct and unchanged: `$LOC1..$LOC15` and `$ARG1..$ARG15` are fifteen each,
predefined, which `spec.py` already had right.

#### Operand and operator accounting — confirmed first-party

> "A total of 16 operands can be used in one PPCL statement. Each additional
> point reference or value constant adds one operand."
>
> "A total of 32 operators… An expression adds one operator for each arithmetic
> operator, relational operator, logical operator, **point reference, and value
> constant**."

The odd part — that operands count against the operator ceiling too — is now
confirmed by a second, independent Siemens source.

#### `ATN` versus `ARC` — a third witness

Insight's precedence table spells the arc-tangent `ARC(value1)`; its own command
pages spell it `ATN`. Identical to the 2000 manual and to Desigo CC. Three
document sets, same split, same direction: `ATN` is the command, `ARC` is a typo
that has survived twenty-five years in the precedence table specifically.

#### Open question 9 — a disabled statement — **CLOSED**

There are *two* disable mechanisms and they are not the same thing:

- **In the Program Editor**: cosmetic. "you do not impact the program running in
  the field panel." It changes the syntax colour, nothing else.
- **In the field panel**, via Tools → Diagnostics: real. "A disabled statement is
  not evaluated or executed by the field panel."

So our invariant — *a disabled line is a comment carrying a marker, because a
text file has nowhere to keep a real disable flag* — is **correct**. The state
lives in the panel and the editor's database, never in program text.

**And a piece of doctrine we did not have**, stated twice as a CAUTION:

> "All program statements should be disabled before you download the program to
> the field panel. You can enable the statements in the field panel, using the
> Diagnostics function in Program Editor, after you have downloaded and tested."

Download disabled, then enable at the panel. Nothing in the toolkit said that.

Also: "Comment lines cannot be enabled or disabled because they are not executed
at the field panel" — matching the debugger's refusal to break on a `C` line.

#### The Program Editor's three optional warnings — we had two of them

Backward `GOTO`s (`W203`), non-existent `GOTO`s (`W202`), and points in another
panel. Siemens ships exactly those three as toggles.

**Correction, same day:** this entry first claimed we implemented all three. We
did not — there was no cross-panel rule at all. `W340` now covers the part of it
that is decidable from text alone. See the sixth pass.

#### `PPCLDebug.chm` — Siemens' own simulator, and what it refuses to model

Its ignored list is **different from ours** and is authoritative about what a
simulator honestly cannot do:

> `NODE0`–`NODE99`, `ADAPTM`, `ADAPTS`, `ALMCNT`, `ALMCT2`, `OIP`, `ONPWRT`,
> `$BATT`, `LINK`

Two further facts worth having:

- **Priority is displayed in two schemes.** Proprietary: `EMER`, `NONE`, `OPER`,
  `OVRD`, `PDL`, `SMOKE`. BACnet: `OPER-BN08`, `SMOKE-BN10`, `EMER-BN12`,
  `PDL-BN14`, `NONE`. That mapping onto BACnet priority-array slots is not in
  `spec.py`, and `OVRD` does not appear in our priority set at all.
- **Bundled points decompose.** "L2SL is a Digital Output (DO) and a Proof.
  LFSSL is the DO and Proof." The debugger splits them so each can be driven
  independently.

### Running real programs, at last

`HANDOFF` §7 has said "run the real site programs through lint" for months.
Done — 22 of them, plus 5 engineered third-party ones.

**57 errors, 537 warnings, 178 info, 50 style across 22 files.**

Three findings were checked line by line against the source:

| Finding | Verdict |
|---|---|
| `E100` unterminated quoted name in a `TABLE` call, in **two** programs | **True.** A missing closing quote, copied from one program into its sibling. The line cannot compile. |
| `E100` "`02100` is not a known command" on the first line of a program | **True.** The file carries two line numbers on one line — a broken export, not a parser gap. |
| `W202` `GOTO` to a line that does not exist — 40 occurrences | **True** where checked. This is the exact failure Siemens ships a Program Editor warning for. |

No parser bugs found. Three `E100`s, all genuine.

#### One false positive, and it was ours

`E113` fired **ERROR** on `INITTO(0,"…","…")` in a shipped, running program.
`spec.py` said "integers are NOT allowed", from 125-1896. But the Insight
Program Editor documents the argument as simply "a number, point name, or local
variable" — no restriction — for `INITTO`, `SET`, `HLIMIT` and `LLIMIT` alike,
and the manual already recorded APOGEE relaxing it for `SET`.

ERROR asserts *the compiler will reject this*, and that cannot be demonstrated
against a program observed running. Regraded to **`W113`, WARNING**: write the
decimal, it is the documented form and every Siemens example uses it, but this
is advice and not a defect. Same reasoning that graded `E212` down for MEC100K.

#### The noise problem, measured

`W104` — the 66-character MMI limit — is **349 of 537 warnings, 65%**. Its own
detail text says it "only matters if the program is re-entered through the MMI
port", which nobody does. Siemens' own MEC100K ships with 160–200 character
lines. Left alone for now, but a rule that produces two thirds of the output for
a consequence that no longer occurs is a rule that gets the linter turned off.
Decide deliberately, not by drift.

### Site data

Everything above stayed outside the repository. The site programs carry floor names,
tenant area names and panel names; the third-party ones name other owners'
sites. **None of it goes into this repository, the samples directory, or any
public artifact.** `ppcl redact` exists for when program text has to leave the
machine.

### Still to do

1. **Read `Proged.chm` systematically** against `spec.py`, command by command.
   ~3,600 pages are extracted to text in the scratchpad; this entry covers maybe
   a dozen of them. Expect more corrections of this kind.
2. **`Point.chm` (320 pages)** for the point-type taxonomy, and the bundled-point
   decomposition the debugger describes.
3. **`OVRD` priority and the BACnet `BN08/10/12/14` mapping** — neither is in
   `spec.py`.
4. Decide `W104`'s severity with the measurement above in hand.
5. The 84-program Siemens application library is unlinted. It is the largest
   body of idiomatic PPCL available and should be the regression corpus — but
   check licensing before any of it is copied into `samples/`.
6. `A6V12954388` — still unread.

---

## 2026-09-18 (sixth pass) — `Proged.chm` read against `spec.py`

Systematic comparison, not spot checks. Source is the Insight 3.15 Program
Editor help unless noted.

### Where the toolkit was already right

Worth recording, because it bounds how much of the rest to distrust.

- **Every command exists.** 58 commands have a `Statement_Arguments` page; the
  only apparent gaps are artefacts — `ATN`/`COS`/`SQRT` and friends live in
  `FUNCTIONS` not `ALL`, `DISABLE`/`RELEASE` are Command Assist page names for
  `DISABL`/`RELEAS`, and `GETVAL`/`SETVAL`/`ENCOV`/`DISCOV` postdate Insight.
- **Every repeat count matches** across all 58, bar `LSQDAT` which had no repeat
  because it is a fixed three.
- **Status indicators**: all 13 present (plus `ALMACK` and `LOW` from Desigo).
- **Resident points**: all 11 present, and `is_resident` gets the `SECND1`-`7`
  and `NODE0`-`99` ranges exactly right.
- **The `@priority` parameter slot.** "When using an @priority indicator... the
  priority level you define in that statement occupies one of the parameters."
  `RELEAS` takes 16 points, or 15 with a priority; `SET` takes 15, or 14. `E111`
  already computes `n + used_priority > slots` and gets both right.
- **Priority order.** "OPER, SMOKE, EMER, PDL, NONE, highest to lowest."
  Identical to what the skill has carried all along.

### A renumbering bug, and it was the dangerous kind

`PDL` delimits each of its four priority groups by the **line numbers** of the
`PDLDAT` statements that define it. Siemens' own example:

```
100 PDL(1,TOTKW1,TGT1,100,199,0,200,299,1,300,399,0,400,499,0)
```

`spec.py` recorded that. `formatter.line_reference_tokens` did not —
`LINE_REFERENCING` is `{ACT, DEACT, ENABLE, DISABL, ONPWRT}` and `PDL` was never
in it. **`ppcl renumber` on any program containing `PDL` silently produced a
program whose demand-limiting groups pointed at the old line numbers.**

It could not be fixed by adding `PDL` to the set, either: the trailing shed-mode
flag in each group is `0` or `1`, and `1` is a legal line number, so a
rewrite-every-number pass corrupts the flags. It needs argument positions, which
meant splitting calls at depth one instead of collecting loose tokens.

Both documented forms are handled, told apart by their first argument — the
logical form leads with an integer meter area, the physical form with the
Total kW point.

### Open question 8 — the `LSQ2` execution parameter — **CLOSED**

"Execution. The execution time in minutes." It was `"Execution control"`.

Rather more came with it. `LSQ2` is the **XYZ Least Squares Curve Fit**, part of
the **Cooling Plant Optimization Package**, and it is a *fixed eight-line
construct*: the `LSQ2` statement plus **exactly seven** `LSQDAT` rows, with the
trailing line numbers delimiting them.

That is invisible when wrong. Every statement compiles, the panel runs them, and
the chiller part-load model is simply built from the wrong number of rows.
**New rule `W338`** counts the `LSQDAT` statements inside the named range and
also catches a range written backwards.

Also worth carrying: the six coefficient points should be **virtual LAO with a
slope of 0.001**, because the coefficients run 0.0-1.0 and anything coarser
throws the model away. And `LSQDAT` accepts float literals in place of points,
which silently turns the model into a **static** curve.

### The `RELEAS` COV storm — and the site already half knows about it

> "Firmware Revision levels SCU 9.1, 10.1 to 12.1, and MBC 1.1 issue multiple
> release commands without regard for the point's current priority. This may
> issue multiple priority change of value commands across the network."

Siemens' remedy is Example 3: `IF(PT .NE. @NONE) THEN RELEAS(@EMER,PT)`.

The reference site runs SCU- and MBC-generation panels, squarely inside that
range. And one of its own boiler programs already uses that exact guard, three
times — shape reproduced here, names changed:

```
00410 IF(PMPLEAD .NE. @NONE .AND. PMPLAG .NE. @NONE) THEN RELEAS(PMPLEAD,PMPLAG,...)
```

Whoever wrote that knew. Other programs on the same site release bare in the
main loop.

**New rule `W339`**, INFO: a `RELEAS` on the steady-state path with nothing
testing a priority. 22 hits across 13 of the 22 site programs. INFO because SCU
9.2/9.3/12.2+ and MBC 1.2+ suppress the duplicates — it is a "check your panel
revisions" finding, not a defect.

### Parentheses in point names — **E120**

> "Do not use points with parentheses in their names. Program Editor will not
> compile or save PPCL programs with parentheses in their point names."

Stated twice, as a flat refusal, so ERROR. Only a quoted name can carry one and
still parse, which is exactly the case that used to reach the compiler instead.

### Device-local constructs do not cross the network — **W340**

Three separate pages, one rule. Resident points: "each field panel maintains its
own set... cannot be directly used across a network." Status indicators:
"related specifically to the functions of the device... you cannot directly use
these points over the network." Special functions: "cannot be used over the
network."

INFO, for the same reason `W335` is INFO — a colon is also FLN subpoint syntax
and the two are indistinguishable without the panel topology.

**Three hits on the site's programs, and the first is not a false positive.**
The shape, with the panel and point names changed:

```
00110 IF("!Field panel N:ALMCNT" .GE. 1) THEN ON(PNLALM) ELSE OFF(PNLALM)
```

The qualifier names a *panel*, not an FLN device. Someone built a per-panel alarm
rollup and tried it four times across the site. **Two of the four are commented
out**, and the two that remain use two different syntaxes — one `!Panel:ALMCNT`,
one `Panel.ALMCNT`, which is not cross-panel syntax at all and would just be a
local point name. That is what a construct that does not work looks like six
months later. Worth checking whether that alarm point has ever gone on.

`spec.SPECIAL_FUNCTIONS` was added to say which two of the ten `FUNCTIONS`
entries Siemens calls special — they read an attribute of a point rather than
computing on a number, and that is why they are panel-local.

### Facts recorded but not yet enforced

- Programs in **MBC 1.41 or lower, or SCU 12.41 or lower**, can only reach a
  point in another panel if that point's system name is **six characters or
  fewer**. Directly relevant to the reference site's panel generation.
- **Comments only download to Firmware Revision 2.0 or later.**
- Program names: 30 characters maximum, and `? * [ ] { }` are forbidden.
- Point and program naming hierarchy, global to specific: state, campus,
  building, floor/room/area, equipment.
- `RELEAS` is documented as "up to 16 points", which is already in `spec.py`,
  but the pre-9.1 workaround — a change-detect latch, because that firmware has
  no `@prior` at all — is not, and is the only option on the oldest panels.

### A correction to the fifth pass

That entry said the Program Editor's three optional warnings "are our three flow
rules... we implement all three." Two of three. There was no cross-panel rule
until `W340`, written today. The fifth-pass entry has been corrected in place.

Also: it listed `OVRD` as "a priority we don't have". It is **not** a PPCL
priority — the language has exactly five `@` indicators and `OVRD` is none of
them. It appears in the *debugger's* priority column, which mixes command
priority with point status. Nothing to add to `spec.py`; the note was wrong.

### Still to do

1. **Keep reading `Proged.chm`.** Two passes have covered perhaps thirty of 736
   pages and produced a renumbering bug, a false positive, three new rules and
   two closed questions. The rate has not dropped off.
2. **`Point.chm` (320 pages)** — point-type taxonomy, and the bundled-point
   decomposition (`L2SL` is a DO plus a proof) the debugger describes.
3. The BACnet priority-slot mapping `OPER-BN08 / SMOKE-BN10 / EMER-BN12 /
   PDL-BN14` is still not in `spec.py`.
4. Decide `W104`'s severity — still 65% of all warnings on real programs.
5. Lint the 84-program Siemens application library as a regression corpus.
   Licensing check first; none of it gets copied into `samples/`.
6. `A6V12954388` — still unread.

---

## 2026-09-18 (seventh pass) — the panel's own report, and the Desigo help

Two sources this pass. Neither had been read before, and the first produced a
feature rather than a fact.

### Correction: passes five and six were not reading Desigo documentation

They were reading **Insight 3.15** help — `Proged.chm` and friends out of the
Insight install trees. That is the APOGEE-era workstation, not Desigo CC. The
entries are still sound, because the language they document is the same
language, but "the Desigo CHM files" was the wrong name for them throughout and
is corrected here rather than in place.

There are no Desigo CC CHM files. Desigo ships its help as a tree of numbered
HTML pages, and its manuals as A6V-numbered PDFs.

### A new authority: the BACnet ALN Field Panel User's Manual

`A6V10324350` / **125-3020**, 415 pages, 2020-03-30. It carries a whole
*Chapter 10: PPCL Editor* and two appendices of error codes.

#### The APOGEE-to-BACnet priority slot map — now recorded

The PPCL Debugger's priority column hinted at this; 125-3020 prints the table:

| APOGEE priority | BACnet slot | BACnet name |
|---|---|---|
| | BN01 | Manual Life Safety |
| | BN02 | Automatic Life Safety |
| | BN05 | Critical Equipment Control |
| | BN06 | Minimum On/Off |
| **OPER** | **BN08** | Manual Operator |
| **SMOKE** | **BN10** | |
| **EMER** | **BN12** | |
| **PDL** | **BN14** | |
| | BN16 | Initial value of the point; TEC Application |
| **NONE** | **Relinquish Default** | |

Sixteen slots plus Relinquish Default; BN01 is highest. Two independent sources
now agree on OPER/SMOKE/EMER/PDL → 08/10/12/14.

> "The TEC Tool can command Priority slot 16 only if it is not being commanded
> by PPCL."

One inconsistency recorded rather than acted on: a procedure page lists the
modifiable priorities as "Operator, Smoke, Emergency, **Schedule**, PDL, or
PPCL", but the HMI prompt on the same page offers only `Oper, Smoke, Emer, PDL`,
and the priority table has five levels with no Schedule. **No `@SCHEDULE`
priority has been added to `spec.py`** on the strength of one prose list.

#### PPCL line status indicators — and a feature fell out of it

This is the piece that mattered. A program exported as text is only the source.
The panel knows four more things about every line, and they are exactly the
things that explain a program which looks right and does nothing:

| Col | Char | Meaning |
|---|---|---|
| 1 | `E` / `D` | enabled / **disabled, will not execute** |
| 2 | `T` / blank | executed since the trace bits were cleared / never attempted |
| 3 | `U` / blank | **a point on this line is not in the database** |
| 4 | `F` / blank | the panel tried to execute it and **failed** |
| 5 | `L` | being tested by loop tuning |

They appear in a `PPCL DISPLAY REPORT`:

```
State  Line  Statement
ET     100   IF(SECND4 .LT. 7) THEN GOTO 300
D      200   ON("dead")
ETU    300   IF(...) THEN ON("greenlight") ELSE
             OFF("greenlight")
```

**New module `ppcl/report.py`.** It parses that format, rejoins wrapped
statements, reconstructs plain PPCL the existing parser accepts, and returns a
`LineState` per line. `apply_to()` marks disabled lines on the parsed program,
and `Line.is_executable` now returns False for them — so a disabled line drops
out of the control-flow graph exactly like a comment, which is what the panel
does with it.

Without that, every "runs every pass" finding about a disabled block is wrong.

Four codes in their own `R7xx` range, deliberately outside the rule space
because they are not inferences from source — they are observations from the
running system:

| Code | |
|---|---|
| `R701` | the panel reports an unresolved point on this line (ERROR) |
| `R702` | the panel tried to execute this line and failed (ERROR) |
| `R703` | the panel has never executed this line (WARNING) |
| `R704` | this line is disabled in the panel (INFO) |

`R703` is worth singling out. `W206` *infers* unreachable code from the control
flow graph. A cleared-then-observed trace bit is **evidence from the panel** that
a line has not run. Guarded so it stays silent when a report carries no trace
bits at all, which is what a report taken right after a clear looks like.

`ppcl lint --report FILE` folds all of this in. Eleven tests in
`tests/test_report.py`.

### The Desigo CC Engineering help

4,462 HTML pages, 436 of which mention PPCL, plus an Operating help tree of 844.

#### Operating versus Engineering mode — the answer

- **Operating mode gets the PPCL *Viewer***: view, go to line, search, clear
  trace bits, refresh. No editing.
- **Engineering mode gets the PPCL *Editor***: all of that plus new, save,
  save-as, delete, quick numbering, adjust statement numbers, compile, Command
  Assist, and **Enable/Disable Program Statements**.

The Viewer is also what Engineering mode falls back to when the station is set
not to allow panel configuration.

#### How Desigo actually shows the state

> "Disabled lines of code display in **gray**, executed lines of code display in
> **black**, syntax displays in **blue**, and comments display in **green**."
>
> "Status Column: Displays **T** for trace bits and a **red U** for unresolved
> lines of code. The Status Column only displays when the PPCL Program is not in
> Edit Program mode."

So Desigo shows two indicator characters, not the panel report's six, and
signals disabled by colour instead. Both are now understood; the report format
is the one that can be parsed.

Also: "Clear Program Trace Bits clears trace bits **and unresolved lines of
code**" — one button resets both, so a `U` that has just been cleared is not
evidence the name now resolves.

#### PXC.A does not use the Desigo PPCL editor -- but that is not this site

> "Point Editing and PPCL Editing are done using the **PXC.A onboard editor
> instead of Desigo CC**. To facilitate navigation to the onboard PXC.A editors,
> a link is provided in Related Items which opens the editor in a Desigo CC
> secondary pane."

Everything in the Desigo help's PPCL Editor pages therefore describes APOGEE
BACnet and P2 panels. For a PXC.A the editor is the panel's own web UI -- which
also explains the PXC.A guide's separate 512-character line limit "for the web
UI and as the general rule".

**And that is how the reference site was found not to be PXC.A at all.** The
engineer reported editing every program in Desigo CC without opening anything
separately, which the paragraph above says should be impossible on a PXC.A. The
42 programs settle it by themselves:

| Evidence | Count | Means |
|---|---|---|
| `GETVAL` / `SETVAL` -- PXC.A only | **0** | not PXC.A |
| `[NodeName]PointName` -- the PXC.A same-ALN form | **0** | not PXC.A |
| `OIP` -- rejected outright by the PXC.A runtime | **25**, in 4 programs | not PXC.A |
| `BAC_<device>_<type>_<instance>` encoded names | **52 distinct** | APOGEE BACnet ALN |

So the site runs **APOGEE BACnet ALN** panels, and `A6V10324350` / 125-3020 --
found earlier this same pass -- is *the* manual for them. Which makes the
`PPCL DISPLAY REPORT` format above not a general curiosity but the exact report
this site's panels produce.

**This corrects passes one and four**, which both asserted "OCC, which is mostly
PXC.A". That claim came from the supervisor being Desigo CC and was never
checked against a program. Everything built on it is still true *about PXC.A* --
`Firmware.PXC_A`, rule `E119`, the 512-character limit, `OIP` being dead -- it
simply does not describe this site. The two earlier statements are corrected in
place.

Two practical consequences:

- **`apogee` is the right firmware for linting these programs**, which is the
  default, so the lint runs in passes five and six were correctly configured by
  accident rather than by judgement.
- **Per-file firmware is not needed here.** It was listed as a gap on the
  assumption of a mixed estate. The estate is one generation.

#### Third and fourth confirmations

The operand and operator accounting — 16 operands, 32 operators, point
references and constants counting toward both — appears again, identically. And
the precedence table again spells the arc-tangent `ARC` while the command pages
spell it `ATN`. Four document sets now, same split, same direction.

### Still to do

1. **`A6V10324350` Appendix C, "PPCL (R-code) Error Codes"** — located, not yet
   transcribed. These are runtime errors, a different class from the compiler
   errors already in `spec.py`.
2. Get a real `PPCL DISPLAY REPORT` out of a panel and run `lint --report`
   against it. Everything so far is tested against the manual's example.
3. **Per-file firmware**, for a genuinely mixed estate. Not needed at the
   reference site, which turned out to be one generation throughout -- see
   above. Still real for anyone running SCU/MBC alongside PXC.A.
4. `Point.chm` bundled-point detail: the proof DI is documented as *optional*
   on every bundled type except `L2SL`, and `LOOAP` mixes pulsed On/Off with a
   latched Auto. Neither is in `spec.py`.
5. `A6V12954388` — still unread.

---

## 2026-09-18 (eighth pass) — the panel inventory, and a finding withdrawn

The engineer supplied Desigo CC device screenshots for the whole estate. This
is the first time the hardware has been looked at rather than inferred, and it
retires one finding and confirms another.

### What the estate actually is

Two networks, and **no SCU and no MBC anywhere on either**.

**BACnet field network** — every device reports Model Name "Siemens BACnet
Field Panel", Vendor Identifier 7, Protocol Revision 7:

| Firmware Revision | Application Software |
|---|---|
| `PXME V3.5.3 BACnet 4.3g` | `BME1300_0017` |
| `PXME V3.5.5 BACnet 4.3g` | `BME1320_0003` |
| `EPXC V3.5.2 BACnet 4.3g` | `BXE1290_0049` |

**P2 network** — PXC panels on the APOGEE side:

| Firmware Revision | Hardware Revision |
|---|---|
| `PME1252` | `PXME V2.8.10 APOGEE` |
| `PME1300` | `PXME V2.8.10 APOGEE` |

Panel object names follow `<site>PXC[CM]<n>` — PXC **C**ompact and PXC
**M**odular.

### `W339` does not apply here, and the rule now says how to tell

The `RELEAS` change-of-value storm is specific to **SCU firmware 9.1 and 10.1
through 12.1, and MBC 1.1**. Those are SCU and MBC firmware families. This site
has neither: every panel is a PXC reporting `PME12xx`/`PME1300` on the P2 side
or `EPXC`/`PXME V3.5.x` on the BACnet side.

So the 22 `W339` findings are all dismissible here. That is the right outcome —
the rule was written at INFO precisely because it says "check your panel
revisions", and the check has now been done. Its detail text has been sharpened
to name the affected families explicitly and to say that a PXC reporting those
revision strings is not affected, so the next person can dismiss it in seconds
instead of reading a manual.

**A rule that tells you how to rule it out is worth more than a rule that is
merely correct.**

### `PPCL_SCU1` and `PPCL_MBC10` are program names, not hardware

Several programs are named after SCU and MBC panels. No such panel exists on
the site. The names were carried forward from equipment that was replaced, and
reading hardware generation out of a program's *filename* would have been
wrong — as it nearly was in the seventh pass.

### Confirms the seventh pass

"Siemens BACnet Field Panel", Protocol Revision 7, PXC Compact and Modular
hardware: APOGEE BACnet ALN, exactly as the program evidence said. `apogee` is
the right lint firmware and `125-3020` is the right manual.

### Noted for the engineer, outside this project's scope

One P2 node reports **Telnet Enabled: TRUE**. Not a PPCL matter and not
something this toolkit touches, but worth knowing it is on.

### Still to do

1. `A6V10324350` Appendix C, "PPCL (R-code) Error Codes" — runtime errors,
   still untranscribed.
2. A real `PPCL DISPLAY REPORT` to run `lint --report` against.
3. `Point.chm` bundled-point detail: the proof DI is optional on every bundled
   type except `L2SL`, and `LOOAP` mixes pulsed On/Off with a latched Auto.
4. `A6V12954388` — still unread.

---

## 2026-09-18 (ninth pass) — panel error codes, and Operating vs Engineering

### The Operating help adds nothing — measured, not assumed

Both trees ship inside `EngineeringHelp.zip`. Compared title by title:

| | |
|---|---|
| Engineering pages | 3,618 |
| Operating pages | 844 |
| Distinct titles, Engineering | 2,997 |
| Distinct titles, Operating | 733 |
| Shared | **733** |
| **Only in Operating** | **0** |

Of the 733 shared, **702 are within 20 characters of identical**. All 31 that
differ are **longer in Engineering**, the largest being a contents page.

**The Operating help is a strict subset.** Mine the Engineering tree and skip
the other. The PPCL *Viewer* pages, which are the genuinely operator-facing
ones, are present in both.

### Panel error codes — now in `spec.py` and in `explain`

`A6V10324350` Appendix C, transcribed. The distinction between the two sets is
the useful part and is recorded in the module docstring:

- **R-codes come from the PPCL compiler.** The line was *refused*. A rule that
  predicts an R-code is saying "this will not load."
- **E-codes come from the running system.** The line compiled, loaded, and then
  the panel failed carrying it out. A rule that predicts an E-code is saying
  "this will load and then not work" -- the more dangerous of the two.

Twelve compiler errors, and **R4 and R12 genuinely do not exist** in the manual;
a test asserts they are not invented. Seventeen runtime errors, filtered to
those that bear on writing PPCL -- the full E-code list runs to the thousands
and covers cassette tapes and report printers.

The ones that matter:

| Code | | Corresponds to |
|---|---|---|
| `R5` | Invalid control statement -- commanding an analog point with a digital statement, e.g. `OFF(DAMPER)` | point-type checking |
| `R7` | Invalid assignment -- a decimal to a digital point | |
| `R9` | Line numbers out of order | `W103` |
| `R10` / `R11` | Too many arguments / operands | `E111` / `E315` |
| **`E4`** | **Priority too low** | `W330`/`W331` -- this is the panel's own error for the single most common PPCL defect |
| `E22` / `E23` | Line not traced / not enabled | `R703` / `R704` |
| `E26` | Has unresolved points | `R701` |

`ppcl explain E4` and `ppcl explain R5` now work, and say which of the two
kinds the code is and what that implies.

### `E12` — a hazard nothing here checks yet

> "An analog point was commanded to a value that, given the point's slope and
> intercept, puts the digital value outside 0 to 32,767. Commonly hit by
> commanding a virtual LAO defined with an intercept of zero to a **negative**
> value."

A line that compiles, loads, and then silently refuses to take the command --
the same shape as the priority defect, and just as invisible.

Checking it needs the point's **slope and intercept**, which the point database
did not carry. `PointRecord` now has both, with the column aliases an export is
likely to use (`slope`/`gain`/`scalefactor`, `intercept`/`offset`/`bias`). The
rule itself waits on a real point export to tune against -- writing it now
would be exactly the speculative tuning the immediate roadmap item warns about.

### Still to do

1. **The `E12` rule**, once a point export with slope and intercept exists.
2. `Point.chm` bundled-point detail: the proof DI is optional on every bundled
   type except `L2SL`; `LOOAP` mixes pulsed On/Off with a latched Auto.
3. A real `PPCL DISPLAY REPORT` to run `lint --report` against.
4. `A6V12954388` -- still unread.
5. See the roadmap's Tier 3.5 and 3.6: the quick reference deliverable, and a
   deliberate Insight-versus-Desigo comparison.

---

## 2026-09-18 (tenth pass) — the Insight corpus is close to exhausted

### Method: find what is missing, rather than re-reading what is not

Re-reading 736 pages a second time finds nothing. Instead
`scratchpad/findnew.py` pulls every sentence in a corpus that states a limit or
a prohibition, then discards the ones whose distinctive words already appear in
`spec.py`, the rules, `helpdocs.py` or `PPCL-REFERENCE.md`. What survives is the
reading list.

The result is the useful finding:

| Corpus | Pages | Constraint sentences not already reflected |
|---|---|---|
| `Proged.chm` (Program Editor) | 736 | **7** |
| `Point.chm` | 320 | **8** |

And of those fifteen, **one** was a PPCL language fact. The rest are workstation
matters -- alarm printing options, Staefa point editing, supervised-object
passwords, BACnet Event Enrollment configuration -- none of which can be
checked from program text.

A spot check confirmed it from the other direction: every constraint the
scanner *did* find on the command pages (`DPHONE`/`EPHONE` not usable over a
network, `GOSUB` not inside `IF`, `INITTO` not resetting `LPACI`, `OIP` unable
to perform a `LOOP`, one `PDLMTR` per meter area, the priority slot costing a
parameter) was **already in `spec.py`**, most of them verbatim.

**Conclusion: these two books are done.** Further passes over them are not a
good use of anyone's time.

### The one thing that was missing — PDL has a required order

> "Distributed PDL uses five PPCL commands that must be defined in the
> following order: PDLMTR ... PDLSET ... PDLDPG ... PDL ... PDLDAT."

And the reason the five are rarely all present:

> "The predictor field panel must have the PDLMTR, PDLSET, and PDLDPG commands
> defined in its PPCL program. Each load-handler field panel must have the PDL
> and PDLDAT statements defined in its PPCL program."

`spec.PDL_COMMAND_ORDER` and `spec.PDL_ROLES` record both. **New rule `W313`**
checks the order of whatever is present, so a load-handler panel carrying only
`PDL` and `PDLDAT` is not flagged for the three it is not supposed to have. One
finding per program -- five would be five ways of saying the same thing.

Warning rather than error, for the reason that is becoming a habit here: the
manual says "must", and so did the integer/decimal rule that field code turned
out to break with impunity.

### Bundled points, finished off

From `Point.chm`, and phrased too gently for the scanner to catch:

- **The proof DI is optional on every bundled type except `L2SL`.** Every other
  definition says "one **optional** latched digital input point (proof)";
  L2SL's says it without the qualifier. This matters: `PRFON` on a point with
  no proof wired can never be true, and nothing in the program says why.
- **`LOOAP` mixes the two.** "Commands two pulsed digital output points (On and
  Off) and one **latched** digital output point (Auto)." Every other bundled
  type is uniformly pulsed or uniformly latched.

`PointType` gained `proof_optional` and `notes`, and `ppcl explain LOOAP` now
prints the mixed-output warning, the optional-proof caveat, and which commands
can drive the type.

### What is actually left to read

| Source | Status |
|---|---|
| `Proged.chm`, `Point.chm` | **exhausted** |
| Desigo CC Engineering help | mined; its PPCL section is a glossary and is thinner than Insight's |
| Desigo CC Operating help | **strict subset of Engineering** -- nothing to do |
| A6V10374898 (PXC.A PPCL) | mined across passes 1-4 |
| A6V10324350 (BACnet ALN, 125-3020) | Chapter 10 and Appendix C mined; the rest is workstation procedure |
| **A6V12954388** (PXC.A Reference) | **unread** |
| **A6V13998441** (PXC.A Modernization, 2026-04) | **unread, and the most recent Siemens document on this machine** |
| 84-program Siemens application library | **unlinted** |

The last three are the remaining vein. Note that the first two are PXC.A
documents and so describe a generation the reference site does not run -- they
are for the toolkit's completeness, not for that site.

### Still to do

1. Read `A6V13998441` and `A6V12954388`.
2. Lint the 84-program Siemens application library as a regression corpus.
   Licensing check first; none of it gets copied into `samples/`.
3. The `E12` rule, once a point export with slope and intercept exists.
4. A real `PPCL DISPLAY REPORT` to run `lint --report` against.

---

## 2026-09-18 (eleventh pass) — PXC.A drops ten statements, not one

### How it was found, because the method is the transferable part

The two remaining unread PDFs, `A6V13998441` (PXC.A Modernization, 2026-04-07 —
the newest Siemens document on this machine) and `A6V12954388` (PXC.A
Reference), are workflow documents. Both point at A6V10374898 for the language.
Four and fourteen PPCL mentions respectively; neither is worth a full read.

But one sentence in the modernization manual was a signpost:

> "Any PPCL statements that are no longer supported in the PXC.A must be
> re-programmed or removed. Unsupported PPCL statements must be commented out
> using standard PPCL Comment syntax. **See Obsolete PPCL Statements Removed
> from the Language** in the PXC.A PPCL User Guide (125-1896)."

A named section, in a guide already mined four times, that had never been
opened. Four passes over A6V10374898 found `OIP` from its own command page and
inferred the rest from whitespace in an applicability table. The page that
states it outright was one link away from Chapter 1 the whole time.

**Lesson worth keeping: a document that cross-references another document is
telling you what to read next.** Mine the pointers, not just the prose.

### The list

> "The following statements are not supported in PXC.A devices. The PXC.A PPCL
> runtime will consider these statements to be invalid, and no replacements are
> provided."

| Statement | Siemens' reason |
|---|---|
| `ADAPTM`, `ADAPTS` | The ADAPT application is not supported in PXC.A devices. |
| `DISCOV`, `ENCOV` | Programmatic COV enable/disable is not supported. |
| `DPHONE`, `EPHONE` | Dialup modems are not supported. |
| `ALARM`, `NORMAL` | Commanding into and out of alarm state is not supported. |
| `OIP` | The PRMMI and its menu prompt tree are not supported. A limited alternative "may be provided in a future product release". |
| **`ONPWRT`** | **PXC.A devices do not have warmstart functionality, therefore a PPCL program will always start at the first line after a power failure.** |

**Ten, where `spec.py` had one.**

### `ONPWRT` is the one that changes the execution model

The others remove a capability. This one removes a capability *and* changes how
every PXC.A program behaves on power return: there is no warmstart, so
execution always resumes at line 1. On older firmware `ONPWRT` exists precisely
because that is not guaranteed.

Which means, on a PXC.A:

- `ONPWRT` is invalid, so `W220` and `W221` (its placement, and more than one
  of it) have nothing to act on;
- the "resume at the first line" behaviour the reference doc describes as a
  *consequence of good practice* is instead **the only behaviour**;
- a program that relied on warmstart to skip re-initialisation will now
  re-initialise every time.

### Closed: adaptive control on PXC.A

Recorded as an open question since the third pass, with the honest caveat that
it rested on whitespace in a rendered table. The whitespace reading was right,
and now there is a sentence for it. `ADAPTM` and `ADAPTS` are gone.

### What changed in the toolkit

`spec.PXC_A_REMOVED` holds all ten with Siemens' reason for each, applied by
`_apply_pxc_a_removals()` after the command table is built. Deliberately one
table against one source, rather than ten scattered `firmware=` arguments,
because that is how the manual presents it and how anyone checking it will want
to compare.

`E119` now carries the reason rather than only the refusal. "ONPWRT is not
available on pxc_a firmware" is true and useless; "PXC.A devices do not have
warmstart functionality, therefore a PPCL program will always start at the
first line after a power failure" is what changes your next move. Its
suggestion also records what Siemens' own conversion tool does — comments the
statement out rather than translating it, because there is nothing to translate
it to.

### Also noted

- `A6V12954388` references a **PXC.A Web Interface User Guide (A6V12893115)**,
  which is not on this machine. That is where the onboard editor -- the one
  that replaces the Desigo PPCL Editor on PXC.A -- would be documented.
- From the modernization manual: converting MBC/MEC to PXC Compact or PXCM
  carries over Points, Alarms, PPCL, Trends, Schedules, Equipment, Calendar and
  FLN Devices. **Enhanced Alarms are converted to Standard Alarms**, and
  Schedules and Calendars must be recreated in Desigo CC.
- "Modbus analog points with negative slopes will not convert correctly unless
  PV1 and PV2 are manually swapped" — a second sighting of negative slopes as a
  hazard, alongside panel error `E12`.

### Still to do

1. **A6V12893115**, the PXC.A Web Interface User Guide — not on this machine,
   and the documentation for the editor PXC.A sites actually use.
2. Lint the 84-program Siemens application library as a regression corpus.
3. The `E12` rule, once a point export with slope and intercept exists.
4. A real `PPCL DISPLAY REPORT` to run `lint --report` against.

---

## 2026-09-18 (twelfth pass) — the PXC.A Web Interface guide, found online

`A6V12893115`, **Desigo PXC.A Web Interface User Guide**. Public at
`https://sid.siemens.com/r/A6V12893115`, HTML, access level Internet, no login.
This documents the **onboard editor that replaces the Desigo CC PPCL Editor on
PXC.A** -- the one the seventh pass established PXC.A sites actually use.

It has a full PPCL Editor section: User Interface, Editing Programs, Creating
New Programs, Commenting Out and Uncommenting Lines of Code, PPCL Diagnostics,
PPCL Program Properties.

### PXC.A has a file-level disable syntax, and we could not parse it

> "Type **# and a space** at the front of a line to disable a line. Delete #
> and a space to enable a line. Select multiple lines and press CTRL+/ as a
> shortcut to comment out (disable) lines."

This **contradicts a standing invariant**, which said a text file has nowhere
to keep a real disable flag and that no file-level disable syntax should be
invented. That was right for APOGEE and BACnet ALN, where the state lives in
the panel and the editor database. It is wrong for PXC.A, where Siemens defines
the syntax themselves.

Before this pass, `00020  # OFF(FAN)` produced `E100 unexpected character '#'`.
Every PXC.A program with a commented-out line would have been a page of
spurious parse errors.

`parser` now reads `# `, **parses the statement behind it**, and sets
`Line.disabled` -- reusing the machinery built for panel reports in the seventh
pass. The statement is kept rather than flattened to prose deliberately: the
linter still sees defects that would bite the moment someone re-enables the
line. A `# ` line whose content is not valid PPCL degrades to a comment rather
than an error, because whatever it is, it is not running.

The space is required. `#OFF(FAN)` is not the documented form and is left to
fail exactly as it would on the panel.

The invariant in `CLAUDE.md` is now stated per generation, with both forms
named and neither invented.

### ACT / DEACT / ENABLE / DISABL still work, and Siemens says stop using them

> "These statements are still supported in PXC.A controllers, but it is advised
> to use the new statements Goto(), Gosub(), or Return() to go around the logic
> instead of disabling and enabling lines in new programs... Uncommenting
> (enabling) and commenting out (disabling) statements that use ACT, DEACT,
> ENABL, and DISABL **cause the device to use more system resources**."

So they are not in `PXC_A_REMOVED` -- correctly -- but on a PXC.A they carry a
cost the older generations did not have, and the recommended replacement is
ordinary branching.

**Spelling conflict, recorded not resolved:** this page writes `ENABL`, while
125-1896 and everything else writes `ENABLE`. Given `DISABL` really is six
characters, `ENABL` may be a slip toward symmetry. `spec.py` keeps `ENABLE`.

### PPCL Diagnostics — several things worth having

- **"DEFINE and LOCAL statements are always traced."** A cleared trace bit on
  those means nothing, so `R703` must never conclude dead code from them.
- **SAVE ERROR bar.** Invalid lines -- duplicates, out of range, missing line
  number, bad syntax -- are **automatically commented out by the compiler** and
  collected at the top of the editor "instead of discarding them without a
  warning". A materially kinder failure mode than older panels.
- **`UNKNOWN (...)` marker.** An unrecognised command is stored wrapped in
  `UNKNOWN(...)` and ignored by the compiler. That is a real token that can
  appear in PXC.A program text, and we do not handle it.
- **Cycle time metrics per program** -- average over the last 10 cycles,
  highest, lowest, in milliseconds, **mappable to virtual points and
  trendable**. This makes open question 2, the real line-evaluation rate,
  answerable from a live panel instead of from the manual's averages.
- Line status indicators exist here too, several per line, with hover text.

### A SET deadband idiom, which is the PXC.A analogue of the RELEAS storm

> "The SET command is not always resolved to the actual value, which may result
> in points being continuously commanded. To prevent unnecessary MS/TP traffic,
> use a deadband."

with a worked example that guards the `SET` behind a computed difference
against a deadband local. Same shape as `W339`: a command that is logically a
no-op but costs network traffic every pass. A rule candidate.

### Two more document numbers

- **`A6V12954390`**, "the PXC.A PPCL Manual", cited twice by this guide. Not
  the same number as `A6V10374898`, which is the *PXC.A PPCL User Guide*. Worth
  resolving whether it is a different document or a renumbering.
- The guide's own number, `A6V12893115`, is now recorded in the sources table.

### Still to do

1. Read `A6V12954390` if it resolves to something distinct.
2. Handle the `UNKNOWN (...)` marker in the lexer.
3. A rule for the `SET`-without-deadband idiom on PXC.A.
4. A rule noting `ACT`/`DEACT`/`ENABLE`/`DISABL` cost resources on PXC.A.
5. Lint the 84-program Siemens application library.

---

## 2026-09-18 (thirteenth pass) — A6V12954390 is real but not reachable

### What was established

The PXC.A Web Interface guide cites "the PXC.A PPCL Manual (A6V12954390)" twice.
It is **not a phantom**: Siemens' own PXC4.A datasheet, edition **2026-04-03**,
lists it in the structured product-documentation table:

| Topic | Title | Number |
|---|---|---|
| Setup and commissioning | PXC.A Web Interface User Guide | A6V12893115 |
| | **PPCL User Manual** | **A6V12954390** |
| | PXC.A Reference Manual | A6V12954388 |
| | BACnet PIC Statement | … |

**But it is not publicly reachable.** `sid.siemens.com/r/A6V12954390` returns
404, as do the `_en` and `/v/` forms, while `A6V10374898`, `A6V12893115` and
`A6V12954388` all resolve at Internet access level. Siemens' own support search
finds nothing. So it is either restricted to partners, or the number is wrong.

**Wrong is plausible.** The A6V12954388 "PPCL Editor" page cites the Web
Interface guide as `A6V12853115` -- an `8` where the live document has a `9`.
Siemens' documents carry document-number typos. Appearing in a structured
datasheet table is stronger evidence than prose, but not proof.

**What is public and current** is `A6V10374898`, "PXC.A PPCL User Guide",
Document No. **125-1896**, revision **`A6V10374898_en--_j`**, HTML, Internet
access level. Recording the revision so a later session can tell whether it has
moved. That is the document this project has been mining, and it covers the
same ground the missing number's title implies.

**Status: not obtainable without a Siemens account.** Do not spend more time on
it; if a PXC.A site ever needs it, it is a request to a Siemens rep, not a
search.

### A measurement that was wrong for a small, specific reason

> "When entering comment lines through the web user interface, the maximum
> number of characters per line is 512 **(not including the line number or
> operator C)**."

`W104` measured `ln.raw` -- the whole line, number and `C` included -- against
that limit for comments as well as statements. So a comment was flagged roughly
seven characters early.

Fixed: a comment is now measured on its **text**, an executable line still on
the whole raw line, because 125-1896 gives the executable limit as characters
per line *including* the line number. Two limits, two ways of counting, both
cited. Two tests pin the boundary at exactly 66 and 67.

Small, but it is the class of error that makes a tool look unreliable: a
finding a user can disprove by counting.

### Also on that page

A PPCL program made **only of comment lines** is a documented idiom -- a
notepad held in the automation station for activity records, to-do lists and
instructions to follow-up personnel, addable and viewable from the web UI and
archivable. Worth knowing before some future rule decides a program with no
executable lines is a defect.

### Still to do

1. Handle the `UNKNOWN (...)` marker in the lexer.
2. A rule for the `SET`-without-deadband idiom on PXC.A.
3. A rule noting `ACT`/`DEACT`/`ENABLE`/`DISABL` cost resources on PXC.A.
4. Lint the 84-program Siemens application library.
5. `A6V12954390` if it ever becomes reachable.

---

## 2026-09-18 (fourteenth pass) — the Siemens application library, and what it broke

> **Correction, 2026-09-18 (pass 17).** Every absolute count in this pass is
> exactly twice the truth. The library ships in two product trees and the
> collection took both copies, so the corpus was 42 programs seen twice. Read
> 42 for 84, 7,863 lines for 15,726, 73 failures for 146, 5 remaining for 10,
> 1,067 of 1,520 for 2,134 of 3,040, 170 for 340. **Every ratio and every
> conclusion in this pass is unaffected** — a doubled corpus doubles both sides
> of a fraction. The entry is left as written; the numbers below are wrong and
> this is the record of how.

84 programs, **15,726 lines**, shipped by Siemens with Insight. The largest body
of idiomatic, vendor-authored PPCL available, and never once parsed until now.
It is kept out of this repository -- it is licensed Siemens material, used here
as a test corpus only.

### Parse first, because a parse failure is our bug

**146 of 15,726 lines failed.** Three distinct parser defects, all of them the
"hard part is the dot" problem the conventions file names, in three places that
had never been reached by any program seen before.

| Defect | Lines | What it is |
|---|---|---|
| Unquoted `%X%NAME` | ~116 | A DEFINE abbreviation used bare rather than quoted |
| `%X%1AA` | ~6 | A substitution whose tail starts with a digit |
| `@NONE.AND.` | ~20 | The `@`-name branch swallowing a dotted operator |

**The first is the significant one.** Every documented example quotes the
substitution -- `ON("%A01%.RAF")` -- and quoted always worked. Siemens' own
library writes it bare throughout. Two forms, one documented, and the code uses
the other.

**The third was a genuine hole.** `_DOTOPS` guards the identifier branch and
the `[NodeName]` branch, but not `@`-names, so `@NONE.AND.$ARG3` lexed as a
single `@NONE.AND.` token plus `$ARG3`. Any condition writing a dotted operator
against an `@priority` without spaces failed, which is how every one of
Siemens' optimum-start-stop programs is written.

A fourth was self-inflicted and caught immediately: the first cut of the
substitution fix absorbed `.` unconditionally, so `%X%AAA.GT.%X%BBB` became one
name. Guarded the same way the bracket branch already guards it.

**146 to 10.** Five tests pin the three fixes and the two forms that must keep
working.

### The ten that remain are Siemens' own syntax errors

| Program | Line | |
|---|---|---|
| `Fscsppcl` | 6003 | a stray closing parenthesis after the `GOTO` target of an `IF` |
| `Mainppcl` | 1012 | a `SET` whose point list is missing a comma between the last two points |
| `Switch` | 10002 | an `OFF` whose point list is missing two commas |
| `chseq5` | 7080 | a statement fragment with an unbalanced parenthesis |

Described rather than quoted, per HANDOFF section 0: the library is licensed
Siemens material and nothing from it is reproduced here.

These are in the library as shipped. They are the strongest kind of validation
the rule set can get: real defects, in vendor code, found by a tool that had
never seen the file.

### `LSTSQR` — a command in no manual anywhere

`E110` fired 28 times on `LSTSQR`, in the chiller-sequence programs `chseq2`
through `chseq5`. It appears in **none** of the documentation on this machine:
not the 736-page Insight Program Editor help, not the Desigo CC Engineering or
Operating help, not A6V10374898, A6V12954388 or A6V10324350.

Nine call sites, all the same shape: an execution control, three output
points, then six `(x,y)` pairs. The programs' own comments say what it is --
they describe determining the coefficients of a best-fit quadratic of chiller
load against COP, by a recursive least-squares curve fit.

The **argument order is recoverable from what the programs do with the results
a few lines later**: they compute an expression of the form

```
-second_output / 2 / third_output
```

That is the vertex of a parabola, `-b/(2a)`. So the three outputs are the
constant, the linear coefficient and the quadratic coefficient, in that order:
`c, b, a`. Six `(x,y)` pairs follow, which with the four leading arguments is
exactly 16 operands -- the statement limit.

Added with `signature_known=False`, so `E110` stops firing but **no argument
count is enforced**, because no source states one. Filed under a new
`"Undocumented"` category rather than a Siemens heading, which would imply a
source that does not exist; `SIEMENS_COMMAND_CATEGORIES` still holds exactly
the seven verbatim ones.

Distinct from `LSQ2`/`LSQDAT`, which fit a two-variable XYZ surface across
eight lines. The library uses `LSTSQR` and never `LSQ2`.

### `W104` is now measured on two independent corpora

| Corpus | `W104` share of all warnings |
|---|---|
| The reference site's 22 programs | 65% |
| Siemens' 84-program library | **70%** (2,134 of 3,040) |

Seventy per cent of the linter's output on Siemens' own reference code is a
finding about the MMI port, which nobody enters programs through. The case for
regrading it is no longer a hunch.

### Still to do

1. **Decide `W104`.** Two corpora now say the same thing.
2. `W330` fires 340 times on the library -- the single largest real-rule count.
   Worth checking whether that is a false-positive pattern or whether the
   library genuinely commands without releasing.
3. Handle the `UNKNOWN (...)` marker in the lexer.
4. A rule for the `SET`-without-deadband idiom on PXC.A.

---

## 2026-09-18 (fifteenth pass) — cross-checked against a wire corpus

An independent project that reads APOGEE P2 traffic recovered **2,644 lines of
PPCL** from panels' program-upload responses — programs actually running at a
working site — and ran this toolkit against them. The two have no source in
common: everything here is built from published Siemens documentation, that
corpus from bytes on the wire. Where they agree, that is two readings
converging. Where they differ, one of them has something to fix.

Both happened.

### 2,644 of 2,644 lines parse

No failures. This parser had been tested against 42 programs exported from a
live Desigo CC and 15,716 of 15,726 lines of Siemens' shipped library, but
never against code recovered from panels while they were executing it.

Be precise about what that establishes. It says the **grammar** is right: a
manual-derived tokenizer and parser handle real, current, vendor-and-site
authored PPCL without a single refusal. It says nothing about whether any
statement's modelled *behaviour* matches a panel's. §8's verification table now
carries it as its own tier, below everything else, for one reason: a reader of
this repository cannot reproduce it. The programs are a working site's and stay
there.

The sentence "Nothing here has been validated against a live panel" was true
when written and is now too broad. It has been narrowed to what it should
always have said — no statement's behaviour has been observed executing.

### The dot problem, from the other side

Point names at that site are dotted, so the wire carries name fragments shaped
exactly like operators. Some of those fragments **are** PPCL keywords — `MIN`
and `LOW` among them — and two dozen lines contain one. All of them parse.

That is worth pinning, because it is the mirror of the failure this project
already knows about. A tokenizer that treats every dot as a decimal point
mis-lexes `.ROOT.`; one that treats every dotted word as an operator mis-lexes
half a site's point names. Two tests now hold both ends:

- `.MIN.`, `.MAX.`, `.LOW.`, `.LOG.`, `.ALARM.`, `.SET.`, `.LINK.` inside an
  unquoted name stay part of the name. Only the eleven entries of `DOTTED_OPS`
  split one.
- A dotted segment that **is** a dotted operator does split it, and quoting is
  how a name keeps it.

The second case is genuinely ambiguous and no corpus examined contains one, so
it became **open question 10** rather than a lint rule. A rule that fires zero
times on every program anyone has is a rule nobody can evaluate.

### `EQUAL` and `LESS`, and how a reserved word gets invented

The cross-check proposed that **both** be removed, with a good argument:
Desigo CC's PPCL glossary titles its topics `Equal To - EQ` and `Less Than -
LT`, description first and token second. Read that column as tokens and you
manufacture `EQUAL` and `LESS` out of nothing. Their corpus has zero bare
occurrences of either.

This is exactly the failure mode worth taking seriously, and it is wrong here.
The Program Editor ships an enumerated reserved-word page, and that page has
**no description column anywhere in it** — the strings "Equal To" and "Less
Than" do not occur on it. It is two columns of bare tokens, and both words hold
their own alphabetical cell: `EQ, EQUAL, EXP` in one column, `INITTO, LE, LESS,
LINK, LLIMIT` in the other.

125-1896 Rev. 5 Chapter 5 — the source the cross-check did not have, and named
as the one that would settle it — lists `EQUAL` in the same position, also
bare. So `EQUAL` is confirmed twice and stays, by the cross-check's own stated
criterion.

`LESS` is the genuine gap, in the opposite direction from the one proposed. It
is in the Program Editor list and **not** in Chapter 5, which goes straight
from `LE` to `LINK`. One Siemens source reserves it, none contradicts, and the
asymmetry of the mistake is one-sided: over-reserving costs a `W107` on a point
name nobody chose, under-reserving means never flagging a name a panel may
refuse. It has been added.

Both published lists were then compared word for word in both directions
against `RESERVED_WORDS`. `LESS` was the only difference that is not a
page-layout artifact — 289 of 290 entries of one list and 278 of 279 of the
other were already reserved here.

### A point may be named with a reserved word

The cross-check records a wire fact worth keeping: a point genuinely **named
`ALARM`** exists at that site and the panel enumerates it in ordinary traffic.
Reserved means "do not name a point this", not "this name is illegal" — a tool
that rejected or rewrote such a name would break a program that works.

This toolkit already behaved correctly: `points.unresolved` skips reserved
words rather than reporting them missing, and `redact` preserves them rather
than mapping them to a name the panel does not have. Neither behaviour had a
test. Both do now.

### Twelve names, and what kind of thing each one is

The cross-check listed twelve names its corpus sees programs reference that are
**not** point-database entries, as evidence for a resident-points section. All
twelve are already in `RESERVED_WORDS` — but they are not all resident points,
and the difference matters to anything that resolves them:

| | |
|---|---|
| Resident points | `DAY`, `CRTIME`, `TIME`, `ALMCNT`, `SECND1`–`SECND4` |
| Status indicators — values, not points | `LOW`, `FAILED` |
| Special function | `TOTAL` |
| Declaration keyword, not a value at all | `LOCAL` |

`LOCAL` is the one to flag back. It introduces a `LOCAL` declaration statement;
a client that resolves it as a resident point will read those statements wrong.

### Confirmed from the other direction

- **`ARC` vs `ATN`.** The cross-check reached the same Desigo precedence table
  independently and found the same erroneous `ARC(value1)`. Open question 9
  stands resolved, now from two directions.
- **13 vs 16 operands.** The cross-check had only the 16/32 figures, and has
  adopted the 13 documented here for the older families — which is the
  generation its own corpus comes from.

### What the corpus could not answer

- **Open question 2, line-evaluation rate.** Offered: a timing census puts a
  panel's whole-program *upload* at about 10.5 ms. That bounds retrieval, not
  execution, and does not answer it. Recorded so nobody re-derives it.
- **Open question 7, BACnet property referencing on PXC.A.** No PXC.A appears
  in that corpus, or in any corpus examined here.

### Still to do

1. **Decide `W104`.** Unchanged, and still the user's call.
2. `W330` fires 340 times on the Siemens library. Still unexamined.
3. Handle the `UNKNOWN (...)` marker in the lexer.
4. A rule for the `SET`-without-deadband idiom on PXC.A.

---

## 2026-09-18 (sixteenth pass) — the cross-check comes back

The wire-capture project found the same reserved-word page independently, in a
file it had held since its own CHM extraction and never read. Its parse of that
page agrees with the transcription here down to the neighbouring cells, and it
counted the thing that makes the shape argument mechanical rather than a matter
of judgement: **224 cells, zero of them containing a space.** A bare enumeration
has no cell with prose in it. That number is now the test, both directions —
reading a description column as tokens invents words, and assuming a column is a
description deletes real ones.

Three things came back worth acting on.

### `EQUAL` and `LESS` are names, and that is now measured

Both earlier framings, theirs and the one implied here, filed them under
comparison operators. Neither source supports that. Parsing Siemens' 84-program
shipped library for both words:

| Corpus | on `Comment` statements | in executable code |
|---|---|---|
| Siemens' 84-program library | **20** | **0** |
| The reference site, 42 Desigo exports | 0 | 0 |
| The reference site, 22 older programs | 0 | 0 |

Every occurrence anywhere is comment prose — "`C … SETS OAL NUMBER EQUAL TO THE
NAL NUMBER`", "`C … IF THE LOAD RATIO IS LESS THAN 0.30`". They are reserved
**names with no documented syntax**, which is a claim both projects' sources
support and neither of the earlier framings did.

### `NODE0`, and a second defect in the same table

The cross-check flagged a range endpoint, which a word-for-word diff of entries
cannot surface: their Desigo-era source says `NODE0 through NODE99`, the
Program Editor's reserved-word page says `NODE1 through NODE99`.

It is not an edition difference. It is **internal to one book**:

| Source | Says |
|---|---|
| Program Editor reserved-word page | `NODE1 through NODE99` |
| Program Editor **dedicated node-points page** | `NODE0 through NODE99`, in the title, and in prose twice — "range from 0 through 99", "between 0 and 99" |
| Program Editor glossary | `NODE0 through NODE99` |
| PPCL Debugger help | `NODE0 through NODE99` |
| Desigo CC engineering help | `NODE0 through NODE99` |

Four to one, and the one is contradicted by a dedicated page in its own book.
`RESERVED_WORDS` already generated `NODE0`–`NODE99` and was right; it now has a
test saying why, because this is exactly the kind of thing a later reader
"corrects" from the reserved-word list.

Worth naming the pattern: that single table is now the sole source for `LESS`
**and** wrong about `NODE`. A page can be uniquely right about one fact and
wrong about another. Neither observation licenses a verdict on the other.

### Open question 10, measured

The cross-check answered it without needing PPCL at all — point names are on
the wire independently of any program referencing them. **3,025 distinct names,
960,469 occurrences, zero containing a dotted-operator segment.**

The near misses are the finding. Reserved words do appear as dotted name
segments — `MIN`, `ALARM`, `OFF` — but none of those is a dotted *operator*, so
splitting on `DOTTED_OPS` leaves them alone, which is what the tests added last
pass assert. One name carries `ROOT`, and `.ROOT.` **is** an operator; it
survives only because `ROOT` is that name's last segment, so the trailing dot
never appears. A name one component longer is the ambiguous case.

So: a negative from one site rather than a proof, with a margin of one naming
decision. It stays a documented hazard with a stated fix — quote the name —
rather than a lint rule that would fire zero times on every corpus anyone has.

### Their correction to the resident-points list, accepted back

The classification sent over held, and their recount strengthened it: their
"observed here" figures had come from a word-boundary match, and in a
word-boundary match a dot is a boundary — so a word appearing as a dotted
segment of a point *name* counted as a reference to the resident *value*. The
same trap this project hit in the lexer, met in a counting script. Recounted by
syntactic position, `LOW` fell from 28 to 4, all four inside quoted names, and
`ALMCNT`'s eight were comment prose. Four of the twelve are neither resident
points nor observed as values.

### Still to do

1. **Decide `W104`.** Unchanged, and still the user's call.
2. `W330` fires 340 times on the Siemens library. Still unexamined.
3. Handle the `UNKNOWN (...)` marker in the lexer.
4. A rule for the `SET`-without-deadband idiom on PXC.A.

---

## 2026-09-18 (seventeenth pass) — what "PPCL" is called, and a corpus counted twice

Two corrections, both from the user and both mine.

### It is not "APOGEE PPCL"

The repository described itself as tooling for "Siemens APOGEE PPCL" — in the
README, the skill description, the CLI's own `--help`, and the public repository
description. The user pushed back: PPCL runs on their newer panels too.

They are right, and Siemens' own documents say so plainly:

| Document | Year | What it calls the language |
|---|---|---|
| *APOGEE Powers Process Control Language (PPCL) User's Manual*, 125-1896 | 2000 | "APOGEE **Powers** Process Control Language" |
| *Proprietary Program Control Language (PPCL) User Manual*, A6V10374898 | PXC.A era | "**Proprietary** Program Control Language" — the expansion itself changed |
| *PXC.A Reference Manual*, A6V12954388 | 04/2025 | plain **PPCL**, and sends a Desigo PXC4/5/7.A engineer to "the **PPCL Users Manual (125-1896)**" — dropping APOGEE from the citation |
| PXC.A Modernization guide, A6V13998441 | 2026 | plain **PPCL**, describing conversion of PPCL *forward* onto PXC.A from MBC/MEC and BACnet PXC panels |
| Desigo CC engineering help | current | "**APOGEE PPCL Editor**" — 420 occurrences, but this is the name of an *editor module* under "APOGEE BACnet Applications", not of the language |

So the only name stable across twenty-five years of Siemens documentation is
**PPCL**. A 2025 manual for current Desigo hardware cites the 2000 manual as
the language reference, and the 2026 modernization guide moves PPCL *onto* the
newest panels rather than off them. Calling the language "APOGEE PPCL" reads it
as legacy, which is wrong in a way that matters to anyone deciding whether the
tool applies to their site.

Corrected in the README, `CLAUDE.md`, the skill, `cli.py --help`,
`spec.PROGRAM_NAME_MAX`'s comment (which also mis-cited the source — the
30-character limit is from the Desigo help and is not generation-specific), and
the public repository description. **Left alone** where the phrase is a proper
noun: the manual's own title, and Desigo CC's "APOGEE PPCL Editor". `apogee`
stays a `Firmware` member, because there it names a firmware family correctly.

### The Siemens library is 42 programs, not 84

Going after `W330`'s "340 hits" found the count before it found the rule.

The library ships in **two product trees** — one under the Insight tree, one
under the Datamate Advanced product tree — and the first collection of it took
both, naming the second copy of each file `__dup1`. All 42 pairs are
byte-identical. So the regression corpus was 42 programs counted twice, and
every absolute figure taken from it was exactly doubled.

| Published | Actual |
|---|---|
| 84 programs | **42** |
| 15,726 lines | **7,863** |
| 15,716 parsed / 10 failures | **7,858 / 5** |
| 146 lines failed before the parser fixes | **73** |
| `W104` 2,134 of 3,040 warnings | **1,067 of 1,520** |
| `W330` 340 | **170** |

**Every ratio is unchanged** — 99.94% parse rate, 70% `W104` share — because
doubling a corpus doubles both sides of a fraction. No conclusion drawn from
this corpus moves. Only the counts were wrong, and they were wrong in the
public repository, which is the part that matters.

Pass 14 is left as written with a correction note at its head. Rewriting it
would erase the record of the mistake, which is the more useful artifact.

### `W330`, now that the count is real

Not a false positive, and not one finding. All **170** hits are `@OPER` — the
operator's own priority — and by write coverage they fall into three groups:

| | | |
|---|---|---|
| Written **unconditionally** every pass | 134 | 79% |
| Written on **both branches** of one `IF` | 21 | 12% |
| **Conditional only** — can strand | 15 | 9% |

The 91% are programs that drive a point at `@OPER` continuously: a lamp-test
utility that turns on every FLN indicator and is meant to be left that way, and
`IF(c) THEN ON(@OPER,X) ELSE OFF(@OPER,X)` pairs that cover both states. Those
cannot strand a point — something writes it every pass — but they do mean an
operator's own command is overwritten on the next pass, which the rule's
current text does not say.

Only the 9% match what `W330` actually claims: commanded above `NONE` on a path
that ends, with nothing to release it.

One rule, two conditions, one message. **Next: split it.**

### Still to do

1. **Decide `W104`.** Unchanged, and still the user's call. The count moved;
   the 70% did not.
2. Split `W330` by write coverage.
3. Handle the `UNKNOWN (...)` marker in the lexer.
4. A rule for the `SET`-without-deadband idiom on PXC.A.

---

## 2026-09-18 (eighteenth pass) — splitting `W330`

`W330` said one thing about two different situations, and the library made that
obvious once the count was right.

### The two shapes

A point commanded above `@NONE` with no `RELEAS` anywhere is either:

1. **written on a path that ends** — the condition clears, nothing rewrites the
   point, and it sits at that priority until a person finds it. This is what
   `W330`'s text describes and it is a genuine defect; or
2. **written on every pass** — unconditionally, or on both branches of one
   `IF`, on a line that lies on a cycle of the control-flow graph. The point
   cannot strand, because something writes it again in a second or two.

Shape 2 is not harmless, but the harm is different: an operator's own command,
a schedule, or another program is **overwritten on the next pass**, silently.
That is deliberate for a lamp test or a hard interlock and a nasty surprise
otherwise. Telling an engineer their point "is never released" when the real
problem is "you cannot take this point at the head end" sends them looking for
the wrong thing.

### `W341`

New rule, `INFO`: *point held above PPCL priority on every pass*. Message says
what is actually true — the point cannot be kept by an operator — and the
suggestion is the real fix, command it at `@NONE` and let priority arbitration
do its job. `INFO` rather than `WARNING` because both legitimate uses are
common and the rule cannot tell them apart.

`W330` keeps shape 1 as a `WARNING`, with its original text, which is now
accurate for every case it fires on.

The every-pass test is deliberately narrow. It requires the line to be in
`analysis.steady_state` and not in `analysis.subroutine_lines`, and it excludes
`SAMPLE`-wrapped statements, because making a statement run *less* often than
every pass is the entire purpose of `SAMPLE`. A write that only happens inside
a subroutine, or only once at startup, stays a `W330` — it can strand.

### What it does to the two corpora

| Corpus | was `W330` | now `W330` | now `W341` |
|---|---|---|---|
| Siemens' 42-program library | 170 | **22** | **148** |
| The reference site | 11 | **4** | **7** |

The narrow test moved 148 rather than the 155 a rough by-hand pass predicted;
the seven-hit difference is writes that look unconditional but sit in a
subroutine body or a run-once region, which the rule correctly leaves as
strandable.

### And it moved decision 1

Taking 148 findings out of `WARNING` raises `W104`'s share of the library's
warnings from 70% to **78%**. The argument for regrading `W104` did not weaken
when a competing rule was thinned out — it got stronger.

### Still to do

1. **Decide `W104`.** Now 78% of the library's warnings. Still the user's call.
2. Handle the `UNKNOWN (...)` marker in the lexer.
3. A rule for the `SET`-without-deadband idiom on PXC.A.

---

## 2026-09-18 (nineteenth pass) — `PROTOCOL.md`, and five statements nobody can document

The user handed over the other project's own technical reference — 11,216
lines, §14 of which is PPCL over the wire. It had not been read here; the
earlier passes worked from a cross-check *derived* from it.

### Five statement tokens the firmware names and no manual documents

The document reproduces the controller's `PPCL_statement_type` enum in full:
**71 members**, `WHOP`-prefixed, the token byte the panel stores with each
compiled statement. Diffed mechanically against `spec.ALL`:

- 60 map onto commands already here, once the vendor's token spellings are
  reduced to source spellings (`WHOPDBSWITCH` → `DBSWIT`, `WHOPINITTOT` →
  `INITTO`, `WHOPRELEASE` → `RELEAS`, `WHOPDISABLE` → `DISABL`);
- 6 are `IF` parts, `ASSIGN`, `COMMENT` and declarations;
- 2 are explicitly unnamed — `WHOPUNKNOWN1` and `WHOPUNKNOWN2`, values 61 and
  62. The vendor's own table records that it is incomplete;
- **5 are left over: `ONERR` (21), `ENTHAL` (48), `MMI` (49), `RELTCU` (60),
  `DIM` (68).**

Every manual on hand was then searched for those five — the Insight Program
Editor help, the Desigo CC engineering and operating help, 125-1896,
A6V10374898, A6V12954388, A6V10324350, A6V13998441. **Not one mention.** Nor
any use: zero occurrences across Siemens' shipped library, the reference site's
programs, and the 2,644 wire-recovered lines.

The reverse direction is clean and explains itself: seven commands here are
absent from the enum — `ADAPTM`, `ADAPTS`, `LSQ2`, `LSQDAT` (firmware 2.7+,
from Command Assist), `GETVAL`, `SETVAL` (PXC.A, which postdates this enum),
and `OIP`.

`OIP` is the instructive one. It is used **152 times** across that project's
corpus and is missing from the same 71-entry table. **Absence from a vendor
enum is evidence of nothing.** It is the second time that has been shown here,
and it cuts both ways: the five leftovers are real because the enum names them,
and `OIP` is real although the enum does not.

### What the toolkit does with them

Before this pass, `ENTHAL(A,B,C)` produced `E110 ERROR — ENTHAL is not a PPCL
command`. That is confidently wrong about the vendor's own firmware, which is
the worst kind of diagnostic.

They are recorded in `spec.FIRMWARE_STATEMENT_TOKENS` rather than `spec.ALL`,
deliberately: **a name whose arguments nobody can state must not be offered by
completion or emitted by the generator.** `E110` now skips them, and `W121`
says the honest thing — the token is real, its arguments and behaviour are
unknown, and this line was left unvalidated. `ppcl explain ONERR` answers with
exactly what is and is not known.

### `report.py` was right, and now it is confirmed

`report.py` models the `PPCL DISPLAY REPORT` state column as five flags —
enabled/disabled, traced, unresolved, failed, looped — read out of a column of
letters in the Program Editor help.

§14.5.1 gives the controller's `PPCL_data` record as it comes off the wire:
`line_enabled`, `line_traced`, `line_unresolved`, `line_failed`, `line_looped`.
**The same five booleans, in the same order.** The model is complete and it is
the panel's own, not an inference from a report layout.

### Confirmed in passing, from the manual side

- **No PPCL statement keyword exceeds six characters.** That project measured
  it over 4,731 statement lines and found six a hard ceiling. Independently:
  no name in `spec.ALL` is longer than six characters either.
- **`ACT`/`DEACT`/`ENABLE`/`DISABL` act on 1 to 16 lines.** Already the
  `max_repeat` here; the wire-side range opcodes carry the same bound.
- **The `@`-indicator consumes one of the sixteen parameter slots.** Already
  how `E111` and `E314` count.

### To send back

1. **`NODE0` is settled, not a live disagreement.** §14.3 records the Insight
   list's `NODE1` against the Desigo glossary's `NODE0` as unresolved. It is
   resolvable *inside the Insight book*: that book's own dedicated node-points
   page states the range in prose twice — "from 0 through 99", "between 0 and
   99" — and carries `NODE0` in its title, as do its glossary and the PPCL
   Debugger help. Four pages to one, three of them in the same book as the
   outlier. The conclusion they reached on an asymmetry argument is right; the
   evidence is stronger than they know.
2. **`OIP`'s `[OPEN]` can be closed.** The Insight Program Editor documents it
   fully: `OIP(trigger, "sequence")`, the sequence being a quoted keystroke
   string of at most 80 characters, one `/` per menu level and a `/` acting as
   a carriage return. Used for reports, point-priority changes, messages and
   auto-dial. The trigger must go OFF then ON; it will not fire on the first
   pass after a power failure, an `ENABLE`, or a database load. Stagger them
   and never share a trigger. And the leading-`@` rule is asymmetric: a trigger
   whose name starts with a digit needs one, a point named *inside* the
   sequence must not have one. A6V10374898 removes the statement outright on
   PXC.A, in those words.
3. **`LN` is not a PPCL function.** §14.4's built-in list has `LN` for natural
   log. It appears in no manual here and is not in the enumerated reserved-word
   list, which carries `LOG` and `EXP` and no `LN`. The same list that settled
   `EQUAL` and `LESS` is the one to check it against.

### Still to do

1. **Decide `W104`.** Now 78% of the library's warnings. Still the user's call.
2. Handle the `UNKNOWN (...)` marker in the lexer.
3. A rule for the `SET`-without-deadband idiom on PXC.A.

### Postscript to pass 19 — the point-type enum, and a field nobody read

The same appendix carries `Point_type_enum`. Diffed against `POINT_TYPES` it
named five this toolkit did not have: `LDAO`, `LENUM`, `LFMSSL`, `LFMSSP`,
`PPCL_LAI`. Two of them turned out to be documented, in pages that had been
read and skimmed past.

**`LFMSSL` and `LFMSSP` are three-speed.** Every page that names a speed type
in the Insight Program Editor help names four, not two:

> *"This command is used to change **LFSSL, LFSSP, LFMSSL, or LFMSSP** points
> to the FAST state with emergency priority."*

— on `EMFAST`, and the same on `EMSLOW`, `FAST` and `SLOW`, and on the `FAST`,
`SLOW` and `OFF` status-indicator pages. The Point Details help defines
`LFMSSL` outright: *four-state control (Fast/Medium/Slow/Stop) of three-speed
latched motor starters that provide proof indication.*

125-1896 Rev. 5 names only two, everywhere, and titles its table "The 11 Point
Types". The enum puts the pair at 22 and 23, after the original block. So this
is a generational addition of the same shape as the operand limit — the 2000
manual is not wrong, it is earlier.

Both are in `POINT_TYPES` and `SPEED_TYPES` now. Their address organisation is
**inferred** from the two-speed siblings and says so, because no published
table covers them.

A smaller thing found in passing: the Commanding help's `LFSSL` page describes
three latched outputs *including a Medium*, which is `LFMSSL`'s organisation.
Table 3-2 is unambiguous that `LFSSL` has two outputs and a proof. The help
page is a copy-paste.

**And then the rule that was not there.** The natural next step was to check
what this had been breaking — and the answer was nothing, because
`spec.Command.point_types` had never been read by any rule. `AUTO` has carried
`{LOOAL, LOOAP}` since the beginning, transcribed from *"can be used only with
LOOAL or LOOAP points"*, and nothing checked it. It was documentation nobody
could be wrong about, which is documentation nobody checks.

**`E316`** now enforces it, and only when the point's type is actually known:
no point database, no finding, because guessing a type from a name would be
worse than silence. A type outside `POINT_TYPES` is left alone rather than
guessed at.

That is the more valuable find of the two. The missing point types were a
one-line gap; the unenforced field had been quietly inert across eighty rules.

`LDAO`, `LENUM` and `PPCL_LAI` are not added: `LENUM` appears only in the
alarm and commanding help, never in a PPCL context, and the other two appear
nowhere at all. The lesson from `ONERR` applies in reverse here — an enum
naming something is not yet a reason to put it in the language spec.

---

## 2026-09-18 (twentieth pass) — SSTO's learned state, and an 80 that was mine

### `W342` — the panel writes its own learned state into the program you read back

Chasing SSTO through `PROTOCOL.md` §15.3.1 landed on a sentence in the Program
Editor's `SSTO` page that had been transcribed into `spec.py` as a note and
never acted on:

> *"When AST or ASP are entered as zero, the current adjustment value is
> displayed each time the command is displayed."*

`SSTO` self-tunes. It carries a start adjustment and a stop adjustment day to
day, nudged by `SSTOCO`'s `coef4` whenever the zone misses its target. Enter
zero and the panel keeps that adjustment internally — **and prints it into the
statement every time the statement is displayed.**

So a program *exported from a panel* carries a number in that slot that nobody
typed. Load that text back and the zone stops tuning from zero and starts
tuning from whatever the panel had learned on export day. The program looks
identical and behaves differently, which is the exact failure this toolkit
exists to catch.

**It is not hypothetical.** Linting the reference site's older programs found
**four** of them, in one file: two `SSTO` statements carrying a four-decimal
literal in `AST` -- of the shape `87.4416`, invented here, because the real
ones are the site's -- and a negated literal in `ASP`. Four decimal places is
not something an engineer types into a start-time adjustment.

`W342` fires on a non-zero numeric literal in either slot and stays quiet on a
zero or a point reference — a virtual LAO is the documented way to seed an
adjustment deliberately, and that is a choice, not an accident. Zero hits on
Siemens' library and on the Desigo-era exports; four on the older corpus.

### `OIP`'s sequence limit is 60, and the 80 was ours

The other project pushed back on the second-round reply: every `OIP` page in
their copy says 60. They are right.

| Source | |
|---|---|
| 125-1896 Rev. 5, Ch. 4, `OIP` | *"The sequence must not exceed 60 characters (including slashes) in length."* |
| Program Editor, `Statement Arguments — OIP` | the same sentence, verbatim |
| A third-party syntax reference transcribing the manual | the same sentence again |

Every corpus here was searched for an `80` in any `OIP` or sequence context.
There is none. This is not a two-source split of the 13-vs-16 shape — it is a
single-source figure and **the source was this project**, confusing it with the
80-character MMI line length. That figure had been in `spec.py` since the first
pass and was sent to the other project as authoritative.

Corrected, and made harder to get wrong: the limit is now
`spec.OIP_SEQUENCE_MAX`, so the parameter documentation and the rule read the
same constant. **`E122`** enforces it, at ERROR, because `OIP` validates its
sequence *when the trigger fires* rather than when the line is entered — an
over-long sequence shows as `FAILED` on the panel and nowhere else.

One piece of corroboration: across the three program corpora there are **158
`OIP` statements** and the longest sequence is **56** characters. If the ceiling
were 80, some of 158 would be expected in the 60–80 band. None is.

### `ALMPRI`, and which enumeration you checked

They noted `ALMPRI` is not on the enumerated reserved-word list. True of the
**Program Editor's** list; it *is* on **125-1896 Chapter 5's**, as a bare cell
in alphabetical position, along with `DEAD` and `HAND`.

The two enumerations differ in both directions. Twelve words are on 125-1896's
and not the Program Editor's — `ALMACK`, `ALMPRI`, `DEAD`, `DEFINE`, `DISCOV`,
`ENCOV`, `HAND`, `LOCAL`, `LOW`, `NOR`, `OK`, `STATE` — and `LESS` is on the
Program Editor's and not 125-1896's. Neither is complete, so a word's absence
from one means nothing by itself. Same lesson as `NODE0`, from the other side.

### Still to do

1. **Decide `W104`.** Still the user's call.
2. Handle the `UNKNOWN (...)` marker in the lexer.
3. A rule for the `SET`-without-deadband idiom on PXC.A.

---

## 2026-09-18 (twenty-first pass) — clearing the PXC.A queue

Two items had been on the "still to do" list since pass 12, both from
A6V12893115. Both were re-fetched and verified word for word before anything
was built, because the first is a token and the second is a rule.

### `E123` — the compiler writes a defect into your program and it reads as fine

> **UNKNOWN Marker.** "Any unknown PPCL commands will be added with an
> UNKNOWN (...) marker and ignored by the compiler upon saving a program.
> Correct or discard unknown commands and save the program again."

This is the rarest thing in the language and the easiest to read past. The
engineer's original command is **still visible on the line**. Nothing about the
text says the panel has given up on it. The statement is in the program, and
nothing executes it.

The parser now recognises `UNKNOWN (...)` — with or without the space, since
the guide prints one and an editor may not keep it — and **does not parse what
is inside.** Whatever is in there is text the compiler already rejected;
parsing it produces a cascade of syntax errors about a line that has already
failed. Before this change, `UNKNOWN (SETPT(ZNTEMP,72.0))` produced `E100`
about a misplaced comma, and `UNKNOWN(FOO)` produced `E110` saying UNKNOWN is
not a PPCL command. Both true and both useless.

`Line.unknown` joins `Line.disabled`, and `is_executable` is False for either
— so the line drops out of the control-flow graph exactly as the panel drops
it. `E123` reports it at ERROR and shows the wrapped text.

### `W343` — SET is the RELEAS storm, a generation later

> **Continuously re-commanded points.** "The SET command is not always
> resolved to the actual value, which may result in points being continuously
> commanded. To prevent unnecessary MS/TP traffic, use a deadband."

with a worked example that computes the difference between a point and the
wanted value into a local, and `SET`s only when that difference leaves a
deadband local.

This is `W339` one generation on: a command that is logically a no-op and costs
network traffic every pass. `W343` is `INFO` for the same reason `W339` is — it
is a cost, not a defect, and on a small trunk it may not matter.

**Deliberately narrow.** It fires only on a `SET` with *no* conditional guard
at all, on a line the main loop reaches every pass, and only on `pxc_a`. A
`SET` already inside an `IF` is left alone even when the condition is not a
deadband, because past that point the engineer has decided when it should run
and the rule cannot read the decision.

For scale, if the gate did not exist: 27 unconditional every-pass `SET`s in
Siemens' library, 18 in the reference site's Desigo exports, 138 in its older
programs. All of those are APOGEE, so all of them stay quiet — which is the
point of gating it.

### What the same page also gives

Two things worth recording that need no code:

- **"DEFINE and LOCAL statements are always traced."** Already honoured by
  `R703`, which must never conclude dead code from a cleared trace bit on one.
- **Per-program cycle time** — average over the last ten cycles, highest and
  lowest, in milliseconds, **mappable to virtual points and trendable**. This
  makes open question 2, the real line-evaluation rate on current hardware,
  answerable from a live panel rather than from the manual's twenty-five-year
  -old averages. It is the cheapest of the open questions to close and needs
  no lab: map the three metrics to virtual points on a panel in service and
  trend them.

### Still to do

1. **Decide `W104`.** Still the user's call, and now 78% of the library's
   warnings.
2. A rule noting `ACT`/`DEACT`/`ENABLE`/`DISABL` cost system resources on
   PXC.A — the last item from pass 12.
