# Adding PPCL support to a text editor

A self-contained specification. Everything needed to lex, highlight and
usefully check Siemens PPCL is in this one file — no need to read or clone the
rest of this repository.

PPCL (Powers Process Control Language) is the sequence language for Siemens
APOGEE and PXC field panels. It is edited today through Desigo, and the text
form is what gets exported, diffed and mailed around.

Scope: what an editor can do cheaply and get right. The traps in §4 are the
ones that actually bite; three of them caused real defects in this project
before they were understood.

---

## 1. What a file looks like

```
10    C AHU-1 SUPPLY FAN
20    IF(TIME .GT. 6.00 .AND. TIME .LT. 18.00) THEN ON("SFAN") ELSE OFF("SFAN")
30    IF("SAT" .GT. 80.0) THEN "CLG" = 100.0
40    GOTO 10
```

A line is **a number, whitespace, then one statement**. That is the whole
grammar at the top level — PPCL has no blocks, no indentation, no nesting.

- **Line numbers** run 1 to 32767 and must **ascend**. They are the only
  labels; `GOTO`/`GOSUB` take a line number, never a name.
- Exports commonly write them **zero-padded to five digits followed by a tab**
  (`00350\tC ...`). Accept both that and plain `10 ` with spaces.
- Execution is **top to bottom, then wraps from the last line back to the
  first**, forever. There is no entry point and no exit.
- **Comments**: a `C` (or lowercase `c`) as the first token after the line
  number. Everything after it is prose. It is not a line prefix character —
  `10 C text` is a comment, `10 CLG = 1` is an assignment.
- **Continuation**: a trailing `&` joins the next line into one statement.
- **Disabled line**: on PXC.A only, a literal `# ` at the very front of the
  line, before the line number. On older panels the disabled state is **not in
  the text at all** — it lives in the panel. Do not invent a representation.
- **`UNKNOWN (...)`**: written by the compiler around a statement it could not
  resolve. The line is kept and ignored by the panel. Treat it as inert.

### Statement forms

There are only five.

| Form | Example |
|---|---|
| Command call | `ON("SFAN")`, `SET(72.0,"SP")` |
| Assignment | `"CLG" = 100.0`, `$LOC1 = X + 1` |
| Conditional | `IF(cond) THEN stmt ELSE stmt` |
| Branch | `GOTO 400`, `GOSUB 900`, `RETURN` |
| Comment | `C anything` |

`IF` takes a single statement for `THEN` and a single statement for `ELSE` —
no blocks, no nesting of `IF` inside `IF`.

---

## 2. Token lists

Complete and current. Matching should be **case-insensitive** and on **whole
words**.

### Commands (66)

```
ACT ADAPTM ADAPTS ALARM AUTO DAY DBSWIT DC DCR DEACT DEFINE DISABL
DISALM DISCOV DPHONE EMAUTO EMFAST EMOFF EMON EMSET EMSLOW ENABLE ENALM
ENCOV EPHONE FAST GETVAL GOSUB GOTO HLIMIT HOLIDA INITTO LLIMIT LOCAL
LOOP LSQ2 LSQDAT LSTSQR MAX MIN NIGHT NORMAL OFF OIP ON ONPWRT PDL
PDLDAT PDLDPG PDLMTR PDLSET RELEAS RETURN SAMPLE SET SETVAL SLOW SSTO
SSTOCO STATE TABLE TIMAVG TOD TODMOD TODSET WAIT
```

### Functions (11)

```
ALMPRI ATN COM COS EXP LN LOG SIN SQRT TAN TOTAL
```

All take one argument. **`LOG` is the common log, base 10** — not the natural
log. `LN` is the natural log. (`EXP`'s base is genuinely unestablished; do not
assume it inverts `LOG`.)

### Operators (11)

```
.AND. .EQ. .GE. .GT. .LE. .LT. .NAND. .NE. .OR. .ROOT. .XOR.
```

**This list is exact and closed.** There is no `.NOT.`, no `.EQUAL.`, no
`.LESS.`. Arithmetic is the ordinary `+ - * / =` plus `.ROOT.`.

### Priorities (5)

```
@NONE  @PDL  @EMER  @SMOKE  @OPER
```

Lowest to highest, in that order. `@OPER` is the highest and a program cannot
command past it. These five are the complete set a program may write. You may
also see `OVRD` in a *point's* priority field — that is an operator override
applied at a workstation, not something PPCL can name.

### Status indicators (16)

