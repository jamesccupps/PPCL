"""The panel test kit: programs that load, and predictions that hold.

``panel-tests/`` holds small programs that settle, on real hardware, what
the simulator has to assume. Their README states what the workbench predicts
for each. These tests keep that table true: when the simulator changes, the
prediction it makes changes here and in the README together, and when a
panel result comes back different, both are corrected against it.
"""

import glob
import os

import pytest

from ppcl import linter, parser, spec
from ppcl.simulator import Clock, Simulator

KIT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "panel-tests")
PROGRAMS = sorted(glob.glob(os.path.join(KIT, "*.pcl")))


def _load(name):
    with open(os.path.join(KIT, name), encoding="utf-8") as fh:
        return parser.parse(fh.read())


def test_the_kit_has_six_programs_and_the_readme_names_each():
    with open(os.path.join(KIT, "README.md"), encoding="utf-8") as fh:
        readme = fh.read()
    assert len(PROGRAMS) == 6
    for path in PROGRAMS:
        assert os.path.basename(path) in readme


@pytest.mark.parametrize("path", PROGRAMS, ids=os.path.basename)
@pytest.mark.parametrize("firmware", [spec.Firmware.APOGEE,
                                      spec.Firmware.PXC_A])
def test_each_program_loads_on_both_generations(path, firmware):
    with open(path, encoding="utf-8") as fh:
        program = parser.parse(fh.read())
    assert not program.errors
    found = {(d.code, d.severity.value)
             for d in linter.lint(program, firmware=firmware)}
    serious = {code for code, severity in found
               if severity in ("error", "warning")}
    # T2 assigns to the reserved SECND1 on purpose: that is its question.
    expected = {"W107"} if "secnd" in path else set()
    assert serious == expected


def test_t1_the_simulator_assumes_a_bare_releas_clears_smoke():
    sim = Simulator(_load("t1-releas-smoke.pcl"))
    sim.panel.load({"T1.SET": 1.0})
    sim.run(passes=2)
    assert sim.panel.get("T1.OUT").priority == "@SMOKE"
    sim.panel.load({"T1.SET": 0.0, "T1.BARE": 1.0})
    sim.run(passes=2)
    assert sim.panel.get("T1.OUT").priority == "@NONE"
    assert any("bare RELEAS" in w for w in sim.warnings)


def test_t2_the_simulator_counts_secnd1_on_past_a_minute():
    sim = Simulator(_load("t2-secnd-counter.pcl"))
    sim.panel.load({"T2.RESET": 1.0})
    sim.run(passes=1)
    sim.panel.load({"T2.RESET": 0.0})
    sim.run(passes=95)
    assert sim.panel.value("T2.SECS") == 95.0
    assert sim.panel.value("T2.DONE") == 1.0


def test_t3_the_simulator_commands_tod_every_pass():
    sim = Simulator(_load("t3-tod-level-or-edge.pcl"), clock=Clock(hours=10.0))
    sim.run(passes=2)
    sim.panel.load({"T3.KICK": 1.0})
    sim.execute_lines(3)
    assert sim.panel.value("T3.FAN") == 0.0
    sim.run(passes=2)
    assert sim.panel.value("T3.FAN") == 1.0           # level, not edge
    assert sim.panel.value("T3.KICK") == 0.0


def test_t4_the_simulator_writes_zero_and_warns_for_both():
    sim = Simulator(_load("t4-divide-and-overflow.pcl"))
    sim.panel.load({"T4.RESET": 1.0})
    sim.run(passes=1)
    sim.panel.load({"T4.RESET": 0.0})
    sim.run(passes=60)
    assert sim.panel.value("T4.DIV") == 0.0
    assert any("division by zero" in w for w in sim.warnings)
    assert any("T4.BIG overflowed" in w for w in sim.warnings)


def test_t5_the_simulator_assumes_500_lines_a_second():
    sim = Simulator(_load("t5-line-rate.pcl"))
    sim.panel.load({"T5.RESET": 1.0})
    sim.run(passes=1)
    sim.panel.load({"T5.RESET": 0.0, "T5.COUNT": 0.0})
    sim.execute_lines(500 * 60)
    assert sim.panel.value("T5.COUNT") == 10000.0


def test_t6_the_simulator_gives_48_from_proportional_alone():
    sim = Simulator(_load("t6-loop-step.pcl"))
    sim.panel.load({"T6.PV": 68.0, "T6.SP": 70.0})
    sim.run(passes=5)
    assert sim.panel.value("T6.CV") == pytest.approx(48.0)
    assert any("approximate PID form" in w for w in sim.warnings)
