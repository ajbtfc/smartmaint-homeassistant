"""The integration end to end against a fake Ninestitch: to-do lists, calendar, sensors,
notifications, readings from linked entities only, ticking a job off, and a disconnected token."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from homeassistant.components.todo import DOMAIN as TODO_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ninestitch.const import DOMAIN
from custom_components.ninestitch.diagnostics import async_get_config_entry_diagnostics

from .conftest import TOKEN, URL, error, feed


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    e = MockConfigEntry(
        domain=DOMAIN,
        title="Maple Street",
        unique_id="hh-1",
        data={
            "token": TOKEN,
            "url": "https://louiosrbcxohskeibsfe.supabase.co",
            "household_id": "hh-1",
            "device_id": "dev-1",
        },
    )
    e.add_to_hass(hass)
    return e


def calls(aioclient_mock: AiohttpClientMocker, endpoint: str) -> list[Any]:
    return [c[2] for c in aioclient_mock.mock_calls if str(c[1]).endswith(endpoint)]


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, **feed_over: Any
) -> None:
    aioclient_mock.get(f"{URL}/ha-feed", json=feed(**feed_over), headers={"etag": 'W/"v1"'})
    aioclient_mock.post(f"{URL}/ha-catalogue", json={"stored": 3})
    aioclient_mock.post(f"{URL}/ha-readings", json={"accepted": 1, "ignored": 0, "duplicates": 0, "rejected": []})
    aioclient_mock.post(f"{URL}/ha-ack", json={"acknowledged": 1})
    hass.states.async_set("person.sam", "home", {"friendly_name": "Sam"})
    hass.states.async_set(
        "sensor.outback_odometer", "60000", {"unit_of_measurement": "mi", "friendly_name": "Outback odometer"}
    )
    hass.states.async_set("binary_sensor.water_heater_leak", "off", {"device_class": "moisture"})
    hass.states.async_set("climate.hallway", "heat", {"hvac_action": "idle"})
    hass.states.async_set("sensor.living_room_temperature", "21.5", {"unit_of_measurement": "°C"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _todo_items(hass: HomeAssistant, entity_id: str) -> list[dict[str, Any]]:
    res = await hass.services.async_call(
        TODO_DOMAIN, "get_items", {"entity_id": entity_id}, blocking=True, return_response=True
    )
    return res[entity_id]["items"]


async def test_to_do_lists_per_person_calendar_and_counts(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    await _setup(hass, entry, aioclient_mock)
    assert entry.state is ConfigEntryState.LOADED

    sam = await _todo_items(hass, "todo.ninestitch_sam")
    assert [i["summary"] for i in sam] == ["Replace furnace filter (House)"]
    assert sam[0]["due"] == "2026-12-01"
    everyone = await _todo_items(hass, "todo.ninestitch_everyone")
    assert [i["summary"] for i in everyone] == ["Water heater leak (House)"], "jobs for nobody matched"
    assert "Urgent" in everyone[0]["description"]

    assert hass.states.get("sensor.ninestitch_maple_street_open_jobs").state == "2"
    assert hass.states.get("sensor.ninestitch_maple_street_overdue_jobs").state == "1"
    cal = hass.states.get("calendar.ninestitch_maple_street_due")
    assert cal is not None and cal.attributes["message"].startswith("#7 Replace furnace filter")


async def test_catalogue_uploaded_on_start_with_people_and_no_values_of_unlinked_entities(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    await _setup(hass, entry, aioclient_mock)
    [uploaded] = calls(aioclient_mock, "/ha-catalogue")
    ids = {e["entity_id"]: e for e in uploaded["entities"]}
    assert {"sensor.outback_odometer", "binary_sensor.water_heater_leak", "climate.hallway", "person.sam"} <= set(ids)
    assert not any(e.startswith("sensor.ninestitch_") for e in ids), "not our own sensors"
    assert ids["sensor.outback_odometer"]["unit"] == "mi"
    # readings: only linked entities, and the odometer's current value is sent on start
    sent = [r for batch in calls(aioclient_mock, "/ha-readings") for r in batch["readings"]]
    assert {r["entity_id"] for r in sent} <= {"sensor.outback_odometer", "climate.hallway"}
    assert any(r["entity_id"] == "sensor.outback_odometer" and r["state"] == "60000" for r in sent)


async def test_a_linked_entity_changing_is_sent_and_an_unlinked_one_isnt(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    await _setup(hass, entry, aioclient_mock)
    before = len(calls(aioclient_mock, "/ha-readings"))
    hass.states.async_set("binary_sensor.water_heater_leak", "on", {"device_class": "moisture"})
    hass.states.async_set("sensor.living_room_temperature", "22.0", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    new = [r for batch in calls(aioclient_mock, "/ha-readings")[before:] for r in batch["readings"]]
    assert [r["entity_id"] for r in new] == ["binary_sensor.water_heater_leak"]
    assert new[0]["state"] == "on"


async def test_run_hours_are_counted_for_a_thermostat(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    await _setup(hass, entry, aioclient_mock)
    listener = entry.runtime_data.listener
    start = listener._now()
    listener._now = lambda: start
    hass.states.async_set("climate.hallway", "heat", {"hvac_action": "heating"})
    await hass.async_block_till_done()
    listener._now = lambda: start + 2 * 3600
    hass.states.async_set("climate.hallway", "heat", {"hvac_action": "idle"})
    await hass.async_block_till_done()
    sent = [
        r
        for batch in calls(aioclient_mock, "/ha-readings")
        for r in batch["readings"]
        if r["entity_id"] == "climate.hallway"
    ]
    assert sent[-1]["state"] == "2.000" and sent[-1]["unit"] == "h"


async def test_notifications_shown_then_acknowledged(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    note = {
        "id": 41,
        "ids": [41, 42],
        "kind": "urgent",
        "person": None,
        "title": "Urgent job",
        "message": "Water heater leak (House)",
        "number": 8,
        "work_order_id": "22222222-2222-4222-8222-222222222222",
    }
    await _setup(hass, entry, aioclient_mock, notifications=[note])
    assert calls(aioclient_mock, "/ha-ack") == [{"ids": [41, 42]}]
    from homeassistant.components import persistent_notification

    notes = persistent_notification._async_get_or_create_notifications(hass)
    assert any(n["title"] == "Urgent job" for n in notes.values()), "nobody matched: Home Assistant's notifications"


async def test_ticking_a_job_off_marks_it_done_as_that_person(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    await _setup(hass, entry, aioclient_mock)
    aioclient_mock.post(f"{URL}/ha-action", json={"ok": True, "status": "done"})
    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {"entity_id": "todo.ninestitch_sam", "item": "11111111-1111-4111-8111-111111111111", "status": "completed"},
        blocking=True,
    )
    [action] = calls(aioclient_mock, "/ha-action")
    assert action["work_order_id"] == "11111111-1111-4111-8111-111111111111"
    assert action["action"] == "done" and action["person"] == "person.sam"
    assert action["idempotency_key"]


async def test_a_job_that_needs_readings_sends_a_link_to_finish_it(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    await _setup(hass, entry, aioclient_mock)
    aioclient_mock.post(
        f"{URL}/ha-action",
        json={
            "ok": False,
            "needs_input": True,
            "url": "https://app.example/work/7",
            "message": "Fill in Pressure before marking this done",
        },
    )
    await hass.services.async_call(
        TODO_DOMAIN,
        "update_item",
        {"entity_id": "todo.ninestitch_sam", "item": "11111111-1111-4111-8111-111111111111", "status": "completed"},
        blocking=True,
    )
    from homeassistant.components import persistent_notification

    notes = persistent_notification._async_get_or_create_notifications(hass)
    [n] = [n for n in notes.values() if "Finish" in n["title"]]
    assert "https://app.example/work/7" in n["message"]


async def test_disconnected_in_ninestitch_asks_to_pair_again(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{URL}/ha-feed", status=401, json=error("token_invalid"))
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_not_on_plus_raises_a_repair_issue(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    from homeassistant.helpers import issue_registry as ir

    aioclient_mock.get(f"{URL}/ha-feed", status=402, json=error("plan_requires_live_sensors"))
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"plan_required_{entry.entry_id}") is not None


async def test_unchanged_feed_keeps_the_last_answer(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    await _setup(hass, entry, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/ha-feed", status=304)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.ninestitch_maple_street_open_jobs").state == "2"
    # the tag comes back exactly as the gateway sent it (in whatever case it named the header)
    [polled] = [c for c in aioclient_mock.mock_calls if str(c[1]).endswith("/ha-feed")]
    assert polled[3]["If-None-Match"] == 'W/"v1"'


async def test_diagnostics_leave_out_the_token(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    await _setup(hass, entry, aioclient_mock)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["token"] == "**REDACTED**"
    assert TOKEN not in str(diag)
    assert "sensor.outback_odometer" in diag["listening_to"]


async def test_a_new_helper_reaches_ninestitch_without_waiting_a_day(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A utility meter made after pairing (#113): the entity list goes again after a minute."""
    from homeassistant.helpers import entity_registry as er

    await _setup(hass, entry, aioclient_mock)
    assert len(calls(aioclient_mock, "/ha-catalogue")) == 1
    er.async_get(hass).async_get_or_create(
        "sensor", "utility_meter", "hvac-filter", suggested_object_id="kitchen_my_ecobee_hvac_filter_runtime"
    )
    hass.states.async_set(
        "sensor.kitchen_my_ecobee_hvac_filter_runtime",
        "1234",
        {"unit_of_measurement": "min", "device_class": "duration"},
    )
    hass.states.async_set("sensor.another_new_one", "1", {})  # a burst: one upload covers both
    await hass.async_block_till_done()
    assert len(calls(aioclient_mock, "/ha-catalogue")) == 1, "not at once"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    uploads = calls(aioclient_mock, "/ha-catalogue")
    assert len(uploads) == 2
    meter = next(e for e in uploads[1]["entities"] if e["entity_id"] == "sensor.kitchen_my_ecobee_hvac_filter_runtime")
    assert meter["unit"] == "min" and meter["device_class"] == "duration"


async def test_ordinary_state_changes_dont_upload_the_list(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    await _setup(hass, entry, aioclient_mock)
    hass.states.async_set("sensor.living_room_temperature", "23.0", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert len(calls(aioclient_mock, "/ha-catalogue")) == 1