Used on the right of a comparison: `IF("FAN" .EQ. ON) ...`

```
ALARM ALMACK AUTO DAYMOD DEAD FAILED FAST HAND LOW NGTMOD OFF OK ON
PRFON SLOW TROUBL
```

### Resident points

System-maintained, always present, no declaration:

```
$BATT $PDL $TOTKW ALMCNT ALMCT2 CRTIME DAY DAYOFM LINK MONTH SECNDS TIME
```

Plus generated ranges:

- `NODE0` … `NODE99` — peer panel online status
- `SECND1` … `SECND7`
- `$LOC1` … `$LOC15` — general-purpose locals
- `$ARG1` … `$ARG15` — subroutine arguments

### Structural keywords

```
IF THEN ELSE C PARAMETER
```

---

## 3. Lexing

### Delimiters

```
space  ,  :  (  .  +  -  *  /  =
```

Note `)` is **not** a delimiter, and `.` is — which is the source of trap 4.1.

### Name forms

| Form | Meaning |
|---|---|
| `ABC123` | bare point name — only if ≤ 6 chars and only `A-Z0-9` |
| `"Bldg.Ahu01.Sfan"` | quoted point name — required above 6 chars or with any other character |
| `$LOC1`, `$ARG3` | local |
| `@OPER` | priority |
| `%MACRO%` | reference to a `DEFINE`d abbreviation |
| `80.0`, `6.00` | number; a bare `6.00` in a time context is decimal hours |

---

## 4. The four traps

These are the ones worth getting right. Each produced a real bug here.

### 4.1 The dot

`.` is a delimiter *and* a decimal point *and* a legal character inside a point
name. Three cases that must lex differently:

```
IF(X .EQ. 1)          ->  .EQ. is an operator
X = 80.0              ->  80.0 is one number
ON("TOWER.FAN.SPEED") ->  one name, dots included
```

The rule: **only the eleven dotted operators split a token.** Everything else
with dots in it stays whole.

The failure has a mirror image, and you need both:

- `"ROOM.MIN.TEMP"` — contains `.MIN.`, which is *not* in the operator list,
  so it must stay one name. Matching command names between dots breaks this.
- `"A.ROOT.B"` — contains `.ROOT.`, which *is* an operator, so it does split.

So: match the eleven operators literally, not "a dot, a word, a dot".

### 4.2 The OIP keystroke string

```
OIP(TRIG,"P/T/D/H///SITE.TOWER.AHU01.SFAN/1/")
```

The quoted argument is **one token** — an operator keystroke sequence, with `/`
as its internal field separator. A point name may appear *inside* it as one
field.

Do not split it, do not treat `/` as division, do not rename through it as if
it were a name. Siemens' own database converter gives up here and tells the
engineer to edit these by hand. The sequence has a 60-character limit.

### 4.3 Quoted spans come first

Anything inside `"` is literal. Run the quoted-span scan **before** any
keyword, operator or name matching — otherwise a command name appearing inside
a point name or an OIP string gets highlighted as a command.

### 4.4 `C` is a token, not a column

`10 C text` is a comment. `10 CLG = 1` is not. Require whitespace after the
`C`. Both cases appear in real files, and lowercase `c` is used too.

---

## 5. Highlighting

A workable scope mapping:

| Element | Scope |
|---|---|
| Line number | `constant.numeric` |
| `C` and comment text | `comment.line` |
| `IF THEN ELSE GOTO GOSUB RETURN` | `keyword.control` |
| Command names | `support.function` |
| Functions | `support.function` |
| `.EQ.` etc | `keyword.operator` |
| `@NONE`…`@OPER` | `constant.language` |
| `ON OFF ALARM`… | `constant.language` |
| Quoted point names | `string.quoted.double` |
| `$LOC1`, `$ARG1` | `variable.language` |
| Resident points | `variable.language` |
| `%MACRO%` | `variable.other` |
| Numbers | `constant.numeric` |

Worth highlighting as **errors**, because they are cheap and unambiguous:

- a `GOTO` or `GOSUB` not followed by a bare line number
- an unterminated quoted string
- a line number that is not the first token

---

## 6. Checks worth implementing

In order of value per line of code. The first four need no parser at all.

### Tier 1 — line-number checks, regex only

1. **Duplicate line numbers.** Common in real files, and it makes any
   renumbering ambiguous. Report both lines.
