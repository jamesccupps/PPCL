# PPCL Workbench — project context

> **Starting a fresh session? Read [HANDOFF.md](HANDOFF.md) first.** It carries
> the session state, research provenance, what is verified versus approximated,
> the honest gap list, and the prioritised roadmap. This file is conventions
> and invariants only.

Tooling for Siemens PPCL: parse, lint, analyse, simulate, renumber,
generate, redact. Python 3.10+, **stdlib only, zero dependencies** — deliberate,
so it runs on any engineering workstation without a package install.

## Layout

| Module | Responsibility |
|---|---|
| `ppcl/spec.py` | The language, machine-readable. Command signatures, point types, priorities, precedence, reserved words. **Every entry traces to the manual.** |
| `ppcl/lexer.py` | Tokenizer. The hard part is the dot: `.EQ.` vs `80.0` vs `"A.B.C"`. |
| `ppcl/parser.py` | Error-tolerant recursive descent. A bad line becomes `Unparsed`, never aborts the file. |
| `ppcl/ast_nodes.py` | Flat AST — PPCL has no block structure. |
| `ppcl/analyzer.py` | Control flow (incl. the wrap edge and Tarjan SCC for steady state) and point read/write tracking. |
| `ppcl/linter.py` + `ppcl/rules/` | 88 rules across syntax, flow, semantics, style, performance. |
| `ppcl/formatter.py` | Format and renumber with exact reference rewriting. |
| `ppcl/simulator.py` | Interpreter with real priority arbitration. |
| `ppcl/generator.py` | `Builder` (symbolic labels) plus fixed program templates. |
| `ppcl/redact.py` | Anonymise site-identifying names. |
| `ppcl/sequence/` | Document model, text DSL, and the compiler that turns a decision table into PPCL. |
| `ppcl/blocks/` | Block diagram: `model.py` is data, `catalog.py` is the block language plus its emitters, `compiler.py` decides what becomes a statement and what stays an expression. |
| `ppcl/debug.py` | Breakpoints (line, write, condition), stepping, watch, mid-run override, over the same simulator. |
| `ppcl/transforms.py` | Source edits at the token level: DEFINE expand/collapse, separator swap, clone with rename, enable/disable, comment. |
| `ppcl/points.py` | The panel point database, and the unresolved-reference check that produces Desigo's `U`. |
| `ppcl/report.py` | Reads a panel's `PPCL DISPLAY REPORT`: which lines are disabled, which have an unresolved point, which have ever executed. Exported program text carries none of that. |
| `ppcl/settings.py` | Declared settings with types, ranges and help. Unknown keys are preserved and reported. |
| `ppcl/helpdocs.py` | The in-app documentation, as data. |
| `ppcl/plant/` | Equipment models and the test bench. |
| `ppcl/web/` | JSON API — `api.py` core, `api_ide.py` for blocks/debug/points/settings/transforms/completion/help — and a stdlib server. The UI is ES modules under `static/js/`, no framework, no build step. |
| `ppcl/cli.py` | Subcommands. |
| `ppcl/mcp_server.py` | MCP server over `web.api.dispatch`, stdlib JSON-RPC. Exposes analysis, NOT file writing. |
| `skills/`, `commands/`, `.claude-plugin/`, `.mcp.json` | The repo is also a Claude Code plugin. See `PLUGIN.md`. |

## Invariants — do not break these

- **`spec.py` is manual-derived.** Never add a signature or limit that is not in
  125-1896. If a value is inferred rather than transcribed, say so in a comment.
- **Every diagnostic carries a `manual=` citation** where the manual supports
  it. A finding you cannot cite is a finding the user cannot argue with a
  vendor.
- **Renumbering must be exact.** `formatter.rewrite_references` works at the
  token level so line bodies survive byte for byte. Do not reroute it through
  the unparser.
- **Refuse rather than guess.** Duplicate line numbers make renumbering
  ambiguous, so it refuses by default and makes the user choose. Keep that
  posture for any transform that could silently change what the panel runs.
- **`LOOP` simulation is approximate and must keep saying so at runtime.**
  Siemens does not publish the PID internals.
- **Read-only with respect to building systems.** No panel writes, no network
  code, no BACnet/P2 client. This tool reads and writes files; the engineer
  loads them.
- **The sequence compiler must keep its guarantees.** One backward GOTO and it
  is last; every interlock force has a matching RELEAS at the same priority;
  no time-based command inside an IF or a subroutine. Those are asserted as
  properties of every compiled program in `tests/test_sequence.py`, not as
  string comparisons. Do not weaken them to make a feature fit.
