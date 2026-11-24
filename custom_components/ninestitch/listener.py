"""Listens to the entities linked in Ninestitch, and only those, and queues their readings.

Which entities, and what each is for, comes from the feed (`links`), so linking or unlinking one in
Ninestitch takes effect within a minute. Values of entities that aren't linked never leave Home
Assistant.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import RUNTIME_TICK
from .policy import RunHours, SendPolicy, counts_run_hours, is_running
from .queue import ReadingQueue

_LOGGER = logging.getLogger(__name__)

CUMULATIVE_PURPOSES = ("meter", "runtime")


class ReadingListener:
    """Turns state changes of linked entities into queued readings."""

    def __init__(
        self,
        hass: HomeAssistant,
        queue: ReadingQueue,
        runtime_store: Store,
        on_queued: Callable[[], None],
        now: Callable[[], float] = time.time,
    ) -> None:
        self.hass = hass
        self.queue = queue
        self._store = runtime_store
        self._on_queued = on_queued
        self._now = now
        self.links: dict[str, dict[str, Any]] = {}
        self.policy = SendPolicy()
        self.run_hours = RunHours()
        self._unsub_states: CALLBACK_TYPE | None = None
        self._unsub_tick: CALLBACK_TYPE | None = None
        self._pending: dict[str, CALLBACK_TYPE] = {}

    async def async_start(self) -> None:
        self.run_hours = RunHours.from_dict(await self._store.async_load())
        self._unsub_tick = async_track_time_interval(self.hass, self._async_tick, RUNTIME_TICK)

    async def async_stop(self) -> None:
        for unsub in (self._unsub_states, self._unsub_tick, *self._pending.values()):
            if unsub:
                unsub()
        self._pending.clear()
        self._unsub_states = self._unsub_tick = None
        await self._async_save_run_hours()

    @callback
    def set_links(self, links: list[dict[str, Any]]) -> None:
        """The linked entities from the feed. Re-subscribes when they change, and sends the current
        value of each new cumulative meter (so a restart doesn't leave an odometer behind)."""
        new = {link["entity_id"]: link for link in links if isinstance(link, dict) and link.get("entity_id")}
        if new == self.links:
            return
        added = [e for e in new if e not in self.links or new[e] != self.links[e]]
        for gone in set(self.links) - set(new):
            self.policy.forget(gone)
            if (unsub := self._pending.pop(gone, None)) is not None:
                unsub()
        self.links = new
        if self._unsub_states:
            self._unsub_states()
            self._unsub_states = None
        if new:
            self._unsub_states = async_track_state_change_event(self.hass, list(new), self._handle_event)
        now = self._now()
        for entity_id in added:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            link = new[entity_id]
            if counts_run_hours(entity_id, link.get("purpose")):
                # don't count time before now (Home Assistant may have been down)
                self.run_hours.update(entity_id, False, now)
                self.run_hours.update(entity_id, is_running(state.domain, state.state, dict(state.attributes)), now)
                self._send_run_hours(entity_id, now, force=True)
            elif link.get("purpose") in CUMULATIVE_PURPOSES:
                self.policy.forget(entity_id)
                self._consider(entity_id, state, now)
        _LOGGER.debug("Listening to %d linked entities", len(new))

    @callback
    def _handle_event(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        state = event.data["new_state"]
        if state is None or entity_id not in self.links:
            return
        now = self._now()
        if counts_run_hours(entity_id, self.links[entity_id].get("purpose")):
            self.run_hours.update(entity_id, is_running(state.domain, state.state, dict(state.attributes)), now)
            self._send_run_hours(entity_id, now)
            return
        self._consider(entity_id, state, now)

    def _consider(self, entity_id: str, state: State, now: float) -> None:
        cumulative = self.links[entity_id].get("purpose") in CUMULATIVE_PURPOSES
        delay = self.policy.decide(entity_id, state.state, now, cumulative)
        if delay is None:
            return
        if delay <= 0:
            self._queue_state(entity_id, state, now)
            return
        if entity_id in self._pending:
            return  # a send is already scheduled; it picks up the latest state

        @callback
        def _later(_: datetime) -> None:
            self._pending.pop(entity_id, None)
            latest = self.hass.states.get(entity_id)
            if latest is not None and entity_id in self.links:
                self._consider(entity_id, latest, self._now())

        self._pending[entity_id] = async_call_later(self.hass, delay, _later)

    def _queue_state(self, entity_id: str, state: State, now: float) -> None:
        reading: dict[str, Any] = {
            "entity_id": entity_id,
            "state": state.state,
            "at": state.last_changed.isoformat(),
        }
        if unit := state.attributes.get("unit_of_measurement"):
            reading["unit"] = unit
        self.queue.add(reading)
        self.policy.mark_sent(entity_id, state.state, now)
        self._on_queued()

    def _send_run_hours(self, entity_id: str, now: float, force: bool = False) -> None:
        value = f"{self.run_hours.value(entity_id, now):.3f}"
        if not force and self.policy.decide(entity_id, value, now, cumulative=True) != 0:
            return
        self.queue.add(
            {
                "entity_id": entity_id,
                "state": value,
                "unit": "h",
                "at": dt_util.utc_from_timestamp(now).isoformat(),
            }
        )
        self.policy.mark_sent(entity_id, value, now)
        self._on_queued()

    async def _async_tick(self, _: datetime | None = None) -> None:
        """Every 15 minutes: run hours of anything still running, and keep the counters saved."""
        now = self._now()
        for entity_id, link in self.links.items():
            if counts_run_hours(entity_id, link.get("purpose")):
                self._send_run_hours(entity_id, now)
        await self._async_save_run_hours()

    async def _async_save_run_hours(self) -> None:
        await self._store.async_save(self.run_hours.as_dict(self._now()))