2. **Line numbers not ascending.** The compiler refuses this outright.
3. **Branch to a line that does not exist.** Collect every line number, then
   every `GOTO`/`GOSUB` target, and diff. Worth knowing: the panel sends a
   branch aimed at a missing line on to the *next* line after it, so this is
   a warning about fragility, not always a failure.
4. **Line length.** Only matters if the program will be typed at a panel's
   serial port — loading from a workstation is unaffected, so make it an
   option, off by default. Limits: **66** characters on APOGEE, **72** on the
   older families, **512** on PXC.A.

### Tier 2 — needs tokenising, still no parse tree

5. **Unbalanced parentheses.**
6. **Unquoted name longer than 6 characters**, or containing anything outside
   `A-Z0-9`. Must be quoted.
7. **Unknown command name.** Use the list in §2. Be careful: four tokens
   (`ONERR`, `ENTHAL`, `RELTCU`, `DIM`) exist in panel firmware but are in no
   manual — report them as unchecked rather than wrong.
8. **Operand count.** Maximum **16** per statement on APOGEE, PXC.A and CM;
   **13** on the physical, logical and unitary families. An `@priority`
   consumes one of those slots, so `ON` takes 16 points without one and 15
   with.
9. **Operator count.** Maximum **32** per statement.
10. **Two `DEFINE`s of the same abbreviation.** The compiler refuses it, and
    the dangerous case is silent: the second wins, so uses written *above* it
    resolve to the later point.
11. **An unsubstituted library template.** Siemens ships its application
    library as templates, not programs. A block of comments near the top
    declares each editable name between backslashes — a description, then the
    token, then a close — and the engineer substitutes them before loading.
    Collect the tokens, then check whether any still appear in executable
    lines; if so the file is not a program yet. Worth doing because an
    unsubstituted token looks exactly like an ordinary point name — most are
    six characters or fewer and pass every other check. Three shapes reach the
    code: bare, quoted, and behind a `%PREFIX%` macro, so match the token with
    word boundaries rather than walking a parse tree. Report once per file.
    The prompt block's own header lines carry only an opening and closing
    backslash with no token field — do not read those as declarations.

### Tier 3 — needs control flow

11. **More than one backward `GOTO`.** Exactly one is permitted and it must be
    the last — it is what closes the main loop. On PXC.A it also marks the end
    of the program cycle.
12. **A time-based command that is not reached every pass.** `TIMAVG`, `TOD`,
    `SSTO` and friends must be evaluated on every scan; putting one inside an
    `IF` or a subroutine silently breaks it.

### Things not to report

- A backward `GOTO` as the last statement — that is the idiom, not a defect.
- A comment-only program, or a very high comment ratio. Normal here.
- `DEFINE` abbreviations that have no matching use in the same file — they are
  per-program and may legitimately be defined elsewhere in the panel.

---

## 7. Things an editor should not try to do

- **Do not renumber through a pretty-printer.** Renumbering must rewrite line
  references at the token level and leave every statement body byte for byte
  intact. A round trip through a formatter will quietly change code that a
  panel is running.
- **Do not guess at a disabled line.** On anything older than PXC.A the state
  is not in the file.
- **Do not auto-correct a point name.** A name that looks wrong may be
  resolved by a `DEFINE` elsewhere in the panel, or may be a deliberate
  reference to another panel's point.
- **Do not reformat `OIP` strings.** See 4.2.

---

## 8. If you want deeper checking later

The rest of this repository is a zero-dependency Python package implementing
all of the above and considerably more — 90 lint rules, a simulator with real
priority arbitration, and a renumberer with exact reference rewriting. Two ways
to use it from an editor without embedding it:

```bash
python -m ppcl.cli lint <file> --format json
```

Add `--workstation-only` if the programs are only ever loaded from a
workstation and never typed at a panel port -- it drops the line-length
findings of §6 tier 1 item 4, which are usually the bulk of the noise.

There is also an MCP server (`python -m ppcl.mcp_server`) exposing the same
analysis to an agent. Neither is required for anything in §§1–7.

---

## 9. Provenance

Token lists and limits here are transcribed from the Siemens PPCL manual
(125-1896) and the current Desigo engineering help, and were reconciled
name-for-name against the dispatch tables the shipped compiler itself carries.
The lexing traps in §4 come from parsing real programs: Siemens' own 42-program
application library, a set of programs exported from a live supervisor, and
2,644 lines recovered from panels over the wire by a separate project.

Two things on this page are explicitly *not* settled, and are marked where they
appear: the base of `EXP`, and whether a bare `RELEAS` clears `@SMOKE`.
