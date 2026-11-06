# The PPCL plugin

Install this repository as a Claude Code plugin and every session becomes a
PPCL session — no re-explaining the language, no re-deriving the research, and
answers that come from running the linter rather than from recollection.

## What you get

**A skill** that loads automatically whenever PPCL, APOGEE, PXC, Insight or
Desigo CC comes up. It carries the execution model, the priority hierarchy, the
failure that matters most, the firmware differences, and the honesty
requirements — what is verified against the manual versus what is approximated.

**An MCP server** exposing the toolkit as eleven tools:

| Tool | Answers |
|---|---|
| `ppcl_lint` | every defect, with the manual reference it comes from |
| `ppcl_explain` | a command, priority, point type, function or rule code |
| `ppcl_help` | priority, execution model, SSTO, decision tables, provenance |
| `ppcl_analyze` | main loop, run-once lines, subroutines, points and priorities |
| `ppcl_simulate` | did this statement fire, and did the write take |
| `ppcl_bench` | does this actually control, including under a fault |
| `ppcl_compile_sequence` | author from a decision table, with guarantees |
| `ppcl_format` / `ppcl_renumber` / `ppcl_transform` | exact source edits |
| `ppcl_commands` | find the command when you know the job, not the name |

**Four commands**: `/ppcl-review`, `/ppcl-explain`, `/ppcl-bench`, `/ppcl-new`.

## Why this rather than a fine-tuned model

The corpus does not exist. There are about five PPCL programs in public,
because it is a proprietary language whose code lives inside panels at
individual sites. A fine-tune would have nothing to train on.

It would also learn the wrong facts. Siemens' own precedence table spells the
arc-tangent `ARC` while their command list spells it `ATN`; third-party PPCL in
the wild uses both. A model trained on scraped code learns the mode of the
data. Rule `E118` knows the answer because the conflict was resolved across two
sources and cited.

And the thing that matters most — *does this program actually work?* — no
amount of training gives you. Only execution does.

So the knowledge lives in `spec.py` and 90 rules rather than in weights, which
is strictly better: it is inspectable, it is citable, and a wrong fact is a
one-line fix instead of a retraining run.

## Install

The MCP server needs the `ppcl` package importable. `.mcp.json` runs
`ppcl_mcp.py` from the plugin's own root, so installing the repository as the
plugin is enough — no `pip install` required. It starts Python with `-E -s`
and takes the current directory off `sys.path` first: when Claude Code starts
a plugin's server, the current directory is your project, and a `ppcl` folder
or a `json.py` in it would otherwise be imported in place of the real one.
Opening this repository itself as a project uses the same file.

The command is `python`. On a Windows workstation that is the python.org
installer's name, and `python3` is usually the Microsoft Store stub. On macOS
and most Linux distributions it is the other way round: there is no `python`
unless you add one (`python-is-python3` on Debian and Ubuntu), or change
`command` in `.mcp.json` to `python3`.

```bash
git clone https://github.com/jamesccupps/PPCL
```

Then add the clone as a plugin directory. Licensed MIT — see `LICENSE`.

> **Before any program text leaves your machine**, run `ppcl redact` over it.
> Nothing in this repository comes from a real site, and nothing under
> `samples/` may: those four programs are from
> [delphian/ppcl-library](https://github.com/delphian/ppcl-library) (MIT).

Verify a local install with:

```bash
python -m ppcl.cli --version
python -m pytest tests -q
```

To check the MCP server by hand:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python -E -s ppcl_mcp.py
```

## What it will not do

**Nothing here touches a building system.** No panel writes, no network client,
no BACnet or P2 connection. The tools read and analyse text; you load programs
through Insight or Desigo CC yourself.

The file-writing endpoints of the web API are deliberately **not** exposed to
the MCP server. An agent gets to analyse and generate; saving over your files
stays your decision.

Nothing here has been validated against a live panel. Every command signature
traces to the manual or to Desigo CC's own Command Assist — a stronger claim
than "tested", and the only claim being made.

## Before program text leaves your machine

Real PPCL carries building, panel, floor and tenant names.

```bash
python -m ppcl.cli redact <path> --outdir <dir> --mapping local-map.json
```

Comment bodies are dropped entirely, because that is where tenant names and
room numbers actually live. The mapping file reverses it and stays local.
