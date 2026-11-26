"""Dashboard sensors: open jobs, overdue jobs, and readings waiting to be sent."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NinestitchConfigEntry, NinestitchData
from .coordinator import NinestitchCoordinator
from .entity import NinestitchEntity


@dataclass(frozen=True, kw_only=True)
class NinestitchSensorDescription(SensorEntityDescription):
    value: Callable[[dict[str, Any], NinestitchData], int | None]


SENSORS = (
    NinestitchSensorDescription(
        key="open_jobs",
        translation_key="open_jobs",
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda feed, _: (feed.get("counts") or {}).get("open"),
    ),
    NinestitchSensorDescription(
        key="overdue_jobs",
        translation_key="overdue_jobs",
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda feed, _: (feed.get("counts") or {}).get("overdue"),
    ),
    NinestitchSensorDescription(
        key="queued_readings",
        translation_key="queued_readings",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda _, rt: len(rt.queue),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: NinestitchConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    rt = entry.runtime_data
    async_add_entities(NinestitchSensor(rt.coordinator, rt, d) for d in SENSORS)


class NinestitchSensor(NinestitchEntity, SensorEntity):
    entity_description: NinestitchSensorDescription

    def __init__(
        self, coordinator: NinestitchCoordinator, rt: NinestitchData, description: NinestitchSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._rt = rt

    @property
    def native_value(self) -> int | None:
        return self.entity_description.value(self.coordinator.data or {}, self._rt)
