"""Notifications reach the person's phone (their mobile app notify service), or Home Assistant's
notifications when they have none (#34)."""

from __future__ import annotations

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ninestitch.notify import async_notify_person, phone_services


async def test_to_the_persons_phone(hass: HomeAssistant) -> None:
    phone_entry = MockConfigEntry(domain="mobile_app")
    phone_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=phone_entry.entry_id, identifiers={("mobile_app", "sam-phone")}, name="Sam’s Pixel"
    )
    er.async_get(hass).async_get_or_create(
        "device_tracker", "mobile_app", "sam-phone", device_id=device.id, suggested_object_id="sams_pixel"
    )
    hass.states.async_set("person.sam", "home", {"device_trackers": ["device_tracker.sams_pixel"]})
    sent: list[ServiceCall] = []
    hass.services.async_register("notify", "mobile_app_sams_pixel", sent.append)

    assert phone_services(hass, "person.sam") == ["mobile_app_sams_pixel"]
    await async_notify_person(
        hass, "person.sam", "Finish it", "Needs readings", tag="t1", url="https://app.example/work/7"
    )
    assert len(sent) == 1
    assert sent[0].data["title"] == "Finish it"
    assert sent[0].data["data"]["url"] == "https://app.example/work/7"
    assert sent[0].data["data"]["clickAction"] == "https://app.example/work/7"


async def test_no_phone_goes_to_home_assistants_notifications(hass: HomeAssistant) -> None:
    hass.states.async_set("person.alex", "home", {"device_trackers": []})
    await async_notify_person(hass, "person.alex", "Urgent job", "Leak", tag="t2", url="https://app.example/work/8")
    notes = persistent_notification._async_get_or_create_notifications(hass)
    assert notes["t2"]["title"] == "Urgent job"
    assert "https://app.example/work/8" in notes["t2"]["message"]
