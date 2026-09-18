"""Prebuilt plants and bindings.

The presets exist so that ``ppcl new ahu`` followed by ``ppcl bench`` works
with no configuration: the default bindings use the same point names the AHU
template generates. Once that round trip works, editing the plant or the
bindings for real equipment is a matter of changing numbers.
"""

from __future__ import annotations

from .bench import Binding, Check
from .components import Coil, Damper, Fan, FreezeStat, MixingBox, Sensor, Valve, Zone
from .systems import AirHandler, HotWaterPlant, Plant, Weather


# --------------------------------------------------------------------------
# Weather presets
# --------------------------------------------------------------------------

WEATHER_PRESETS = {
    "design_winter": Weather.design_winter,
    "design_summer": Weather.design_summer,
    "shoulder": Weather.shoulder,
    "mild": lambda: Weather(low=45.0, high=65.0),
    "cold_day": lambda: Weather(low=8.0, high=26.0),
}


def build_weather(config) -> Weather:
    if config is None:
        return Weather()
    if isinstance(config, str):
        maker = WEATHER_PRESETS.get(config)
        if maker is None:
            raise KeyError(
                "unknown weather preset %r; available: %s"
                % (config, ", ".join(sorted(WEATHER_PRESETS)))
            )
        return maker()
    if "preset" in config:
        base = build_weather(config["preset"])
        for key in ("low", "high", "fixed", "min_hour"):
            if key in config:
                setattr(base, key, float(config[key]))
        return base
    return Weather(
        low=float(config.get("low", 20.0)),
        high=float(config.get("high", 45.0)),
        fixed=(float(config["fixed"]) if "fixed" in config else None),
        min_hour=float(config.get("min_hour", 5.0)),
    )


# --------------------------------------------------------------------------
# Air handler presets
# --------------------------------------------------------------------------


def single_zone_ahu(name: str = "AHU1", cfm: float = 10000.0) -> AirHandler:
    """A constant-volume single-zone AHU with hot and chilled water coils.

    Sized loosely on a floor of a mid-rise office: ~10,000 CFM serving a zone
    with a few hundred thousand BTU/h of envelope and internal load.
    """
    return AirHandler(
        name=name,
        supply_fan=Fan(design_cfm=cfm, heat_rise=1.2, proof_delay=8.0),
        return_fan=Fan(design_cfm=cfm * 0.9, heat_rise=0.0, proof_delay=8.0),
        mixing_box=MixingBox(
            outside_damper=Damper(leakage=0.03, characteristic=1.2),
            minimum_oa_fraction=0.15,
        ),
        heating_coil=Coil(
            kind="heating", capacity_btuh=cfm * 45.0, water_temp=180.0,
            effectiveness=0.85, valve=Valve(equal_percentage=True),
        ),
        cooling_coil=Coil(
            kind="cooling", capacity_btuh=cfm * 55.0, water_temp=44.0,
            effectiveness=0.80, valve=Valve(equal_percentage=True),
        ),
        zone=Zone(
            temperature=70.0,
            capacitance=cfm * 2.5,
            ua=cfm * 0.09,
            internal_gain_btuh=cfm * 3.0,
        ),
        freeze_stat=FreezeStat(setpoint=36.0, manual_reset=True, delay=30.0),
    )


def big_ahu(name: str = "AHU1") -> AirHandler:
    """A larger unit, closer to a full-floor air handler."""
    return single_zone_ahu(name=name, cfm=24000.0)


AHU_PRESETS = {
    "single_zone_ahu": single_zone_ahu,
    "big_ahu": big_ahu,
}


# --------------------------------------------------------------------------
# Plant construction
# --------------------------------------------------------------------------


