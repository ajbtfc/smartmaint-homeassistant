"""A to-do list per person: their open jobs in Ninestitch (todo.ninestitch_<person>).

People are matched on Ninestitch's Household → Home Assistant → "Who is who". Jobs that aren't
assigned to anyone matched go on one shared list. Ticking a job off marks it done in Ninestitch, as
that person; one that still needs readings stays open, and a notification links to it to finish there.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any

from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity, TodoListEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from . import NinestitchConfigEntry
from .api import NinestitchError
from .coordinator import NinestitchCoordinator
from .entity import NinestitchEntity
from .notify import async_notify_person

_LOGGER = logging.getLogger(__name__)
EVERYONE = "everyone"


def _people(data: dict[str, Any] | None) -> dict[str, str]:
    """Home Assistant person → their name in Ninestitch, for everyone matched."""
    out: dict[str, str] = {}
    for member in ((data or {}).get("people") or {}).values():
        if isinstance(member, dict) and member.get("person"):
            out[str(member["person"])] = str(member.get("name") or member["person"])
    return out


async def async_setup_entry(
    hass: HomeAssistant, entry: NinestitchConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        data = coordinator.data or {}
        if not (data.get("out") or {}).get("todo", True):
            return
        new = [NinestitchTodoList(coordinator, EVERYONE, None)] if EVERYONE not in known else []
        known.add(EVERYONE)
        for person, name in _people(data).items():
            if person not in known:
                known.add(person)
                new.append(NinestitchTodoList(coordinator, person, name))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class NinestitchTodoList(NinestitchEntity, TodoListEntity):
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    def __init__(self, coordinator: NinestitchCoordinator, person: str, name: str | None) -> None:
        super().__init__(coordinator, f"todo_{person}")
        self.person = None if person == EVERYONE else person
        if self.person is None:
            self._attr_translation_key = "everyone"
        else:
            self._attr_name = name
        # todo.ninestitch_sam: short and the same in every household (the design's name for it)
        self.entity_id = f"todo.ninestitch_{slugify(person.removeprefix('person.'))}"

    def _mine(self, job: dict[str, Any]) -> bool:
        people = _people(self.coordinator.data)
        person = job.get("person")
        if self.person is None:
            return not person or person not in people
        return person == self.person

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        if self.person is not None and self.person not in _people(self.coordinator.data):
            return False  # no longer matched on "Who is who"
        return True

    @property
    def todo_items(self) -> list[TodoItem]:
        items = []
        for job in (self.coordinator.data or {}).get("jobs") or []:
            if not self._mine(job):
                continue
            due = job.get("due_date")
            details = [f"#{job.get('number')}"]
            if job.get("priority") == "urgent":
                details.append("Urgent")
            if job.get("status") == "in_progress":
                details.append("Started")
            if job.get("due_meter_left") is not None and job.get("meter_unit"):
                details.append(f"{job['due_meter_left']:,.0f} {job['meter_unit']} left")
            items.append(
                TodoItem(
                    uid=str(job["id"]),
                    summary=f"{job.get('title')} ({job.get('thing')})" if job.get("thing") else str(job.get("title")),
                    status=TodoItemStatus.NEEDS_ACTION,
                    due=date.fromisoformat(due) if due else None,
                    description=" · ".join(details),
                )
            )
        return items

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Ticked off: done in Ninestitch, as this person."""
        if item.status != TodoItemStatus.COMPLETED:
            return
        job = next((j for j in (self.coordinator.data or {}).get("jobs") or [] if str(j["id"]) == item.uid), None)
        if job is None:
            raise HomeAssistantError("That job is no longer open in Ninestitch")
        person = self.person or job.get("person")
        try:
            result = await self.coordinator.api.action(str(job["id"]), "done", person, str(uuid.uuid4()))
        except NinestitchError as err:
            raise HomeAssistantError(f"Ninestitch didn't mark it done: {err.message}") from err
        if result.get("needs_input"):
            await async_notify_person(
                self.hass,
                person,
                title=f"Finish “{job.get('title')}” in Ninestitch",
                message=str(result.get("message") or "It needs readings before it can be marked done."),
                tag=f"ninestitch_{job['id']}_needs_input",
                url=result.get("url"),
            )
        await self.coordinator.async_request_refresh()
