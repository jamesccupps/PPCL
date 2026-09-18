"""Tests for the equipment models and the test bench.

The physics tests assert *direction and bound* rather than exact values. The
models are first-order approximations, so pinning them to four decimal places
would only lock in the approximation. What has to hold is that heat flows the
right way, that nothing violates a conservation limit, and that the documented
failure modes actually reproduce.
"""

import pytest

from ppcl import generator, parser
from ppcl.plant import (
    Binding,
    Check,
    Fault,
    Plant,
    TestBench,
    Weather,
    build_plant,
    default_bindings,
    default_checks,
)
from ppcl.plant.components import (
    SENSIBLE,
    Actuator,
    Coil,
    Damper,
    Fan,
    FreezeStat,
    MixingBox,
    Sensor,
    Valve,
    Zone,
)
from ppcl.plant.library import single_zone_ahu


# --------------------------------------------------------------------------
# Actuators, dampers, valves
# --------------------------------------------------------------------------


def test_actuator_takes_its_stroke_time_to_travel():
    a = Actuator(stroke_time=60.0, position=0.0)
    a.command(100.0)
    a.step(30.0)
    assert a.position == pytest.approx(50.0)
    a.step(30.0)
    assert a.position == pytest.approx(100.0)


def test_actuator_does_not_overshoot():
    a = Actuator(stroke_time=10.0)
    a.command(40.0)
    a.step(100.0)
    assert a.position == pytest.approx(40.0)


def test_stuck_actuator_ignores_commands():
    a = Actuator(stroke_time=10.0, position=75.0, stuck=True)
    a.command(0.0)
    a.step(60.0)
    assert a.position == 75.0


def test_closed_damper_still_leaks():
    d = Damper(leakage=0.05)
    d.command(0.0)
    d.step(120.0)
    assert d.position == 0.0
    assert d.flow_fraction == pytest.approx(0.05)


def test_damper_reaches_full_flow_when_open():
    d = Damper(leakage=0.05)
    d.command(100.0)
    d.step(300.0)
    assert d.flow_fraction == pytest.approx(1.0)


def test_equal_percentage_valve_opens_slowly_at_first():
    v = Valve(equal_percentage=True, rangeability=30.0)
    v.command(50.0)
    v.step(300.0)
    # At mid-stroke an equal-percentage valve passes far less than half flow.
    assert 0.0 < v.flow_fraction < 0.3


def test_linear_valve_is_proportional():
    v = Valve(equal_percentage=False)
    v.command(50.0)
    v.step(300.0)
    assert v.flow_fraction == pytest.approx(0.5)


def test_valve_leak_by_passes_flow_when_shut():
    v = Valve(leak_by=0.1)
    v.command(0.0)
    v.step(300.0)
    assert v.flow_fraction == pytest.approx(0.1)


# --------------------------------------------------------------------------
# Coils
# --------------------------------------------------------------------------


def test_heating_coil_cannot_exceed_the_water_temperature():
    coil = Coil(kind="heating", capacity_btuh=10_000_000.0, water_temp=140.0,
                effectiveness=1.0)
    coil.valve.command(100.0)
    coil.step(300.0)
    leaving = coil.leaving_temp(entering_temp=55.0, cfm=10000.0)
    assert 55.0 < leaving <= 140.0


def test_cooling_coil_cannot_go_below_the_water_temperature():
    coil = Coil(kind="cooling", capacity_btuh=10_000_000.0, water_temp=44.0,
                effectiveness=1.0)
    coil.valve.command(100.0)
    coil.step(300.0)
    leaving = coil.leaving_temp(entering_temp=80.0, cfm=10000.0)
    assert 44.0 <= leaving < 80.0


def test_coil_capacity_limits_the_temperature_rise():
    cfm = 10000.0
    capacity = 108000.0  # exactly 10 F of rise at this airflow
    coil = Coil(kind="heating", capacity_btuh=capacity, water_temp=250.0,
                effectiveness=1.0)
    coil.valve.command(100.0)
    coil.step(300.0)
    rise = coil.leaving_temp(55.0, cfm) - 55.0
    assert rise == pytest.approx(capacity / (SENSIBLE * cfm), rel=0.05)


