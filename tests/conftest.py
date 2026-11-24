"""Shared test set-up: a fake Ninestitch, answering like the real endpoints do."""

from __future__ import annotations

from typing import Any

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

URL = "https://louiosrbcxohskeibsfe.supabase.co/functions/v1"
TOKEN = "sm_ha_" + "a" * 64


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def error(code: str, message: str = "No", **params: Any) -> dict[str, Any]:
    """A Ninestitch error body: details is a JSON string with the stable code."""
    import json

    return {"code": "HTTP", "message": message, "details": json.dumps({"code": code, **params})}


def feed(**over: Any) -> dict[str, Any]:
    """A feed like ha-feed's: two people, one matched to a Home Assistant person."""
    data: dict[str, Any] = {
        "household": "Maple Street",
        "household_id": "hh-1",
        "device_id": "dev-1",
        "out": {"todo": True, "calendar": True, "notify_assigned": True, "notify_urgent": True, "notify_due": False},
        "people": {
            "u-sam": {"name": "Sam", "person": "person.sam"},
            "u-alex": {"name": "Alex", "person": None},
        },
        "jobs": [
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "number": 7,
                "title": "Replace furnace filter",
                "thing": "House",
                "status": "open",
                "priority": "medium",
                "due_date": "2026-12-01",
                "due_meter_left": None,
                "meter_unit": None,
                "person": "person.sam",
            },
            {
                "id": "22222222-2222-4222-8222-222222222222",
                "number": 8,
                "title": "Water heater leak",
                "thing": "House",
                "status": "open",
                "priority": "urgent",
                "due_date": None,
                "due_meter_left": None,
                "meter_unit": None,
                "person": None,
            },
        ],
        "calendar": [
            {"kind": "job", "number": 7, "title": "Replace furnace filter", "thing": "House", "date": "2026-12-01"},
            {"kind": "routine", "title": "Oil change", "thing": "Outback", "date": "2027-01-15"},
        ],
        "counts": {"open": 2, "overdue": 1},
        "notifications": [],
        "links": [
            {"entity_id": "sensor.outback_odometer", "purpose": "meter", "value_type": "number", "unit": "mi"},
            {"entity_id": "binary_sensor.water_heater_leak", "purpose": "alert", "value_type": "binary", "unit": None},
            {"entity_id": "climate.hallway", "purpose": "runtime", "value_type": "number", "unit": "h"},
        ],
    }
    data.update(over)
    return data
