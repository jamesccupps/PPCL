# Panel test kit

Nothing in this workbench has been checked against a live panel. Its simulator
follows the manual where the manual is clear, and where it is not, it makes an
assumption and says so at runtime. These six small programs settle those
assumptions on real hardware, one question each. Each takes a few minutes.

## Safety first

- **A spare or bench controller only**, with no equipment wired to it and not
  on a network serving occupied space. Never a panel in service.
- **Virtual points only.** Every point these programs touch is one you create
  for the test (analog value / binary value objects on BACnet, LAO / LDO
  virtual points on APOGEE).
- T1 commands a virtual point at `@SMOKE` priority. That is a priority level,
  not a smoke system, but some front ends annunciate it: run it where nobody
  is watching the alarm screen for real, and clean up with step 4.
- The one exception to "spare panel only" is T5's alternative on PXC.A: reading
  the cycle-time metrics a panel already reports changes nothing.

Every program lints clean on APOGEE and PXC.A firmware (`ppcl lint
--firmware pxc_a panel-tests`). T2 draws `W107` on purpose: assigning to the
reserved `SECND1` is the thing it tests.

## How to run one

1. Create the virtual points the program's header lists.
2. Load the program from Desigo CC or Insight, and enable it.
3. Follow the steps below, recording what the table asks for.
4. Disable and delete the program and its points afterwards.

## The tests

| Test | Question | Steps | Record | The workbench predicts |
|---|---|---|---|---|
| **T1** `t1-releas-smoke.pcl` | Does a bare `RELEAS` clear `@SMOKE`? (open question 3) | Turn `T1.SET` on, then off. Turn `T1.BARE` on, then off. Read `T1.OUT`'s priority. Then turn `T1.CLEAN` on and off. | `T1.OUT`'s value and priority after SET, and after BARE | `50.0 @SMOKE` after SET; **`@NONE` after a bare RELEAS** -- an assumption, and the simulator warns that it is one |
| **T2** `t2-secnd-counter.pcl` | Is `SECND1` a seconds counter you can reset, or the second of the minute? | Turn `T2.RESET` on, then off. Watch `T2.SECS` for two minutes. | Does `SECS` pass 60? When does `T2.DONE` come on? | `SECS` counts on past 60 and reads **95** after 95 s; **`DONE` on after 90 s** |
| **T3** `t3-tod-level-or-edge.pcl` | Does `TOD` command every pass inside its window, or only at its times? | Run between 00:00 and 23:59. Turn `T3.KICK` on once; line 120 turns `T3.FAN` off and `KICK` back off. | Is `FAN` back on within a second, or does it stay off? | **Back on within a pass**: the simulator commands every pass (level). The manual's "time the ON command runs" suggests it should stay off (edge) |
| **T4** `t4-divide-and-overflow.pcl` | What does the panel do with 1/0, and with a value that overflows? | Turn `T4.RESET` on, then off. Wait a minute. Export a `PPCL DISPLAY REPORT`. | `T4.DIV`; `T4.BIG` after a minute; whether lines 110 and 130 show as failed in the report, and any error code | **0 for both, with a warning** that the panel's answer is not documented. A panel value is likely a 32-bit float, which `BIG` overruns on its seventh multiplication; the simulator's 64-bit float lasts about fifty |
| **T5** `t5-line-rate.pcl` | How many lines a second does this panel run? (open question 2) | Turn `T5.RESET` on, then off, and time exactly 60 s by a clock. Repeat with the panel's other programs disabled, if it has any. | `T5.COUNT` at 60 s, each way. Lines per second = `COUNT x 3 / 60` | **10,000** in 60 s: the simulator assumes 500 lines a second. On PXC.A, the Web Interface's per-program cycle time answers this without a test panel |
| **T6** `t6-loop-step.pcl` | What form does `LOOP` compute? (open question 1) | Set `T6.SP` to 70 and `T6.PV` to 68. Trend `T6.CV` every second for two minutes. Then set `ig` to 200 on line 100, reload, and repeat. | The `CV` trend, both runs | **48.0** and steady with `ig = 0` (bias 50, 1 % per unit of error, direct acting). With `ig > 0` the simulator's form is an approximation and says so; the panel's trend is the answer |

## What to do with a result

Send it back with the panel's model and firmware revision (the PXME / PME
string, as on the panel's Firmware Revision). Each result either confirms the
simulator, or changes it:

- Record it in `docs/PPCL-REFERENCE.md` section 8, marking the question as
  settled by a named panel and firmware.
- If the simulator assumed wrong, change the simulator -- against its golden
  record, as `CLAUDE.md` requires -- and change the prediction in this table
  and in `tests/test_panel_tests.py`, which holds the simulator to it.
- If the linter has a rule built on the assumption, re-check it the same way.