def test_shut_coil_does_nothing():
    coil = Coil(kind="heating", water_temp=180.0)
    coil.valve.command(0.0)
    coil.step(300.0)
    assert coil.leaving_temp(55.0, 10000.0) == pytest.approx(55.0)


def test_coil_with_no_airflow_passes_air_through():
    coil = Coil(kind="heating")
    coil.valve.command(100.0)
    coil.step(300.0)
    assert coil.leaving_temp(55.0, 0.0) == 55.0


def test_heating_coil_will_not_cool_air_hotter_than_its_water():
    coil = Coil(kind="heating", water_temp=120.0)
    coil.valve.command(100.0)
    coil.step(300.0)
    assert coil.leaving_temp(150.0, 10000.0) == pytest.approx(150.0)


# --------------------------------------------------------------------------
# Fans, sensors, zone, safeties
# --------------------------------------------------------------------------


def test_fan_proof_lags_the_start_command():
    fan = Fan(proof_delay=10.0)
    fan.command(True)
    fan.step(5.0)
    assert fan.proof is False
    fan.step(6.0)
    assert fan.proof is True


def test_failed_fan_will_not_start():
    fan = Fan(failed=True)
    fan.command(True)
    assert fan.running is False
    assert fan.cfm == 0.0


def test_fan_airflow_scales_with_speed():
    fan = Fan(design_cfm=10000.0, minimum_speed=0.0)
    fan.command(True, speed=50.0)
    assert fan.cfm == pytest.approx(5000.0)


def test_sensor_lags_toward_the_actual_value():
    s = Sensor(tau=60.0)
    s.step(70.0, 1.0)  # initialises to the actual value
    readings = [s.step(100.0, 10.0) for _ in range(3)]
    assert readings[0] < readings[1] < readings[2] < 100.0
    for _ in range(60):
        s.step(100.0, 10.0)
    assert s.value == pytest.approx(100.0, abs=0.5)


def test_failed_sensor_reports_its_failure_value():
    s = Sensor(failed=True, failed_value=32.0)
    assert s.step(75.0, 1.0) == 32.0


def test_stuck_sensor_holds_its_reading():
    s = Sensor(tau=1.0)
    s.step(70.0, 1.0)
    s.stuck = True
    assert s.step(100.0, 60.0) == pytest.approx(70.0)


def test_zone_warms_with_warm_supply_air():
    z = Zone(temperature=68.0, internal_gain_btuh=0.0, ua=0.0)
    z.step(supply_temp=95.0, cfm=10000.0, outside_temp=68.0, dt=60.0)
    assert z.temperature > 68.0


def test_zone_cools_toward_outside_with_no_airflow():
    z = Zone(temperature=70.0, internal_gain_btuh=0.0, ua=1000.0)
    z.step(supply_temp=70.0, cfm=0.0, outside_temp=0.0, dt=600.0)
    assert z.temperature < 70.0


def test_freezestat_needs_sustained_low_temperature():
    f = FreezeStat(setpoint=36.0, delay=30.0)
    assert f.step(30.0, 10.0) is False
    assert f.step(30.0, 10.0) is False
    assert f.step(30.0, 15.0) is True


def test_manual_reset_freezestat_stays_tripped():
    f = FreezeStat(setpoint=36.0, delay=0.0, manual_reset=True)
    f.step(30.0, 10.0)
    assert f.tripped
    f.step(70.0, 600.0)
    assert f.tripped
    f.reset()
    assert not f.tripped


def test_auto_reset_freezestat_clears_itself():
    f = FreezeStat(setpoint=36.0, delay=0.0, manual_reset=False)
    f.step(30.0, 10.0)
    assert f.tripped
    f.step(70.0, 10.0)
    assert not f.tripped


# --------------------------------------------------------------------------
# Mixing box and the air path
# --------------------------------------------------------------------------


def test_mixed_air_is_the_flow_weighted_average():
    box = MixingBox(outside_damper=Damper(leakage=0.0))
    box.outside_damper.command(50.0)
    box.step(300.0)
    mixed = box.mixed_temp(outside_temp=0.0, return_temp=100.0, fan_running=True)
    frac = box.oa_fraction
    assert mixed == pytest.approx(frac * 0.0 + (1 - frac) * 100.0)


