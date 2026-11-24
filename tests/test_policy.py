"""What gets sent and when (noise limits), and run-hour counting (#34)."""

from __future__ import annotations

from custom_components.ninestitch.policy import RunHours, SendPolicy, counts_run_hours, is_running


def test_on_change_at_most_once_a_minute_and_the_last_state_isnt_lost() -> None:
    p = SendPolicy()
    assert p.decide("binary_sensor.leak", "on", 0, cumulative=False) == 0
    p.mark_sent("binary_sensor.leak", "on", 0)
    assert p.decide("binary_sensor.leak", "on", 5, cumulative=False) is None, "unchanged"
    assert p.decide("binary_sensor.leak", "off", 20, cumulative=False) == 40, "sent when the minute is up"
    assert p.decide("binary_sensor.leak", "off", 61, cumulative=False) == 0


def test_unknown_and_unavailable_are_dropped() -> None:
    p = SendPolicy()
    for state in ("unknown", "unavailable", "", "None"):
        assert p.decide("sensor.x", state, 0, cumulative=False) is None


def test_cumulative_meters_hourly_or_on_a_meaningful_change() -> None:
    p = SendPolicy()
    p.mark_sent("sensor.odo", "60000", 0)
    assert p.decide("sensor.odo", "60010", 120, cumulative=True) == 3600 - 120, "10 mi isn't 1 %: wait for the hour"
    assert p.decide("sensor.odo", "60700", 120, cumulative=True) == 0, "700 mi is"
    assert p.decide("sensor.odo", "60010", 30, cumulative=True) is not None
    assert p.decide("sensor.odo", "59000", 120, cumulative=True) == 0, "going backwards (a reset) goes now"
    assert p.decide("sensor.odo", "60010", 3600, cumulative=True) == 0


def test_running() -> None:
    assert is_running("climate", "heat", {"hvac_action": "heating"})
    assert not is_running("climate", "heat", {"hvac_action": "idle"})
    assert is_running("binary_sensor", "on", {})
    assert not is_running("switch", "off", {})
    assert counts_run_hours("climate.hallway", "runtime")
    assert not counts_run_hours("sensor.furnace_run_hours", "runtime"), "a run-hours sensor is sent as it is"
    assert not counts_run_hours("climate.hallway", "record")


def test_run_hours_count_while_running_and_survive_a_restart() -> None:
    r = RunHours()
    r.update("climate.hallway", True, 0)
    r.update("climate.hallway", False, 1800)
    r.update("climate.hallway", True, 7200)
    assert r.value("climate.hallway", 9000) == 1.0
    saved = r.as_dict(9000)
    restored = RunHours.from_dict(saved)
    assert restored.value("climate.hallway", 99_999) == 1.0, "time while Home Assistant was down isn't counted"
