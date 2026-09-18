---
description: Prove a PPCL program against simulated equipment before it touches a panel
argument-hint: <path-to-program> [weather] [fault]
---

Bench the program at `$1`.

## Run it

Call `ppcl_bench`. Use `$2` as the weather preset if given, otherwise
`design_winter` — the winter case is where freeze protection and mixed-air low
limits actually get exercised.

## Then break it

A passing run against benign weather proves very little. Inject the failure the
sequence is supposed to handle and run it again: a stuck outside-air damper, a
failed sensor, a coil with no water, a fan that does not prove. If `$3` names a
fault, start there; otherwise pick the one this program's interlocks claim to
protect against.

The question a fault answers is not "does the model still run" but "does the
program do something sensible about it" — and the answer is regularly no.

## Reporting

State clearly which checks passed and which failed, and for a failure say what
the program did wrong rather than only that a threshold was crossed.

Three honesty requirements, every time:

- **`LOOP` output values are approximated.** Siemens does not publish the PID
  form. Timing, inputs and output limits are exact; the computed value is
  indicative. Never present a simulated `cv` as tuning guidance.
- **The equipment models are first-order approximations.** Good for exercising
  control logic. Not a load calculation, not an energy model, no
  dehumidification.
- **`SSTO`, `PDL*`, `OIP`, `DC` and `DCR` are not simulated** — they are traced
  as no-ops. A clean run of a program using them proves nothing about them.

If the run was long enough that the bench reduced the simulated line rate, say
so; it changes how often a non-time-gated accumulator ticks.