- **Sequence compilation is one way.** Do not add a PPCL-to-document lifter
  that guesses; if it cannot be done faithfully it should not be done.
- **The web server writes files, so its sandbox is load-bearing.** Every path
  goes through `Workspace.resolve`, which rejects absolute paths, parent
  traversal and unexpected extensions. It binds localhost by default. Do not
  add an endpoint that takes a path without resolving it, and do not add shell
  execution or outbound requests.
- **The UI never renders generated code it has not linted.** `seq/compile` and
  `blocks/compile` both return diagnostics alongside the text, and both panes
  show them.
- **The block compiler's cost model is load-bearing.** Values flow as
  expressions and only condense into a local for a reason — fan-out, held
  state, a command that writes into a point, an operand-limit overflow, or a
  block the engineer named. A `LOCAL` statement declares at most sixteen
  names, so the compiler chunks declarations across statements rather than
  refusing the diagram, and a panel evaluates a fixed number of lines per
  second shared across every program it runs. A compiler that spends one local
  and one line per block produces a program nobody will accept. Do not
  "simplify" this into one-local-per-block.
- **Feedback in a diagram is cut on the stateful block's *outgoing* edges, not
  its inputs.** Cutting the inputs would emit the block before the values it
  needs exist. There is a test for it.
- **`ROUTES` is the routing table.** `api_ide` merges into it; nothing may
  route from a private copy, or replacing a handler silently stops working.
- **The debugger's `run` has a budget and leaves the session where it stopped.**
  A person is waiting; pressing Run again continues.
- **The MCP server never exposes a file-writing endpoint.** `/api/save`,
  `/api/open` and `/api/files` are deliberately absent from `TOOLS`; an agent
  analyses and generates, the person decides what is written. There is a test.
- **Firmware is not cosmetic.** `pxc_a` allows 512-character lines, treats a
  backward `GOTO` as the end of the program cycle, and rejects the ten
  statements in `spec.PXC_A_REMOVED` -- including `ONPWRT`, because it has no
  warmstart at all.
  Any new `Firmware` member must be added to `MMI_LINE_LIMIT`,
  `MMI_CONTINUATION_LIMIT` and `MAX_OPERANDS` or the rules raise `KeyError` at
  lint time -- there is a test that walks every member.
- **How a disabled line is represented depends on the generation, and neither
  form may be invented.** On APOGEE and BACnet ALN the state lives in the panel
  and the editor database, never in the text, so the transform writes a comment
  carrying a marker and warns that loading it to a panel removes the statement.
  On **PXC.A it is in the text**: Siemens defines `# ` at the front of a line
  as the disable, and `parser` reads it, keeps the statement, and sets
  `Line.disabled`. A third case is written by the compiler rather than the
  engineer: an unrecognised command is saved wrapped in `UNKNOWN (...)` and
  ignored, so `parser` sets `Line.unknown`, does **not** parse what is inside
  (the compiler already rejected it), and `E123` reports the line as inert. The statement is kept rather than flattened to prose so the
  linter still sees defects that would bite the moment someone re-enables it.

## Rule authoring

Add rules in `ppcl/rules/*.py` with the `@rule(code, summary, severity)`
decorator; the module is imported by `linter._load_rules`. Codes: `E1xx`/`W1xx`
syntax, `E2xx`/`W2xx` flow, `E3xx`/`W3xx` semantics, `S6xx` style, `P7xx`
optimisation.

Every new rule needs **two** tests: one case that fires and, where a false
positive is plausible, one that stays quiet. Several rules here are deliberately
severity-graded rather than binary — the main-loop `GOTO` trampoline is `INFO`
while an inner backward `GOTO` is `WARNING`; a guarded override is `INFO` while
two unconditional writers are `WARNING`. Preserve that: a linter that cries wolf
on idiomatic PPCL gets turned off.

## Testing

```bash
python -m pytest tests -q          # 500 tests
python -m ppcl.cli lint samples    # exercises the CLI against real programs
```

`samples/` holds four real-world programs from `delphian/ppcl-library` (MIT).
They contain genuine defects — duplicate line numbers, `GOSUB` inside `IF`,
`TIMAVG` in a subroutine — and are the regression fixtures for those rules.
Do not "fix" them.

## Open questions

Tracked in `docs/PPCL-REFERENCE.md` §8, each with the test that would settle it:
the exact `LOOP` PID form, line-evaluation rate on current PXC hardware, whether
a bare `RELEAS` clears `@SMOKE`, and the `DC` example that contradicts
Table 4-1. Nothing here has been validated against a live panel.
