---
description: Author a new PPCL program from a described sequence of operations
argument-hint: <what the equipment should do>
---

Write PPCL for: $ARGUMENTS

## Prefer a sequence document over hand-written PPCL

When someone is describing *behaviour* rather than editing existing code, build
a sequence document and compile it. A decision table of equipment against modes,
plus interlocks, resets and loops, compiled with `ppcl_compile_sequence`.

That is not a stylistic preference. The compiler guarantees, by construction:

- exactly one backward `GOTO`, and it is the last statement
- a matching `RELEAS` at the **same priority** for every interlock force
- no time-based command inside an `IF` or a subroutine

Those are the three defects that cause most field failures, and in a compiled
document they cannot be written.

## Before you write anything

Establish, and ask if you do not know:

- the **modes of operation** — occupied, unoccupied, warm-up, shutdown, smoke
- what each piece of equipment does in each mode
- the **interlocks**, their trip and reset conditions, and the priority they act
  at. Every interlock needs a reset condition with real hysteresis
- the setpoints, and whether any are reset from another variable

Modes are listed lowest priority first and the last match wins, so a shutdown
mode placed last overrides everything above it.

## Always lint what you produce

`ppcl_compile_sequence` lints automatically and returns the findings. Show them.
Never present generated PPCL that has not been checked.

Then bench it with `ppcl_bench`, including a fault, before saying it is ready.

## If they are editing existing PPCL

Edit it directly. Compilation is one way — do not try to reconstruct a document
from hand-written code, because the `GOTO` structure carries intent a table
cannot represent.
