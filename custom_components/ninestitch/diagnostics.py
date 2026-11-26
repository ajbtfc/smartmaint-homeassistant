"""Diagnostics download: what the integration knows and is doing, without the token or job details."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import NinestitchConfigEntry
from .const import CONF_TOKEN

TO_REDACT = {CONF_TOKEN}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: NinestitchConfigEntry) -> dict[str, Any]:
    rt = entry.runtime_data
    feed = rt.coordinator.data or {}
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "feed": {
            "last_update_success": rt.coordinator.last_update_success,
            "out": feed.get("out"),
            "counts": feed.get("counts"),
            "jobs": len(feed.get("jobs") or []),
            "calendar_items": len(feed.get("calendar") or []),
            "people_matched": sum(1 for p in (feed.get("people") or {}).values() if (p or {}).get("person")),
            "links": feed.get("links"),
        },
        "queue": {
            "waiting": len(rt.queue),
            "next_attempt": rt.queue.next_attempt,
            "last_error": rt.queue.last_error,
            "last_result": rt.queue.last_result,
            "dropped": rt.queue.dropped,
            "paused_not_on_plus": rt.queue.plan_required,
            "disconnected": rt.queue.auth_failed,
        },
        "listening_to": sorted(rt.listener.links),
        "run_hours": rt.listener.run_hours.as_dict(time.time()),
    }
