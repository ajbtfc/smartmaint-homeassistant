"""The entity catalogue: what Home Assistant could send, for Ninestitch's setup screen.

Names, areas, units, device classes and current states of sensors, binary sensors and thermostats.
Uploaded on start, a minute after entities are added, renamed or removed (#113), and daily.
Values are sent only for entities linked in Ninestitch.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CATALOGUE_DOMAINS, IGNORED_STATES

MAX_ENTITIES = 2000


def build_catalogue(hass: HomeAssistant) -> list[dict[str, Any]]:
    ents = er.async_get(hass)
    devs = dr.async_get(hass)
    areas = ar.async_get(hass)
    out: list[dict[str, Any]] = []
    for state in hass.states.async_all(CATALOGUE_DOMAINS):
        entry = ents.async_get(state.entity_id)
        if entry is not None and (entry.disabled_by or entry.hidden_by or entry.platform == "ninestitch"):
            continue  # hidden, disabled, or one of our own (open jobs…)
        area_id = entry.area_id if entry else None
        if area_id is None and entry and entry.device_id and (device := devs.async_get(entry.device_id)):
            area_id = device.area_id
        area = areas.async_get_area(area_id) if area_id else None
        out.append(
            {
                "entity_id": state.entity_id,
                "name": state.name,
                "area": area.name if area else None,
                "domain": state.domain,
                "device_class": state.attributes.get("device_class"),
                "unit": state.attributes.get("unit_of_measurement"),
                "state": None if state.state in IGNORED_STATES else state.state[:200],
            }
        )
    # people too, for "Who is who" in Ninestitch
    for state in hass.states.async_all("person"):
        out.append(
            {
                "entity_id": state.entity_id,
                "name": state.name,
                "area": None,
                "domain": "person",
                "device_class": None,
                "unit": None,
                "state": None,
            }
        )
    return out[:MAX_ENTITIES]
