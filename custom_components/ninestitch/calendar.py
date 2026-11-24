"""calendar.ninestitch: open jobs' due dates, and when routines are next due."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import NinestitchConfigEntry
from .coordinator import NinestitchCoordinator
from .entity import NinestitchEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: NinestitchConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([NinestitchCalendar(entry.runtime_data.coordinator)])


def _event(item: dict[str, Any]) -> CalendarEvent | None:
    try:
        day = date.fromisoformat(str(item.get("date")))
    except ValueError:
        return None
    title = str(item.get("title") or "")
    thing = item.get("thing")
    if item.get("kind") == "job":
        summary = f"#{item.get('number')} {title}"
        description = f"Job in Ninestitch{f' for {thing}' if thing else ''}."
    else:
        summary = title
        description = f"Routine{f' for {thing}' if thing else ''}: a job is created when it comes due."
    return CalendarEvent(
        start=day,
        end=day + timedelta(days=1),
        summary=f"{summary} ({thing})" if thing else summary,
        description=description,
        uid=f"{item.get('kind')}-{item.get('number') or title}-{day.isoformat()}",
    )


class NinestitchCalendar(NinestitchEntity, CalendarEntity):
    _attr_translation_key = "due"

    def __init__(self, coordinator: NinestitchCoordinator) -> None:
        super().__init__(coordinator, "calendar")

    def _events(self) -> list[CalendarEvent]:
        items = (self.coordinator.data or {}).get("calendar") or []
        return sorted((e for e in map(_event, items) if e), key=lambda e: e.start)

    @property
    def event(self) -> CalendarEvent | None:
        today = dt_util.now().date()
        return next((e for e in self._events() if e.end > today), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        start, end = start_date.date(), end_date.date()
        return [e for e in self._events() if e.start <= end and e.end > start]
