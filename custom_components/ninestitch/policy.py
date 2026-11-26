"""When a reading is worth sending, and run-hour counting. Plain Python, so it's easy to test.

Sending (design/home-assistant.md, "Noise"):
  * entities send on change, at most once a minute each; a change inside that minute is sent when
    the minute is up, so the last state is never lost (a leak sensor going wet then dry);
  * cumulative meters (odometer, run hours) send at most hourly, or sooner when the value moved by
    1 % (at least 1 unit) or went backwards (a reset Ninestitch should hear about);
  * `unknown` / `unavailable` are dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .const import (
    CLIMATE_RUNNING_ACTIONS,
    CUMULATIVE_MEANINGFUL_CHANGE,
    CUMULATIVE_SEND_INTERVAL,
    IGNORED_STATES,
    MIN_SEND_INTERVAL,
)


def _number(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class _Sent:
    at: float
    value: str


@dataclass
class SendPolicy:
    """Remembers what was sent for each entity, and says whether a new state should go now."""

    min_interval: float = MIN_SEND_INTERVAL
    cumulative_interval: float = CUMULATIVE_SEND_INTERVAL
    sent: dict[str, _Sent] = field(default_factory=dict)

    def decide(self, entity_id: str, state: str, now: float, cumulative: bool) -> float | None:
        """0 → send now; a number → wait that many seconds, then send the latest; None → don't send."""
        if state is None or str(state).strip().lower() in IGNORED_STATES:
            return None
        last = self.sent.get(entity_id)
        if last is None:
            return 0.0
        if last.value == state:
            return None
        elapsed = now - last.at
        if cumulative:
            if elapsed >= self.cumulative_interval:
                return 0.0
            new, old = _number(state), _number(last.value)
            if new is not None and old is not None:
                if new < old or abs(new - old) >= max(1.0, abs(old) * CUMULATIVE_MEANINGFUL_CHANGE):
                    return 0.0 if elapsed >= self.min_interval else self.min_interval - elapsed
            return self.cumulative_interval - elapsed
        if elapsed >= self.min_interval:
            return 0.0
        return self.min_interval - elapsed

    def mark_sent(self, entity_id: str, state: str, now: float) -> None:
        self.sent[entity_id] = _Sent(now, state)

    def forget(self, entity_id: str) -> None:
        self.sent.pop(entity_id, None)


def is_running(domain: str, state: str | None, attributes: dict[str, Any]) -> bool:
    """A thermostat runs while it heats or cools; anything else while it's on."""
    if state is None:
        return False
    if domain == "climate":
        return attributes.get("hvac_action") in CLIMATE_RUNNING_ACTIONS
    return str(state).lower() == "on"


def counts_run_hours(entity_id: str, purpose: str | None) -> bool:
    """Run-hour links on things that report on/off (a thermostat, a switch, a binary sensor) are
    counted here. A run-hours *sensor* (an Integral helper, a device's own counter) is sent as is."""
    return purpose == "runtime" and not entity_id.startswith("sensor.")


@dataclass
class RunHours:
    """Cumulative run hours per entity, from on/off transitions. Survives restarts through
    `as_dict` / `from_dict`; time while Home Assistant was down isn't counted."""

    hours: dict[str, float] = field(default_factory=dict)
    running_since: dict[str, float] = field(default_factory=dict)

    def update(self, entity_id: str, running: bool, now: float) -> None:
        self.hours.setdefault(entity_id, 0.0)
        since = self.running_since.get(entity_id)
        if since is not None:
            self.hours[entity_id] += max(0.0, now - since) / 3600.0
        if running:
            self.running_since[entity_id] = now
        else:
            self.running_since.pop(entity_id, None)

    def value(self, entity_id: str, now: float) -> float:
        total = self.hours.get(entity_id, 0.0)
        since = self.running_since.get(entity_id)
        if since is not None:
            total += max(0.0, now - since) / 3600.0
        return total

    def as_dict(self, now: float) -> dict[str, Any]:
        return {"hours": {e: self.value(e, now) for e in self.hours}}

    @classmethod
    def from_dict(cls, data: Any) -> RunHours:
        hours = {}
        if isinstance(data, dict) and isinstance(data.get("hours"), dict):
            hours = {str(k): float(v) for k, v in data["hours"].items() if isinstance(v, (int, float))}
        return cls(hours=hours)
