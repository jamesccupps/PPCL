"""Physical component models for the PPCL test bench.

Scope, stated plainly: these are first-order lumped-parameter models in IP
units, built to exercise control logic. A sequence that behaves correctly here
is very likely to behave correctly on the equipment; a sequence that hunts,
short-cycles, deadlocks or trips a safety here definitely will.

They are **not** a load calculation and **not** an energy model. There is no
psychrometric chart, no coil circuiting, no duct pressure network, no radiant
exchange. Do not size equipment from this and do not quote kWh from it.

Standard constants used throughout (sea level, 70 F, standard air):

* ``1.08`` BTU/h per CFM per degree F  -- sensible heat, from
  60 min/h * 0.075 lb/ft3 * 0.24 BTU/lb-F
* ``500`` BTU/h per GPM per degree F   -- water side, from
  60 min/h * 8.33 lb/gal * 1.0 BTU/lb-F
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

#: Sensible heat factor, BTU/h per CFM per degree F.
SENSIBLE = 1.08
#: Water-side heat factor, BTU/h per GPM per degree F.
WATER = 500.0


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else (high if value > high else value)


# --------------------------------------------------------------------------
# Actuated devices
# --------------------------------------------------------------------------


@dataclass
class Actuator:
    """An actuator that takes time to travel and can stick.

    Real dampers and valves do not jump to a commanded position, and a control
    loop tuned against an instantaneous actuator will hunt on real equipment.
    ``stroke_time`` is the seconds required to travel the full 0-100% range.
    """

    stroke_time: float = 60.0
    position: float = 0.0  # percent, 0-100
    commanded: float = 0.0
    #: Fault: actuator is stuck at its current position.
    stuck: bool = False
    #: Fault: fraction of full stroke the actuator cannot achieve at the top.
    stroke_limit: float = 100.0

    def command(self, percent: float) -> None:
        self.commanded = clamp(percent, 0.0, 100.0)

    def step(self, dt: float) -> float:
        """Advance the actuator by ``dt`` seconds and return its position."""
        if self.stuck:
            return self.position
        target = min(self.commanded, self.stroke_limit)
        if self.stroke_time <= 0:
            self.position = target
            return self.position
        max_travel = 100.0 * dt / self.stroke_time
        delta = target - self.position
        if abs(delta) <= max_travel:
            self.position = target
        else:
            self.position += math.copysign(max_travel, delta)
        self.position = clamp(self.position, 0.0, 100.0)
        return self.position


@dataclass
class Damper:
    """An air damper with a characteristic curve and leakage.

    ``characteristic`` shapes flow against position: 1.0 is linear, values
    above 1 model the slow-opening behaviour typical of opposed-blade dampers
    with poor authority. ``leakage`` is the flow fraction that passes at 0%,
    which is what makes a "closed" outside air damper still freeze a coil.
    """

    actuator: Actuator = field(default_factory=lambda: Actuator(stroke_time=90.0))
    characteristic: float = 1.0
    leakage: float = 0.02
    minimum_position: float = 0.0

    def command(self, percent: float) -> None:
        self.actuator.command(max(percent, self.minimum_position))

    def step(self, dt: float) -> float:
        self.actuator.step(dt)
        return self.flow_fraction

    @property
    def position(self) -> float:
        return self.actuator.position

    @property
    def flow_fraction(self) -> float:
        """Fraction of full flow, 0 to 1, including leakage."""
        frac = (self.actuator.position / 100.0) ** self.characteristic
        return clamp(self.leakage + (1.0 - self.leakage) * frac, 0.0, 1.0)


@dataclass
class Valve:
    """A control valve with an equal-percentage or linear characteristic.

    Equal percentage is the normal choice for a hot or chilled water coil
    because it linearises the coil's own non-linear heat transfer.
    ``rangeability`` is the ratio of maximum to minimum controllable flow,
    typically 30 to 50 for a globe valve.
    """

    actuator: Actuator = field(default_factory=lambda: Actuator(stroke_time=60.0))
    equal_percentage: bool = True
    rangeability: float = 30.0
    #: Fault: flow fraction that passes with the valve commanded shut.
    leak_by: float = 0.0

    def command(self, percent: float) -> None:
        self.actuator.command(percent)

    def step(self, dt: float) -> float:
        self.actuator.step(dt)
        return self.flow_fraction

    @property
    def position(self) -> float:
        return self.actuator.position

    @property
    def flow_fraction(self) -> float:
        pos = self.actuator.position / 100.0
        if pos <= 0.0:
            return clamp(self.leak_by, 0.0, 1.0)
        if self.equal_percentage:
            frac = self.rangeability ** (pos - 1.0)
        else:
            frac = pos
        return clamp(max(frac, self.leak_by), 0.0, 1.0)


# --------------------------------------------------------------------------
# Air-side components
# --------------------------------------------------------------------------


@dataclass
class Fan:
    """A supply or return fan with start/stop, optional VFD, and proof.

    ``proof_delay`` models the time for a differential pressure or current
    switch to make after the fan starts, and to drop after it stops. Sequences
    that check proof immediately after commanding a fan fail on real equipment
    for exactly this reason.
    """

    design_cfm: float = 10000.0
    #: Temperature rise across the fan from motor and shaft work, degrees F.
    heat_rise: float = 1.0
    proof_delay: float = 8.0
    minimum_speed: float = 20.0

    running: bool = False
    speed: float = 100.0  # percent, when running
    proof: bool = False
    #: Fault: the fan will not start.
    failed: bool = False
    #: Fault: the proof switch never makes even when the fan runs.
    proof_failed: bool = False

    _proof_timer: float = 0.0

    def command(self, on: bool, speed: float = None) -> None:
        self.running = bool(on) and not self.failed
        if speed is not None:
            self.speed = clamp(speed, 0.0, 100.0)

    def step(self, dt: float) -> None:
        want = self.running and not self.proof_failed
        if want != self.proof:
            self._proof_timer += dt
            if self._proof_timer >= self.proof_delay:
                self.proof = want
                self._proof_timer = 0.0
        else:
            self._proof_timer = 0.0

    @property
    def cfm(self) -> float:
        if not self.running:
            return 0.0
        speed = max(self.speed, self.minimum_speed)
        return self.design_cfm * speed / 100.0

    @property
    def actual_heat_rise(self) -> float:
        """Fan heat scales roughly with the cube of speed over the airflow."""
        if not self.running:
            return 0.0
        ratio = max(self.speed, self.minimum_speed) / 100.0
        return self.heat_rise * (ratio ** 2)


@dataclass
class MixingBox:
    """Outside, return and relief dampers feeding a mixed-air plenum.

    The mixed-air temperature is the flow-weighted average of outside and
    return air. Damper leakage is what lets a nominally closed outside air
    damper drag the mixed air below freezing on a design night, so leakage is
    modelled rather than assumed away.
    """

    outside_damper: Damper = field(default_factory=Damper)
    #: Fraction of design airflow that must come from outside when occupied.
    minimum_oa_fraction: float = 0.15

    def step(self, dt: float) -> None:
        self.outside_damper.step(dt)

    def mixed_temp(self, outside_temp: float, return_temp: float,
                   fan_running: bool) -> float:
        if not fan_running:
            # With the fan off the plenum drifts toward outside air through
            # damper leakage, which is the condition that freezes coils.
            frac = self.outside_damper.flow_fraction
        else:
            frac = max(self.outside_damper.flow_fraction, 0.0)
        frac = clamp(frac, 0.0, 1.0)
        return frac * outside_temp + (1.0 - frac) * return_temp

    @property
    def oa_fraction(self) -> float:
        return self.outside_damper.flow_fraction


@dataclass
class Coil:
    """A hot or chilled water coil.

    Heat transfer is limited by three things, and the model applies all of
    them: the water flow available through the valve, the approach to the
    entering water temperature (you cannot heat air past the water), and the
    coil's design capacity.

    ``kind`` is ``"heating"`` or ``"cooling"``. Cooling is modelled **sensible
    only** -- there is no dehumidification, so do not use this to reason about
    humidity control or reheat energy.
    """

    kind: str = "heating"
    capacity_btuh: float = 400000.0
    water_temp: float = 180.0
    #: Design effectiveness: fraction of the theoretical maximum approach the
    #: coil achieves at full flow.
    effectiveness: float = 0.85
    valve: Valve = field(default_factory=Valve)
    #: Fault: fouling multiplier on effective capacity, 1.0 is clean.
    fouling: float = 1.0

    def step(self, dt: float) -> None:
        self.valve.step(dt)

    def leaving_temp(self, entering_temp: float, cfm: float) -> float:
        """Air temperature leaving the coil."""
        if cfm <= 0:
            return entering_temp

        flow = self.valve.flow_fraction
        if flow <= 0:
            return entering_temp

        approach = self.water_temp - entering_temp
        if self.kind == "heating" and approach <= 0:
            return entering_temp
        if self.kind == "cooling" and approach >= 0:
            return entering_temp

        # Limit 1: how far the air could move toward the water temperature.
        max_delta = approach * self.effectiveness * (flow ** 0.5)
        # Limit 2: the coil's rated capacity at the current water flow.
        capacity_delta = (self.capacity_btuh * flow * self.fouling) / (
            SENSIBLE * cfm
        )
        if self.kind == "cooling":
            capacity_delta = -capacity_delta

        if self.kind == "heating":
            delta = min(max_delta, capacity_delta)
            delta = max(delta, 0.0)
        else:
            delta = max(max_delta, capacity_delta)
            delta = min(delta, 0.0)

        return entering_temp + delta

    def load_btuh(self, entering_temp: float, cfm: float) -> float:
        """Heat added (positive) or removed (negative), BTU/h."""
        return SENSIBLE * cfm * (self.leaving_temp(entering_temp, cfm) - entering_temp)


@dataclass
class Sensor:
    """A measurement with first-order lag, offset, and failure modes.

    Sensor lag is why a discharge air loop tuned on an instantaneous model
    hunts in the field. ``tau`` is the time constant in seconds: a bare
    thermistor in a duct is 10-30 s, one in a thermowell can be minutes.
    """

    tau: float = 20.0
    value: float = 70.0
    offset: float = 0.0
    #: Fault: the reading is frozen at ``value``.
    stuck: bool = False
    #: Fault: the reading is forced to ``failed_value``.
    failed: bool = False
    failed_value: float = 0.0
    _initialised: bool = False

    def step(self, actual: float, dt: float) -> float:
        if self.failed:
            self.value = self.failed_value
            return self.value
        if self.stuck:
            return self.value
        if not self._initialised:
            self.value = actual + self.offset
            self._initialised = True
            return self.value
        if self.tau <= 0:
            self.value = actual + self.offset
            return self.value
        alpha = 1.0 - math.exp(-dt / self.tau)
        self.value += (actual + self.offset - self.value) * alpha
        return self.value


@dataclass
class Zone:
    """A conditioned space as a single lumped thermal capacitance.

    ``capacitance`` is BTU per degree F of everything in the zone that stores
    heat -- air, furniture, slab, partitions. A rule of thumb for an office
    floor is roughly 3 to 6 BTU/F per square foot; the default here suits a
    space of a few thousand square feet.

    ``ua`` is the envelope conductance in BTU/h-F to outside air.
    """

    temperature: float = 72.0
    capacitance: float = 20000.0
    ua: float = 800.0
    internal_gain_btuh: float = 30000.0
    #: Occupied fraction, scaling the internal gain.
    occupancy: float = 1.0

    def step(self, supply_temp: float, cfm: float, outside_temp: float,
             dt: float) -> float:
        supply_q = SENSIBLE * cfm * (supply_temp - self.temperature)
        envelope_q = self.ua * (outside_temp - self.temperature)
        internal_q = self.internal_gain_btuh * self.occupancy
        net = supply_q + envelope_q + internal_q
        self.temperature += net * (dt / 3600.0) / max(self.capacitance, 1.0)
        return self.temperature


@dataclass
class FreezeStat:
    """A low-limit thermostat on the coil face.

    Manual-reset devices stay tripped until someone walks to the unit, which
    is precisely the behaviour a sequence has to be tested against.
    """

    setpoint: float = 36.0
    manual_reset: bool = True
    tripped: bool = False
    #: Seconds below setpoint before the device trips.
    delay: float = 30.0
    _below: float = 0.0

    def step(self, temperature: float, dt: float) -> bool:
        if temperature < self.setpoint:
            self._below += dt
            if self._below >= self.delay:
                self.tripped = True
        else:
            self._below = 0.0
            if not self.manual_reset:
                self.tripped = False
        return self.tripped

    def reset(self) -> None:
        self.tripped = False
        self._below = 0.0
