---
description: Review a PPCL program — lint it, analyse its flow, and assess whether the sequence is actually right
argument-hint: <path-to-program> [firmware]
---

Review the PPCL program at `$1`.

Work in this order, and do not skip to an opinion before the tools have run.

## 1. Check it mechanically

Call `ppcl_lint` on the file's contents. If the user named a firmware in `$2`,
pass it; otherwise ask which panel generation this is for **if and only if** the
findings would differ — `pxc_a` allows 512-character lines and rejects `OIP`,
`apogee` allows 66 characters and 16 operands, older families 13.

Do not hand-inspect for duplicate line numbers, argument counts, unreachable
code or reference integrity. The linter does that exhaustively and cites the
manual for each finding.

## 2. Understand what it does

Call `ppcl_analyze`. That gives you the main loop entry, which lines run every
pass versus once at startup, the subroutines, and every point with the
priorities it is commanded at.

## 3. Reproduce anything suspicious

If a finding concerns priority, timing or "this never seems to fire", prove it
with `ppcl_simulate` rather than reasoning about it. Seed the points that
matter. A command refused because the point is held at a higher priority comes
back named — that is usually the whole answer.

If the question is whether the *control* works rather than whether a line
fires, use `ppcl_bench`.

## 4. Then apply judgement

The linter checks conformance. It cannot tell you whether the sequence is
right. Spend your own effort on:

- **Does the logic match the sequence of operations?** Ask for the SOO if you
  do not have it.
- **Life safety and equipment protection** — freeze protection, high static
  cutout, smoke control, low limits. These must fail safe and release cleanly.
  Never soften one to clear a lint finding.
- **Power return.** Execution resumes at the first line. Latches, `WAIT`
  triggers and `SAMPLE` intervals all reset.
- **Other programs in the same panel**, and Desigo CC or Insight scheduling,
  which can command the same points.

## Reporting

Lead with what is actually wrong, most serious first. For each finding say what
breaks in the building, not just which rule fired. Quote the manual citation
the finding carries.

If the program is clean, say so plainly and state what you checked — a clean
report that does not say what it covered is worthless.
