"""Composed equipment: air handlers, weather, and the plant container.

An air handler here is the air path an operator would draw on a napkin:

    outside air ---\\
                    [mixing box] -- preheat -- cooling -- heating -- fan --> zone
    return air ----/                                                          |
         ^--------------------------------------------------------------------

Each stage is one of the models in :mod:`ppcl.plant.components`, stepped in
that order every timestep. Sensors read the physical state through a lag, and
the PPCL program only ever sees the sensors -- which is the point, because a
sequence that only works against instantaneous perfect measurements is a
sequence that does not work.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .components import (
    SENSIBLE,
    Coil,
    Damper,
    Fan,
    FreezeStat,
    MixingBox,
    Sensor,
    Valve,
    Zone,
    clamp,
)


# --------------------------------------------------------------------------
# Weather
# --------------------------------------------------------------------------


@dataclass
class Weather:
    """Outside conditions over a simulated day.

    The default is a sinusoid with the minimum at 05:00 and the maximum at
    15:00, which is close enough to a real diurnal swing for control testing.
    Set ``fixed`` to hold a constant temperature for a design-day test.
    """

    low: float = 20.0
    high: float = 45.0
    fixed: float = None
    #: Hour of the daily minimum.
    min_hour: float = 5.0

    def outside_temp(self, hours: float) -> float:
        if self.fixed is not None:
            return self.fixed
        mean = (self.low + self.high) / 2.0
        swing = (self.high - self.low) / 2.0
        phase = (hours - self.min_hour) / 24.0 * 2.0 * math.pi
        return mean - swing * math.cos(phase)

    @classmethod
    def design_winter(cls) -> "Weather":
        """A Maine design-day morning: cold enough to freeze a coil."""
        return cls(fixed=2.0)

    @classmethod
    def design_summer(cls) -> "Weather":
        return cls(fixed=91.0)

    @classmethod
    def shoulder(cls) -> "Weather":
        """Economizer weather -- the season sequences actually get wrong."""
        return cls(low=38.0, high=58.0)


# --------------------------------------------------------------------------
# Air handler
# --------------------------------------------------------------------------


@dataclass
class AirHandler:
    """A single-zone air handler with mixing box, coils, fan and a zone."""

    name: str = "AHU1"

    supply_fan: Fan = field(default_factory=Fan)
    return_fan: Fan = field(default_factory=lambda: Fan(design_cfm=9000.0, heat_rise=0.0))
    mixing_box: MixingBox = field(default_factory=MixingBox)
    heating_coil: Coil = field(
        default_factory=lambda: Coil(kind="heating", capacity_btuh=500000.0,
                                     water_temp=180.0)
    )
    cooling_coil: Coil = field(
        default_factory=lambda: Coil(kind="cooling", capacity_btuh=600000.0,
                                     water_temp=44.0)
    )
    zone: Zone = field(default_factory=Zone)
    freeze_stat: FreezeStat = field(default_factory=FreezeStat)

    mat_sensor: Sensor = field(default_factory=lambda: Sensor(tau=25.0))
    dat_sensor: Sensor = field(default_factory=lambda: Sensor(tau=20.0))
    rat_sensor: Sensor = field(default_factory=lambda: Sensor(tau=45.0))
    zone_sensor: Sensor = field(default_factory=lambda: Sensor(tau=180.0))
    oat_sensor: Sensor = field(default_factory=lambda: Sensor(tau=300.0))

    # Live physical state, updated every step.
    mixed_temp: float = 55.0
    coil_face_temp: float = 55.0
    discharge_temp: float = 55.0
    return_temp: float = 72.0
    outside_temp: float = 40.0
    cfm: float = 0.0

    # -- commands from PPCL ------------------------------------------------

    def apply(self, name: str, value: float) -> bool:
        """Apply a command. Returns False if the name is unknown."""
        if name == "sfan":
            self.supply_fan.command(value >= 0.5)
        elif name == "sfan_speed":
            self.supply_fan.command(self.supply_fan.running, speed=value)
        elif name == "rfan":
            self.return_fan.command(value >= 0.5)
        elif name == "oa_damper":
            self.mixing_box.outside_damper.command(value)
        elif name == "hw_valve":
            self.heating_coil.valve.command(value)
        elif name == "cw_valve":
            self.cooling_coil.valve.command(value)
        elif name == "freeze_reset":
            if value >= 0.5:
                self.freeze_stat.reset()
        else:
            return False
        return True

    # -- readable state ----------------------------------------------------

    def outputs(self) -> dict:
        """Everything a PPCL program could sensibly read."""
        return {
            "oat": self.oat_sensor.value,
            "mat": self.mat_sensor.value,
            "dat": self.dat_sensor.value,
            "rat": self.rat_sensor.value,
            "zone_temp": self.zone_sensor.value,
            "sfan_proof": 1.0 if self.supply_fan.proof else 0.0,
            "rfan_proof": 1.0 if self.return_fan.proof else 0.0,
            "freeze_tripped": 1.0 if self.freeze_stat.tripped else 0.0,
            "oa_position": self.mixing_box.outside_damper.position,
            "oa_fraction": self.mixing_box.oa_fraction,
            "hw_position": self.heating_coil.valve.position,
            "cw_position": self.cooling_coil.valve.position,
            "cfm": self.cfm,
            # Unlagged physical truth, for charting and assertions. A PPCL
            # program should not be bound to these -- the panel cannot see them.
            "actual_mat": self.mixed_temp,
            "actual_dat": self.discharge_temp,
            "actual_zone": self.zone.temperature,
            "coil_face": self.coil_face_temp,
        }

    # -- physics -----------------------------------------------------------

    def step(self, dt: float, outside_temp: float) -> None:
        self.outside_temp = outside_temp

        # 1. Actuators travel first: the air sees last step's positions moving.
        self.mixing_box.step(dt)
        self.heating_coil.step(dt)
        self.cooling_coil.step(dt)
        self.supply_fan.step(dt)
        self.return_fan.step(dt)

        running = self.supply_fan.running
        self.cfm = self.supply_fan.cfm

        # 2. Mixing box. With the fan off, damper leakage still admits outside
        #    air, which is exactly how coils freeze overnight.
        self.return_temp = self.zone.temperature
        self.mixed_temp = self.mixing_box.mixed_temp(
            outside_temp, self.return_temp, running
        )

        # 3. Coils, in air-path order.
        if running and self.cfm > 0:
            after_cooling = self.cooling_coil.leaving_temp(self.mixed_temp, self.cfm)
            after_heating = self.heating_coil.leaving_temp(after_cooling, self.cfm)
            self.coil_face_temp = min(self.mixed_temp, after_cooling)
            self.discharge_temp = after_heating + self.supply_fan.actual_heat_rise
        else:
            # No airflow: the coil face sits at the plenum temperature, and
            # a hot water coil with flow will warm the still air around it.
            self.coil_face_temp = self.mixed_temp
            if self.heating_coil.valve.flow_fraction > 0.05:
                self.coil_face_temp = (
                    self.mixed_temp
                    + (self.heating_coil.water_temp - self.mixed_temp) * 0.3
                )
            self.discharge_temp = self.coil_face_temp

        # 4. Safety, on the coil face where the device actually lives.
        self.freeze_stat.step(self.coil_face_temp, dt)

        # 5. Zone response.
        self.zone.step(self.discharge_temp, self.cfm, outside_temp, dt)

        # 6. Sensors last: the panel reads a lagged view of all of the above.
        self.oat_sensor.step(outside_temp, dt)
        self.mat_sensor.step(self.mixed_temp, dt)
        self.dat_sensor.step(self.discharge_temp, dt)
        self.rat_sensor.step(self.return_temp, dt)
        self.zone_sensor.step(self.zone.temperature, dt)


# --------------------------------------------------------------------------
# Hot water plant
# --------------------------------------------------------------------------


@dataclass
class HotWaterPlant:
    """A boiler and lead/lag pumps, for testing plant-level sequences."""

    name: str = "HWP"
    setpoint: float = 180.0
    supply_temp: float = 140.0
    #: Degrees F per minute the boiler can raise supply temperature.
    ramp_rate: float = 4.0
    ambient: float = 60.0
    #: Degrees F per minute lost when the boiler is off.
    loss_rate: float = 0.8

    boiler_on: bool = False
    pumps: dict = field(default_factory=lambda: {"P1": False, "P2": False})
    supply_sensor: Sensor = field(default_factory=lambda: Sensor(tau=60.0))

    def apply(self, name: str, value: float) -> bool:
        if name == "boiler":
            self.boiler_on = value >= 0.5
        elif name.startswith("pump"):
            key = "P%s" % name[-1]
            if key not in self.pumps:
                return False
            self.pumps[key] = value >= 0.5
        elif name == "setpoint":
            self.setpoint = value
        else:
            return False
        return True

    def outputs(self) -> dict:
        return {
            "supply_temp": self.supply_sensor.value,
            "actual_supply": self.supply_temp,
            "boiler_status": 1.0 if self.boiler_on else 0.0,
            "flow": 1.0 if any(self.pumps.values()) else 0.0,
            "pump_count": float(sum(1 for v in self.pumps.values() if v)),
        }

    def step(self, dt: float, outside_temp: float) -> None:
        minutes = dt / 60.0
        flowing = any(self.pumps.values())
        if self.boiler_on and flowing:
            self.supply_temp += self.ramp_rate * minutes
            self.supply_temp = min(self.supply_temp, self.setpoint + 5.0)
        else:
            self.supply_temp -= self.loss_rate * minutes
            self.supply_temp = max(self.supply_temp, self.ambient)
        self.supply_sensor.step(self.supply_temp, dt)


# --------------------------------------------------------------------------
# Plant container
# --------------------------------------------------------------------------


class Plant:
    """A named collection of systems, addressed by dotted path.

    ``plant.read("AHU1.dat")`` returns the lagged discharge sensor.
    ``plant.write("AHU1.hw_valve", 60)`` commands the heating valve.
    """

    def __init__(self, weather: Weather = None):
        self.systems = {}
        self.weather = weather or Weather()
        self.outside_temp = self.weather.outside_temp(8.0)

    def add(self, system) -> None:
        self.systems[system.name.upper()] = system

    def _split(self, path: str):
        if "." not in path:
            raise KeyError("plant path %r must be SYSTEM.name" % path)
        system, _, name = path.partition(".")
        key = system.upper()
        if key not in self.systems:
            raise KeyError(
                "no system %r in the plant; known systems: %s"
                % (system, ", ".join(sorted(self.systems)) or "none")
            )
        return self.systems[key], name

    def read(self, path: str) -> float:
        system, name = self._split(path)
        outputs = system.outputs()
        if name not in outputs:
            raise KeyError(
                "system %s has no output %r; available: %s"
                % (system.name, name, ", ".join(sorted(outputs)))
            )
        return outputs[name]

    def write(self, path: str, value: float) -> None:
        system, name = self._split(path)
        if not system.apply(name, value):
            raise KeyError("system %s has no command %r" % (system.name, name))

    def snapshot(self) -> dict:
        out = {}
        out["OUTSIDE"] = self.outside_temp
        for key, system in self.systems.items():
            for name, value in system.outputs().items():
                out["%s.%s" % (key, name)] = value
        return out

    def step(self, dt: float, hours: float) -> None:
        self.outside_temp = self.weather.outside_temp(hours)
        for system in self.systems.values():
            system.step(dt, self.outside_temp)