def build_plant(config) -> Plant:
    """Build a plant from a configuration dictionary.

    Two forms are accepted::

        {"preset": "single_zone_ahu", "weather": "design_winter"}

        {"weather": {"low": 10, "high": 30},
         "systems": [{"kind": "ahu", "name": "AHU1", "cfm": 12000,
                      "overrides": {"zone.temperature": 68}}]}
    """
    if isinstance(config, str):
        config = {"preset": config}
    config = config or {}

    plant = Plant(weather=build_weather(config.get("weather")))

    if "systems" in config:
        for spec in config["systems"]:
            plant.add(_build_system(spec))
    else:
        preset = config.get("preset", "single_zone_ahu")
        maker = AHU_PRESETS.get(preset)
        if maker is None:
            raise KeyError(
                "unknown plant preset %r; available: %s"
                % (preset, ", ".join(sorted(AHU_PRESETS)))
            )
        kwargs = {}
        if "cfm" in config:
            kwargs["cfm"] = float(config["cfm"])
        plant.add(maker(name=config.get("name", "AHU1"), **kwargs))

    for path, value in (config.get("overrides") or {}).items():
        _apply_override(plant, path, value)

    return plant


def _build_system(spec: dict):
    kind = spec.get("kind", "ahu")
    name = spec.get("name", "AHU1")
    if kind == "ahu":
        system = single_zone_ahu(name=name, cfm=float(spec.get("cfm", 10000.0)))
    elif kind == "hot_water_plant":
        system = HotWaterPlant(name=name, setpoint=float(spec.get("setpoint", 180.0)))
    else:
        raise KeyError("unknown system kind %r" % kind)
    for path, value in (spec.get("overrides") or {}).items():
        _set_path(system, path, value)
    return system


def _apply_override(plant: Plant, path: str, value) -> None:
    system_name, _, rest = path.partition(".")
    system = plant.systems.get(system_name.upper())
    if system is None:
        raise KeyError("override targets unknown system %r" % system_name)
    _set_path(system, rest, value)


def _set_path(obj, path: str, value) -> None:
    """Set a dotted attribute path, e.g. ``zone.temperature``."""
    parts = path.split(".")
    for part in parts[:-1]:
        if not hasattr(obj, part):
            raise KeyError("no attribute %r while setting %r" % (part, path))
        obj = getattr(obj, part)
    leaf = parts[-1]
    if not hasattr(obj, leaf):
        raise KeyError("no attribute %r while setting %r" % (leaf, path))
    current = getattr(obj, leaf)
    if isinstance(current, bool):
        setattr(obj, leaf, bool(value))
    elif isinstance(current, (int, float)):
        setattr(obj, leaf, float(value))
    else:
        setattr(obj, leaf, value)


# --------------------------------------------------------------------------
# Default bindings
# --------------------------------------------------------------------------

#: Point names used by the ``ppcl new ahu`` template, so the generated
#: program benches with no configuration.
DEFAULT_AHU_BINDINGS = [
    Binding("MAT", "AHU1.mat", "sensor"),
    Binding("DAT", "AHU1.dat", "sensor"),
    Binding("OAT", "AHU1.oat", "sensor"),
    Binding("RAT", "AHU1.rat", "sensor"),
    Binding("ZNT", "AHU1.zone_temp", "sensor"),
    Binding("SFPRF", "AHU1.sfan_proof", "sensor"),
    Binding("FRZ", "AHU1.freeze_tripped", "sensor"),
    Binding("SFAN", "AHU1.sfan", "command"),
    Binding("RFAN", "AHU1.rfan", "command"),
    Binding("OADPR", "AHU1.oa_damper", "command"),
    Binding("HVLV", "AHU1.hw_valve", "command"),
    Binding("CVLV", "AHU1.cw_valve", "command"),
]


def default_bindings(preset: str = "single_zone_ahu") -> list:
    return [Binding(b.point, b.path, b.direction, b.scale, b.offset)
            for b in DEFAULT_AHU_BINDINGS]


#: Checks worth running against any AHU sequence. These encode what "the
#: sequence works" actually means for an air handler.
DEFAULT_AHU_CHECKS = [
    Check(kind="never_true", var="AHU1.freeze_tripped",
          name="freeze stat never trips"),
    Check(kind="never_below", var="AHU1.coil_face", low=36.0,
          name="coil face stays above 36 F"),
    Check(kind="max_cycles", var="AHU1.sfan_proof", limit=6, warmup=300.0,
          name="supply fan does not short cycle"),
    Check(kind="within", var="AHU1.actual_zone", low=66.0, high=78.0,
          warmup=1800.0, name="zone held between 66 and 78 F"),
]


def default_checks() -> list:
    return [Check(**vars(c)) for c in DEFAULT_AHU_CHECKS]