def test_damper_leakage_drags_mixed_air_toward_outside():
    box = MixingBox(outside_damper=Damper(leakage=0.10))
    box.outside_damper.command(0.0)
    box.step(300.0)
    mixed = box.mixed_temp(outside_temp=0.0, return_temp=70.0, fan_running=False)
    assert mixed < 70.0


def test_air_handler_heats_when_the_valve_opens():
    ah = single_zone_ahu()
    ah.apply("sfan", 1.0)
    ah.apply("oa_damper", 0.0)
    for _ in range(60):
        ah.step(5.0, outside_temp=40.0)
    cold = ah.discharge_temp
    ah.apply("hw_valve", 100.0)
    for _ in range(60):
        ah.step(5.0, outside_temp=40.0)
    assert ah.discharge_temp > cold


def test_air_handler_cools_when_the_cooling_valve_opens():
    ah = single_zone_ahu()
    ah.apply("sfan", 1.0)
    ah.apply("oa_damper", 0.0)
    for _ in range(60):
        ah.step(5.0, outside_temp=85.0)
    warm = ah.discharge_temp
    ah.apply("cw_valve", 100.0)
    for _ in range(60):
        ah.step(5.0, outside_temp=85.0)
    assert ah.discharge_temp < warm


def test_open_damper_on_a_cold_day_trips_the_freezestat():
    ah = single_zone_ahu()
    ah.apply("sfan", 1.0)
    ah.apply("oa_damper", 100.0)
    for _ in range(200):
        ah.step(5.0, outside_temp=2.0)
    assert ah.freeze_stat.tripped


# --------------------------------------------------------------------------
# Weather
# --------------------------------------------------------------------------


def test_fixed_weather_holds_its_temperature():
    w = Weather(fixed=12.0)
    assert w.outside_temp(3.0) == 12.0
    assert w.outside_temp(15.0) == 12.0


def test_diurnal_weather_is_coldest_in_the_early_morning():
    w = Weather(low=20.0, high=50.0, min_hour=5.0)
    assert w.outside_temp(5.0) == pytest.approx(20.0, abs=0.5)
    assert w.outside_temp(17.0) == pytest.approx(50.0, abs=0.5)
    assert w.outside_temp(5.0) < w.outside_temp(14.0)


# --------------------------------------------------------------------------
# Plant addressing
# --------------------------------------------------------------------------


def test_plant_read_and_write_by_path():
    plant = build_plant({"preset": "single_zone_ahu"})
    plant.write("AHU1.hw_valve", 100.0)
    plant.step(5.0, hours=8.0)
    assert plant.read("AHU1.hw_position") > 0.0


def test_unknown_system_raises_a_useful_error():
    plant = build_plant({"preset": "single_zone_ahu"})
    with pytest.raises(KeyError) as exc:
        plant.read("AHU9.dat")
    assert "AHU1" in str(exc.value)


def test_unknown_output_lists_what_is_available():
    plant = build_plant({"preset": "single_zone_ahu"})
    with pytest.raises(KeyError) as exc:
        plant.read("AHU1.nonsense")
    assert "dat" in str(exc.value)


def test_plant_overrides_apply():
    plant = build_plant({
        "preset": "single_zone_ahu",
        "overrides": {"AHU1.zone.temperature": 60.0},
    })
    assert plant.systems["AHU1"].zone.temperature == 60.0


# --------------------------------------------------------------------------
# The bench
# --------------------------------------------------------------------------


def _bench(program_text, weather="design_winter", **kw):
    prog = parser.parse(program_text, name="t")
    plant = build_plant({"preset": "single_zone_ahu", "weather": weather})
    return TestBench(prog, plant, default_bindings(), **kw)


def test_bench_rejects_a_mistyped_binding_path():
    prog = parser.parse("10\tON(SFAN)\n20\tGOTO 10\n")
    plant = build_plant({"preset": "single_zone_ahu"})
    with pytest.raises(KeyError):
        TestBench(prog, plant, [Binding("SFAN", "AHU1.no_such_thing", "command")])


def test_bench_rejects_a_bad_binding_direction():
    prog = parser.parse("10\tON(SFAN)\n20\tGOTO 10\n")
    plant = build_plant({"preset": "single_zone_ahu"})
    with pytest.raises(ValueError):
        TestBench(prog, plant, [Binding("SFAN", "AHU1.dat", "sideways")])


