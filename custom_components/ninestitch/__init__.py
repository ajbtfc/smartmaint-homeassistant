"""Ninestitch: household maintenance, with Home Assistant's sensors and to-do lists.

In: linked entities' readings (odometers, run hours, leak and battery alerts) go to Ninestitch,
through a persisted offline queue. Out: each person's open jobs as a to-do list, a calendar, open
and overdue counts, and notifications to their phone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store

from .api import AuthFailed, NinestitchApi, NinestitchError
from .catalogue import build_catalogue
from .const import CATALOGUE_INTERVAL, CONF_TOKEN, CONF_URL, DEFAULT_URL, DOMAIN, PLATFORMS, QUEUE_TICK
from .coordinator import NinestitchCoordinator
from .listener import ReadingListener
from .queue import ReadingQueue

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1


@dataclass
class NinestitchData:
    api: NinestitchApi
    queue: ReadingQueue
    listener: ReadingListener
    coordinator: NinestitchCoordinator


type NinestitchConfigEntry = ConfigEntry[NinestitchData]


async def async_setup_entry(hass: HomeAssistant, entry: NinestitchConfigEntry) -> bool:
    api = NinestitchApi(async_get_clientsession(hass), entry.data.get(CONF_URL, DEFAULT_URL), entry.data[CONF_TOKEN])
    queue = ReadingQueue(api, Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.queue"))
    await queue.async_load()

    flushing = False

    async def _async_flush() -> None:
        nonlocal flushing
        if flushing or not queue.due():
            return
        flushing = True
        try:
            await queue.flush()
        finally:
            flushing = False
        if queue.auth_failed:
            entry.async_start_reauth(hass)

    @callback
    def _request_flush() -> None:
        entry.async_create_background_task(hass, _async_flush(), "ninestitch_flush")

    listener = ReadingListener(
        hass, queue, Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.run_hours"), _request_flush
    )
    await listener.async_start()
    coordinator = NinestitchCoordinator(hass, entry, api, listener)
    entry.runtime_data = NinestitchData(api, queue, listener, coordinator)

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        await listener.async_stop()
        raise

    async def _async_queue_tick(_: datetime) -> None:
        await _async_flush()

    async def _async_upload_catalogue(_: object = None) -> None:
        try:
            stored = await api.upload_catalogue(build_catalogue(hass))
            _LOGGER.debug("Uploaded %d entities to Ninestitch", stored)
        except AuthFailed:
            entry.async_start_reauth(hass)
        except NinestitchError as err:
            _LOGGER.info("Couldn't upload the entity list to Ninestitch: %s", err.message)

    unsubs: list[CALLBACK_TYPE] = [
        async_track_time_interval(hass, _async_queue_tick, QUEUE_TICK),
        async_track_time_interval(hass, _async_upload_catalogue, CATALOGUE_INTERVAL),
    ]

    @callback
    def _started(_: HomeAssistant) -> None:
        # once everything has loaded: integrations add entities while Home Assistant starts
        entry.async_create_background_task(hass, _async_upload_catalogue(), "ninestitch_catalogue")

    unsubs.append(async_at_started(hass, _started))
    for unsub in unsubs:
        entry.async_on_unload(unsub)
    _request_flush()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NinestitchConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.listener.async_stop()
    await entry.runtime_data.queue.async_save()
    return True
