"""Notifications to a person's phone through the Home Assistant app.

A person (person.sam) has device trackers; a tracker that belongs to the mobile app has a
`notify.mobile_app_<device>` service. Without one (no person mapped, or no phone), the notification
goes to Home Assistant's own notifications instead.
"""

from __future__ import annotations

import logging

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

_LOGGER = logging.getLogger(__name__)


def phone_services(hass: HomeAssistant, person: str | None) -> list[str]:
    """notify service names (without the domain) for a person's phones."""
    if not person:
        return []
    state = hass.states.get(person)
    if state is None:
        return []
    ents = er.async_get(hass)
    devs = dr.async_get(hass)
    services: list[str] = []
    for tracker in state.attributes.get("device_trackers") or []:
        entry = ents.async_get(tracker)
        if entry is None or entry.platform != "mobile_app" or entry.device_id is None:
            continue
        device = devs.async_get(entry.device_id)
        if device is None:
            continue
        name = f"mobile_app_{slugify(device.name_by_user or device.name or '')}"
        if hass.services.has_service("notify", name):
            services.append(name)
    return services


async def async_notify_person(
    hass: HomeAssistant,
    person: str | None,
    title: str,
    message: str,
    tag: str,
    url: str | None = None,
) -> None:
    """Show a notification to a person, or to Home Assistant if they have no phone."""
    services = phone_services(hass, person)
    if not services:
        text = f"{message}\n\n[Open in Ninestitch]({url})" if url else message
        persistent_notification.async_create(hass, text, title=title, notification_id=tag)
        return
    data: dict[str, object] = {"tag": tag, "group": "ninestitch"}
    if url:
        data["url"] = url  # Android
        data["clickAction"] = url  # iOS
    for service in services:
        await hass.services.async_call(
            "notify", service, {"title": title, "message": message, "data": data}, blocking=True
        )