def test_commands_reach_the_plant_and_sensors_come_back():
    bench = _bench("10\tON(SFAN)\n20\tSET(100.0,HVLV)\n30\tGOTO 10\n")
    result = bench.run(seconds=600, dt=10)
    assert bench.plant.read("AHU1.hw_position") > 50.0
    assert bench.plant.read("AHU1.sfan_proof") == 1.0
    # The program can read back a sensor the plant produced.
    assert bench.panel.value("DAT") != 0.0
    assert result.series("AHU1.actual_dat")


def test_fault_injection_changes_behaviour():
    bench = _bench("10\tON(SFAN)\n20\tSET(0.0,OADPR)\n30\tGOTO 10\n")
    bench.add_fault(
        Fault(at_seconds=300, system="AHU1", kind="oa_damper_stuck", value=100.0)
    )
    result = bench.run(seconds=2400, dt=10)
    assert result.faults
    assert bench.plant.read("AHU1.freeze_tripped") == 1.0


def test_unknown_fault_is_reported_not_swallowed():
    bench = _bench("10\tON(SFAN)\n20\tGOTO 10\n")
    bench.add_fault(Fault(at_seconds=0, system="AHU1", kind="not_a_fault"))
    result = bench.run(seconds=60, dt=5)
    assert any("not_a_fault" in w for w in result.warnings)


def test_checks_pass_and_fail_as_expected():
    bench = _bench("10\tON(SFAN)\n20\tSET(0.0,OADPR)\n30\tGOTO 10\n")
    bench.add_check(Check(kind="never_true", var="AHU1.freeze_tripped",
                          name="no freeze"))
    bench.add_check(Check(kind="eventually", var="AHU1.sfan_proof",
                          name="fan proves"))
    result = bench.run(seconds=900, dt=10)
    outcomes = {c.name: c.passed for c in result.checks}
    assert outcomes["no freeze"] is True
    assert outcomes["fan proves"] is True


def test_check_on_a_missing_variable_fails_loudly():
    bench = _bench("10\tON(SFAN)\n20\tGOTO 10\n")
    bench.add_check(Check(kind="within", var="AHU1.nope", low=0, high=1))
    result = bench.run(seconds=120, dt=5)
    assert result.checks[0].passed is False
    assert "no samples" in result.checks[0].detail


def test_starved_lines_are_surfaced_by_the_bench():
    text = (
        "10\tON(SFAN)\n"
        "20\tON(RFAN)\n"
        "30\tGOTO 20\n"
        "40\tSET(50.0,HVLV)\n"
    )
    bench = _bench(text)
    result = bench.run(seconds=300, dt=10)
    assert any("never ran" in w for w in result.warnings)


# --------------------------------------------------------------------------
# End to end: the generated template against real weather
# --------------------------------------------------------------------------


@pytest.mark.parametrize("weather", ["design_winter", "design_summer", "shoulder"])
def test_generated_ahu_template_holds_the_zone(weather):
    """The shipped template must actually control, not merely lint clean."""
    prog = parser.parse(generator.air_handler(), name="AHU1")
    plant = build_plant({"preset": "single_zone_ahu", "weather": weather})
    bench = TestBench(prog, plant, default_bindings())
    for check in default_checks():
        bench.add_check(check)
    result = bench.run(seconds=7200, dt=10)
    failures = [c for c in result.checks if not c.passed]
    assert not failures, "\n".join("%s: %s" % (c.name, c.detail) for c in failures)


def test_generated_template_sequences_heating_and_cooling_apart():
    """Both valves must never be open at once -- that is simultaneous h/c."""
    prog = parser.parse(generator.air_handler(), name="AHU1")
    plant = build_plant({"preset": "single_zone_ahu", "weather": "shoulder"})
    bench = TestBench(prog, plant, default_bindings())
    result = bench.run(seconds=7200, dt=10)
    for sample in result.history:
        hw = sample["values"]["AHU1.hw_position"]
        cw = sample["values"]["AHU1.cw_position"]
        assert min(hw, cw) < 5.0, (
            "both valves open at t+%.0fs: HW %.1f%%, CW %.1f%%"
            % (sample["elapsed"], hw, cw)
        )
